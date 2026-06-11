import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from auto_campaigns import calculate_next_run
from conexion_bigquery import get_bigquery_client
from database import AutoCampaign, AutoCampaignExecutionLog, db
from api_handlers.Wolkvox_Carga_Clientes import (
    transform_datos_clientes,
    serialize_clientes_body,
)
from services.query_validator import validate_and_normalize

BASE_DIR = Path(__file__).resolve().parent
AUTO_UPLOAD_DIR = BASE_DIR / "uploads" / "auto_campaigns"
BATCH_SIZE = 10000
MAX_RECORDS_WOLKVOX = 50000

_running_lock = threading.Lock()
running_campaigns: dict[int, dict] = {}

# ===========================================================================
# UTILIDADES
# ===========================================================================

def _log(message: str, level: str = "INFO") -> None:
    """Registra un mensaje en el log de tareas."""
    try:
        from backend import log_task
        log_task(f"[AUTO-CAMPAIGN] {message}", level=level)
    except Exception:
        pass


def _safe_filename(text: str) -> str:
    """Genera un nombre de archivo seguro."""
    return "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text
    )[:80]


def _render_query(query: str) -> str:
    """Reemplaza placeholders de fecha en la consulta SQL."""
    today = datetime.now()
    params = {
        "fecha": today.strftime("%Y-%m-%d"),
        "fecha_hora": today.strftime("%Y-%m-%d %H:%M:%S"),
        "yyyymmdd": today.strftime("%Y%m%d"),
    }

    class _SafeParams(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return (query or "").format_map(_SafeParams(params))


def _normalize_bigquery_value(value):
    """Normaliza valores provenientes de BigQuery."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _response_summary(response: requests.Response) -> dict:
    """Resume una respuesta HTTP para logging."""
    try:
        body = response.json()
    except ValueError:
        body = response.text[:2000]
    return {
        "status": response.status_code,
        "ok": response.ok,
        "body": body,
    }


# ===========================================================================
# BIGQUERY
# ===========================================================================

def fetch_data_from_bigquery(query: str, params=None) -> list[dict]:
    """Ejecuta una consulta BigQuery y valida campos requeridos."""
    del params
    query_text = _render_query(query).strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", query_text, re.IGNORECASE):
        raise ValueError(
            "Solo se permiten consultas SELECT o WITH para campañas automáticas."
        )

    client = get_bigquery_client()
    if client is None:
        raise RuntimeError("Cliente BigQuery no disponible.")

    rows = []
    for row in client.query(query_text).result():
        rows.append(
            {key: _normalize_bigquery_value(value) for key, value in dict(row).items()}
        )

    success, normalized_rows, error_msg = validate_and_normalize(rows)
    if not success:
        raise ValueError(f"Validación de consulta fallida: {error_msg}")

    if len(normalized_rows) > MAX_RECORDS_WOLKVOX:
        _log(
            f"ADVERTENCIA: La consulta retorna {len(normalized_rows)} registros, "
            f"pero la licencia Wolkvox solo permite {MAX_RECORDS_WOLKVOX}. "
            f"Se limitará a los primeros {MAX_RECORDS_WOLKVOX} registros.",
            level="WARN"
        )
        normalized_rows = normalized_rows[:MAX_RECORDS_WOLKVOX]

    return normalized_rows


# ===========================================================================
# MAPEO Y CSV
# ===========================================================================

def map_rows_for_wolkvox(
    rows: list[dict], field_mapping: dict
) -> tuple[list[dict], list[str]]:
    """Mapea filas normalizadas a campos Wolkvox."""
    columns = list(field_mapping.keys())

    if rows and field_mapping:
        available_columns = set(rows[0].keys())
        expected_sources = {
            str(value).strip().lower()
            for value in field_mapping.values()
            if value
        }
        if expected_sources and not expected_sources.intersection(available_columns):
            _log(
                f"Advertencia: la consulta no devuelve columnas compatibles. "
                f"Columnas encontradas: {', '.join(sorted(available_columns))}. "
                f"Esperadas: {', '.join(sorted(expected_sources))}.",
                level="WARN",
            )

    mapped = []
    for row in rows:
        mapped_row = {}
        for target_field, source_field in field_mapping.items():
            mapped_row[target_field] = row.get(source_field, "")
        mapped.append(mapped_row)
    return mapped, columns


def generate_csv_from_data(
    data: list[dict],
    columns: list[str],
    *,
    campaign_id: int,
    batch_index: Optional[int] = None,
) -> str:
    """Genera un archivo CSV en el directorio de uploads."""
    AUTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_batch_{batch_index}" if batch_index is not None else ""
    filename = (
        f"auto_campaign_{campaign_id}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.csv"
    )
    path = AUTO_UPLOAD_DIR / _safe_filename(filename)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    return str(path)


# ===========================================================================
# WOLKVOX - URLs
# ===========================================================================

def _get_base_url_wolkvox(server_name: str) -> str:
    """Obtiene la URL base de Wolkvox para un servidor."""
    from backend import get_server

    try:
        srv = get_server(server_name)
    except Exception:
        srv = None

    if srv:
        prefix = (srv.get("url") or "").strip().rstrip("/")
        if prefix.lower().startswith("http"):
            return prefix
        return f"https://wv{prefix}.wolkvox.com"
    else:
        if server_name.lower().startswith("http"):
            return server_name.rstrip("/")
        return f"https://wv{server_name}.wolkvox.com"


def _resolve_wolkvox_url(endpoint: str, campaign_id: str, server_name: str = "") -> str:
    """Resuelve placeholders en la URL de Wolkvox."""
    url = (endpoint or "").strip()
    if "{{campaign_id}}" in url:
        url = url.replace("{{campaign_id}}", str(campaign_id))
    if "{{servidor}}" in url or "{{server}}" in url:
        from servers import get_server

        server_value = server_name or ""
        server = get_server(server_name) if server_name else None
        if server:
            server_value = (server.get("url") or "").rstrip("/")
            if server_value and not server_value.startswith(("http://", "https://")):
                server_value = f"https://wv{server_value}.wolkvox.com"
        url = url.replace("{{servidor}}", server_value).replace("{{server}}", server_value)
    return url


def _build_start_campaign_url(add_record_url: str, campaign_id: str) -> str:
    """Construye la URL para iniciar una campaña en Wolkvox."""
    parts = urlsplit(add_record_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["api"] = "start"
    query["campaign_id"] = str(campaign_id)
    for key in ("type_campaign", "campaign_status"):
        query.pop(key, None)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


# ===========================================================================
# WOLKVOX - LIMPIEZA
# ===========================================================================

def _clean_wolkvox_campaign(campaign: AutoCampaign, token: str) -> dict:
    """Limpia (borra) todos los registros existentes en una campaña de Wolkvox."""
    server_name = (campaign.server_name or "").strip()
    base_url = _get_base_url_wolkvox(server_name)
    campaign_id = str(campaign.wolkvox_campaign_id or "").strip()
    headers = {"wolkvox-token": token} if token else {}

    if not campaign_id:
        return {"success": False, "message": "No hay ID de campaña Wolkvox configurado"}

    _log(f"Iniciando limpieza de campaña {campaign_id}...")

    # Método 1: Endpoint configurado
    if campaign.wolkvox_delete_records_endpoint:
        delete_url = _resolve_wolkvox_url(
            campaign.wolkvox_delete_records_endpoint, campaign_id, server_name
        )
        _log(f"Método 1 - Endpoint configurado: {delete_url}")
        try:
            response = requests.post(delete_url, headers=headers, timeout=120)
            if response.ok:
                _log(f"Campaña {campaign_id} limpiada (método 1)")
                return {"success": True, "message": "Campaña limpiada con endpoint configurado"}
        except Exception as exc:
            _log(f"Error método 1: {exc}", level="WARN")

    # Método 2: Endpoint estándar v2
    delete_url = f"{base_url}/api/v2/campaigns.php?api=delete_records&campaign_id={campaign_id}"
    _log(f"Método 2 - Estándar v2: {delete_url}")
    try:
        response = requests.post(delete_url, headers=headers, timeout=120)
        if response.ok:
            _log(f"Campaña {campaign_id} limpiada (método 2)")
            return {"success": True, "message": "Campaña limpiada con endpoint estándar v2"}
    except Exception as exc:
        _log(f"Error método 2: {exc}", level="WARN")

    # Método 3: Endpoint v1
    delete_url = f"{base_url}/api/campaigns.php?api=delete_records&campaign_id={campaign_id}"
    _log(f"Método 3 - Estándar v1: {delete_url}")
    try:
        response = requests.post(delete_url, headers=headers, timeout=120)
        if response.ok:
            _log(f"Campaña {campaign_id} limpiada (método 3)")
            return {"success": True, "message": "Campaña limpiada con endpoint v1"}
    except Exception as exc:
        _log(f"Error método 3: {exc}", level="WARN")

    return {"success": False, "message": "No se pudo limpiar la campaña con ningún método"}


def _check_wolkvox_record_count(campaign: AutoCampaign, token: str) -> int:
    """Verifica cuántos registros hay en la campaña de Wolkvox."""
    from backend import get_authorization_headers

    server_name = (campaign.server_name or "").strip()
    base_url = _get_base_url_wolkvox(server_name)
    campaign_id = str(campaign.wolkvox_campaign_id or "").strip()

    if not campaign_id:
        return -1

    check_url = f"{base_url}/api/v2/real_time.php?api=campaigns"
    try:
        headers = get_authorization_headers(server_name) or {}
        resp = requests.get(check_url, headers=headers, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            campaigns = []
            if isinstance(data, dict):
                campaigns = data.get("data", data.get("campaigns", []))
            elif isinstance(data, list):
                campaigns = data

            for camp in campaigns:
                if isinstance(camp, dict):
                    camp_id = str(camp.get("campaign_id", camp.get("id", "")))
                    if camp_id == campaign_id:
                        records = int(camp.get("records", camp.get("total", 0)))
                        return records
            return 0
        return -1
    except Exception as exc:
        _log(f"Error verificando registros: {exc}", level="WARN")
        return -1


# ===========================================================================
# WOLKVOX - INICIAR CAMPAÑA
# ===========================================================================

def start_wolkvox_campaign(
    endpoint: str,
    token: str,
    campaign_id: str,
    *,
    server_name: str = "",
) -> dict:
    """Inicia una campaña en Wolkvox con reintentos."""
    add_record_url = _resolve_wolkvox_url(endpoint, campaign_id, server_name)
    url = _build_start_campaign_url(add_record_url, campaign_id)
    headers = {"wolkvox-token": token} if token else {}

    max_retries = 4
    backoff_factor = 1.2

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.put(url, headers=headers, timeout=60)
            summary = _response_summary(response)

            if response.ok:
                return {
                    "success": True,
                    "message": f"Wolkvox start HTTP {response.status_code}",
                    "url": url,
                    "response": summary,
                }

            if response.status_code in (409, 429) or (500 <= response.status_code < 600):
                _log(f"Start {response.status_code} (attempt {attempt}), retrying...", level="WARN")
                if attempt < max_retries:
                    time.sleep(backoff_factor * (2 ** (attempt - 1)))
                    continue

            return {
                "success": False,
                "message": f"Start HTTP {response.status_code}",
                "url": url,
                "response": summary,
            }

        except requests.Timeout:
            _log(f"Timeout start (attempt {attempt})", level="WARN")
            if attempt < max_retries:
                time.sleep(backoff_factor * (2 ** (attempt - 1)))
                continue
            return {"success": False, "message": "Timeout iniciando campaña.", "url": url}

        except Exception as exc:
            return {"success": False, "message": str(exc), "url": url}

    return {"success": False, "message": "Error desconocido iniciando campaña.", "url": url}


# ===========================================================================
# WOLKVOX - ENVIAR CSV
# ===========================================================================

def send_csv_to_wolkvox(
    csv_file_path: str,
    endpoint: str,
    token: str,
    campaign_id: str,
    *,
    server_name: str = "",
) -> dict:
    """Envía un archivo CSV a Wolkvox con reintentos."""
    url = _resolve_wolkvox_url(endpoint, campaign_id, server_name)
    if not url:
        return {"success": False, "records_sent": 0, "message": "Endpoint add_record vacío."}

    headers = (
        {"wolkvox-token": token, "Content-Type": "application/json"}
        if token
        else {"Content-Type": "application/json"}
    )

    try:
        csv_content = Path(csv_file_path).read_text(encoding="utf-8-sig")
        body = transform_datos_clientes(csv_content)
        if not body:
            return {"success": False, "records_sent": 0, "message": "CSV sin clientes.", "url": url}

        max_retries = 3
        backoff_factor = 1.5
        payload = serialize_clientes_body(body).encode("utf-8")

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, data=payload, timeout=120)
                summary = _response_summary(response)

                if response.ok:
                    records_sent = 0
                    try:
                        resp_body = response.json()
                        if isinstance(resp_body, dict):
                            records_sent = int(resp_body.get("records", 0) or resp_body.get("uploaded", 0) or 0)
                    except Exception:
                        pass
                    return {
                        "success": True,
                        "records_sent": records_sent,
                        "message": f"HTTP {response.status_code}",
                        "url": url,
                        "response": summary,
                    }

                if response.status_code in (409, 429) or (500 <= response.status_code < 600):
                    _log(f"Wolkvox {response.status_code} (attempt {attempt}), retrying...", level="WARN")
                    if attempt < max_retries:
                        time.sleep(backoff_factor * (2 ** (attempt - 1)))
                        continue

                if response.status_code == 404:
                    error_msg = str(summary.get("body", ""))
                    if "licenses" in error_msg.lower() or "50000" in error_msg:
                        return {
                            "success": False,
                            "records_sent": 0,
                            "message": f"Límite de licencias excedido: {error_msg[:200]}",
                            "url": url,
                            "response": summary,
                        }

                return {
                    "success": False,
                    "records_sent": 0,
                    "message": f"HTTP {response.status_code}",
                    "url": url,
                    "response": summary,
                }

            except requests.Timeout:
                if attempt < max_retries:
                    time.sleep(backoff_factor * (2 ** (attempt - 1)))
                    continue
                return {"success": False, "records_sent": 0, "message": "Timeout.", "url": url}

            except Exception as exc:
                return {"success": False, "records_sent": 0, "message": str(exc), "url": url}

        return {"success": False, "records_sent": 0, "message": "Error desconocido.", "url": url}

    except Exception as exc:
        return {"success": False, "records_sent": 0, "message": str(exc), "url": url}


# ===========================================================================
# CONTROL DE EJECUCIÓN
# ===========================================================================

def request_stop_auto_campaign(campaign_id: int) -> bool:
    """Solicita detener una campaña en ejecución."""
    with _running_lock:
        state = running_campaigns.get(campaign_id)
        if not state:
            return False
        state["stop_requested"] = True
        return True


def is_auto_campaign_running(campaign_id: int) -> bool:
    """Verifica si una campaña está en ejecución."""
    with _running_lock:
        return campaign_id in running_campaigns


# ===========================================================================
# TOKEN Y REPORTES
# ===========================================================================

def _get_token(campaign: AutoCampaign) -> str:
    """Obtiene el token Wolkvox para una campaña."""
    from backend import get_authorization_headers, load_config

    load_config()
    headers = get_authorization_headers(campaign.server_name or None)
    return headers.get("wolkvox-token") or ""


def _write_report_json(log: AutoCampaignExecutionLog) -> str:
    """Escribe un archivo JSON con el resumen de la ejecución."""
    AUTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = AUTO_UPLOAD_DIR / f"auto_campaign_report_{log.auto_campaign_id}_{log.id}.json"
    payload = {
        "execution_id": log.id,
        "auto_campaign_id": log.auto_campaign_id,
        "start_time": log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else "",
        "end_time": log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else "",
        "records_fetched": log.records_fetched,
        "records_sent": log.records_sent,
        "records_failed": log.records_failed,
        "error_message": log.error_message or "",
        "csv_file_path": log.csv_file_path or "",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ===========================================================================
# EJECUCIÓN PRINCIPAL
# ===========================================================================

def run_auto_campaign(campaign_id: int) -> dict:
    """
    Ejecuta una campaña automática completa:
    1. Limpia registros anteriores en Wolkvox
    2. Consulta BigQuery
    3. Envía datos a Wolkvox en lotes
    4. Inicia la campaña en Wolkvox
    5. Procesa CDR a BigQuery (Temporal.Robots_Temporal)
    """
    with _running_lock:
        if campaign_id in running_campaigns:
            return {"success": False, "message": "La campaña automática ya está en ejecución."}
        running_campaigns[campaign_id] = {
            "stop_requested": False,
            "started_at": datetime.now(),
        }

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        with _running_lock:
            running_campaigns.pop(campaign_id, None)
        return {"success": False, "message": "No se encontró la campaña automática."}

    log = AutoCampaignExecutionLog(
        auto_campaign_id=campaign.id,
        start_time=datetime.utcnow(),
    )
    db.session.add(log)
    campaign.running = True
    db.session.commit()

    records_sent = 0
    records_failed = 0
    full_csv_path = ""
    error_message = ""
    bigquery_result = None

    try:
        _log("=" * 60)
        _log(f"INICIANDO CAMPAÑA: {campaign.name} (ID={campaign.id})")
        _log(f"  Servidor: {campaign.server_name}")
        _log(f"  Campaña Wolkvox: {campaign.wolkvox_campaign_id}")
        _log("=" * 60)

        # 1. Token
        _log("Paso 1/6: Obteniendo token...")
        token = _get_token(campaign)
        if not token:
            raise RuntimeError("No se encontró token Wolkvox.")
        _log("✅ Token obtenido")

        # 2. Limpiar campaña
        _log("Paso 2/6: Limpiando campaña...")
        clean_result = _clean_wolkvox_campaign(campaign, token)
        if clean_result.get("success"):
            _log("✅ Campaña limpiada")
            time.sleep(3)
        else:
            _log(f"⚠️ No se pudo limpiar: {clean_result.get('message')}", level="WARN")
            current = _check_wolkvox_record_count(campaign, token)
            if current > 0:
                available = MAX_RECORDS_WOLKVOX - current
                if available <= 0:
                    raise RuntimeError(f"Campaña llena ({current}/{MAX_RECORDS_WOLKVOX})")

        # 3. BigQuery
        _log("Paso 3/6: Consultando BigQuery...")
        rows = fetch_data_from_bigquery(campaign.bigquery_query)
        mapped_rows, columns = map_rows_for_wolkvox(rows, campaign.field_mapping or {})
        log.records_fetched = len(mapped_rows)
        _log(f"✅ Registros: {log.records_fetched}")

        if not mapped_rows:
            _log("⚠️ Sin registros", level="WARN")
            error_message = "La consulta no retornó registros"
        else:
            full_csv_path = generate_csv_from_data(mapped_rows, columns, campaign_id=campaign.id)
            log.csv_file_path = full_csv_path
            db.session.commit()

            # 4. Enviar lotes
            _log(f"Paso 4/6: Enviando {len(mapped_rows)} registros en lotes de {BATCH_SIZE}...")
            total_batches = (len(mapped_rows) + BATCH_SIZE - 1) // BATCH_SIZE

            for offset in range(0, len(mapped_rows), BATCH_SIZE):
                with _running_lock:
                    if running_campaigns.get(campaign.id, {}).get("stop_requested"):
                        error_message = "Detenida por el usuario."
                        break

                batch_num = (offset // BATCH_SIZE) + 1
                batch = mapped_rows[offset : offset + BATCH_SIZE]

                batch_path = generate_csv_from_data(
                    batch, columns, campaign_id=campaign.id, batch_index=batch_num
                )

                result = send_csv_to_wolkvox(
                    batch_path,
                    campaign.wolkvox_add_record_endpoint,
                    token,
                    campaign.wolkvox_campaign_id,
                    server_name=campaign.server_name or "",
                )

                if result.get("success"):
                    records_sent += result.get("records_sent", len(batch))
                    _log(f"  ✅ Lote {batch_num}/{total_batches} enviado")
                else:
                    records_failed += len(batch)
                    error_message = result.get("message", "Error")
                    _log(f"  ❌ Lote {batch_num} fallido: {error_message}", level="ERROR")
                    if "licenses" in error_message.lower():
                        break

            # 5. Iniciar campaña
            if records_sent > 0:
                _log("Paso 5/6: Iniciando campaña en Wolkvox...")
                start_result = start_wolkvox_campaign(
                    campaign.wolkvox_add_record_endpoint,
                    token,
                    campaign.wolkvox_campaign_id,
                    server_name=campaign.server_name or "",
                )
                if start_result.get("success"):
                    _log("✅ Campaña iniciada")
                else:
                    _log(f"⚠️ No se pudo iniciar: {start_result.get('message')}", level="WARN")

        # Actualizar log
        log.records_sent = records_sent
        log.records_failed = records_failed
        log.error_message = error_message or None
        log.end_time = datetime.utcnow()

        # Actualizar campaña
        campaign.running = False
        campaign.last_run = datetime.utcnow()

        if campaign.schedule_type == "one_time":
            campaign.next_run = None
        elif campaign.schedule_type == "recurring":
            campaign.next_run = calculate_next_run(
                campaign.schedule_type,
                campaign.schedule_value,
                from_time=datetime.now(),
            )
        else:
            campaign.next_run = None

        db.session.commit()

        # Guardar JSON de respaldo
        json_path = _write_report_json(log)
        db.session.commit()
        _log(f"✅ JSON guardado: {json_path}")

        # 6. Procesar CDR a BigQuery
        _log("Paso 6/6: Procesando CDR a BigQuery...")
        try:
            from bigquery_cdr_processor import process_cdr_to_bigquery

            bigquery_result = process_cdr_to_bigquery(campaign, log)

            if bigquery_result.get("success"):
                log.report_file_path = "bigquery://Temporal.Robots_Temporal"
                db.session.commit()
                _log(f"✅ CDR en BigQuery: {bigquery_result.get('rows_uploaded')} registros")
            else:
                log.report_file_path = json_path
                db.session.commit()
                _log(f"⚠️ CDR no cargado: {bigquery_result.get('message')}", level="WARN")
        except ImportError:
            log.report_file_path = json_path
            db.session.commit()
            _log("⚠️ Módulo bigquery_cdr_processor no encontrado", level="WARN")
        except Exception as exc:
            log.report_file_path = json_path
            db.session.commit()
            _log(f"⚠️ Error CDR: {exc}", level="WARN")

        # Resumen
        _log("=" * 60)
        _log("🏁 CAMPAÑA FINALIZADA")
        _log(f"   Obtenidos: {log.records_fetched}")
        _log(f"   Enviados:  {records_sent}")
        _log(f"   Fallidos:  {records_failed}")
        if bigquery_result:
            _log(f"   BigQuery:  {bigquery_result.get('rows_uploaded', 0)} registros")
        _log("=" * 60)

        return {
            "success": records_failed == 0 and not error_message,
            "message": error_message or "Campaña ejecutada exitosamente.",
            "execution_id": log.id,
            "records_fetched": log.records_fetched,
            "records_sent": records_sent,
            "records_failed": records_failed,
            "csv_file_path": full_csv_path,
            "bigquery_result": bigquery_result,
        }

    except Exception as exc:
        db.session.rollback()
        error_message = str(exc)
        _log(f"❌ ERROR FATAL: {error_message}", level="ERROR")
        import traceback
        _log(traceback.format_exc(), level="ERROR")

        campaign = AutoCampaign.query.get(campaign_id)
        if campaign:
            campaign.running = False
            campaign.last_run = datetime.utcnow()

        log = AutoCampaignExecutionLog.query.get(log.id)
        if log:
            log.end_time = datetime.utcnow()
            log.records_sent = records_sent
            log.records_failed = records_failed or max(0, log.records_fetched - records_sent)
            log.error_message = error_message

        db.session.commit()

        return {
            "success": False,
            "message": error_message,
            "execution_id": log.id if log else None,
        }

    finally:
        with _running_lock:
            running_campaigns.pop(campaign_id, None)

        campaign = AutoCampaign.query.get(campaign_id)
        if campaign and campaign.running:
            campaign.running = False
            db.session.commit()


# ===========================================================================
# INICIO ASÍNCRONO
# ===========================================================================

def start_auto_campaign_async(campaign_id: int, app) -> bool:
    """Inicia una campaña automática en un hilo de fondo."""
    with _running_lock:
        if campaign_id in running_campaigns:
            return False

    def _worker():
        with app.app_context():
            run_auto_campaign(campaign_id)

    threading.Thread(
        target=_worker,
        name=f"auto-campaign-{campaign_id}",
        daemon=True,
    ).start()

    _log(f"Campaña id={campaign_id} lanzada en background")
    return True