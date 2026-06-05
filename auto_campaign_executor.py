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
BATCH_SIZE = 10000  # Tamaño de lote para envío a Wolkvox
MAX_RECORDS_WOLKVOX = 50000  # Límite de licencias Wolkvox

_running_lock = threading.Lock()
running_campaigns: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _log(message: str, level: str = "INFO") -> None:
    """Registra un mensaje en el log de tareas."""
    try:
        from backend import log_task
        log_task(f"[AUTO-CAMPAIGN] {message}", level=level)
    except Exception:
        pass


def _safe_filename(text: str) -> str:
    """Genera un nombre de archivo seguro eliminando caracteres no alfanuméricos."""
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


# ---------------------------------------------------------------------------
# Obtención de datos desde BigQuery
# ---------------------------------------------------------------------------

def fetch_data_from_bigquery(query: str, params=None) -> list[dict]:
    """
    Ejecuta una consulta BigQuery y valida que incluya campos requeridos.
    """
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

    # Validar y normalizar campos
    success, normalized_rows, error_msg = validate_and_normalize(rows)
    if not success:
        raise ValueError(f"Validación de consulta fallida: {error_msg}")

    # Verificar límite de registros vs licencias Wolkvox
    if len(normalized_rows) > MAX_RECORDS_WOLKVOX:
        _log(
            f"ADVERTENCIA: La consulta retorna {len(normalized_rows)} registros, "
            f"pero la licencia Wolkvox solo permite {MAX_RECORDS_WOLKVOX}. "
            f"Se limitará a los primeros {MAX_RECORDS_WOLKVOX} registros.",
            level="WARN"
        )
        normalized_rows = normalized_rows[:MAX_RECORDS_WOLKVOX]

    return normalized_rows


# ---------------------------------------------------------------------------
# Mapeo y generación de CSV
# ---------------------------------------------------------------------------

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
                f"Advertencia: la consulta no devuelve columnas compatibles con el mapeo de campaña. "
                f"Columnas encontradas: {', '.join(sorted(available_columns))}. "
                f"Al menos una de estas columnas era esperada: {', '.join(sorted(expected_sources))}.",
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


# ---------------------------------------------------------------------------
# URLs y comunicación con Wolkvox
# ---------------------------------------------------------------------------

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


def start_wolkvox_campaign(
    endpoint: str,
    token: str,
    campaign_id: str,
    *,
    server_name: str = "",
) -> dict:
    """Inicia una campaña en Wolkvox con reintentos en caso de error."""
    add_record_url = _resolve_wolkvox_url(endpoint, campaign_id, server_name)
    url = _build_start_campaign_url(add_record_url, campaign_id)
    headers = {"wolkvox-token": token} if token else {}

    max_retries = 4
    backoff_factor = 1.2
    last_summary = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.put(url, headers=headers, timeout=60)
            summary = _response_summary(response)
            last_summary = summary

            if response.ok:
                return {
                    "success": True,
                    "message": f"Wolkvox start HTTP {response.status_code}",
                    "url": url,
                    "response": summary,
                }

            if response.status_code in (409, 429) or (500 <= response.status_code < 600):
                _log(
                    f"Wolkvox start returned {response.status_code} on attempt {attempt}. Will retry.",
                    level="WARN",
                )
                if attempt < max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue
                else:
                    return {
                        "success": False,
                        "message": f"Wolkvox start HTTP {response.status_code} after {attempt} attempts.",
                        "url": url,
                        "response": summary,
                    }

            return {
                "success": False,
                "message": f"Wolkvox start HTTP {response.status_code}",
                "url": url,
                "response": summary,
            }

        except requests.Timeout:
            _log(
                f"Timeout iniciando campaña en Wolkvox on attempt {attempt}.",
                level="WARN",
            )
            if attempt < max_retries:
                delay = backoff_factor * (2 ** (attempt - 1))
                time.sleep(delay)
                continue
            return {
                "success": False,
                "message": "Timeout iniciando campaña en Wolkvox.",
                "url": url,
            }

        except Exception as exc:
            _log(
                f"Error iniciando campaña Wolkvox on attempt {attempt}: {exc}",
                level="ERROR",
            )
            return {"success": False, "message": str(exc), "url": url}

    return {
        "success": False,
        "message": "Error desconocido iniciando campaña Wolkvox.",
        "url": url,
        "response": last_summary,
    }


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
        return {
            "success": False,
            "records_sent": 0,
            "message": "Endpoint add_record vacío.",
        }

    headers = (
        {"wolkvox-token": token, "Content-Type": "application/json"}
        if token
        else {"Content-Type": "application/json"}
    )

    try:
        csv_content = Path(csv_file_path).read_text(encoding="utf-8-sig")
        body = transform_datos_clientes(csv_content)
        if not body:
            return {
                "success": False,
                "records_sent": 0,
                "message": "El CSV no contiene clientes para enviar.",
                "url": url,
            }

        max_retries = 5
        backoff_factor = 1.5
        last_summary = None
        payload = serialize_clientes_body(body).encode("utf-8")

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    url, headers=headers, data=payload, timeout=120
                )
                summary = _response_summary(response)
                last_summary = summary

                if response.ok:
                    # Contar registros enviados desde el cuerpo de la respuesta
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
                        "message": f"Wolkvox HTTP {response.status_code}",
                        "url": url,
                        "response": summary,
                    }

                if response.status_code in (409, 429) or (
                    500 <= response.status_code < 600
                ):
                    _log(
                        f"Wolkvox returned {response.status_code} on attempt {attempt}. Will retry.",
                        level="WARN",
                    )
                    if attempt < max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    else:
                        return {
                            "success": False,
                            "records_sent": 0,
                            "message": f"Wolkvox HTTP {response.status_code} after {attempt} attempts.",
                            "url": url,
                            "response": summary,
                        }

                # Error 404 con mensaje de límite de licencias
                if response.status_code == 404:
                    error_msg = str(summary.get("body", ""))
                    if "licenses" in error_msg.lower() or "50000" in error_msg:
                        _log(
                            f"Error de límite de licencias Wolkvox: {error_msg}",
                            level="ERROR"
                        )
                        return {
                            "success": False,
                            "records_sent": 0,
                            "message": f"Límite de licencias Wolkvox excedido: {error_msg[:200]}",
                            "url": url,
                            "response": summary,
                        }

                return {
                    "success": False,
                    "records_sent": 0,
                    "message": f"Wolkvox HTTP {response.status_code}",
                    "url": url,
                    "response": summary,
                }

            except requests.Timeout:
                _log(
                    f"Timeout cargando CSV a Wolkvox on attempt {attempt}.",
                    level="WARN",
                )
                if attempt < max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue
                return {
                    "success": False,
                    "records_sent": 0,
                    "message": "Timeout cargando CSV a Wolkvox.",
                    "url": url,
                }

            except Exception as exc:
                _log(
                    f"Error enviando a Wolkvox on attempt {attempt}: {exc}",
                    level="ERROR",
                )
                return {
                    "success": False,
                    "records_sent": 0,
                    "message": str(exc),
                    "url": url,
                }

        return {
            "success": False,
            "records_sent": 0,
            "message": "Error desconocido enviando a Wolkvox.",
            "url": url,
            "response": last_summary,
        }

    except requests.Timeout:
        return {
            "success": False,
            "records_sent": 0,
            "message": "Timeout cargando CSV a Wolkvox.",
            "url": url,
        }
    except Exception as exc:
        return {
            "success": False,
            "records_sent": 0,
            "message": str(exc),
            "url": url,
        }


# ---------------------------------------------------------------------------
# Control de ejecución
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Token y reportes
# ---------------------------------------------------------------------------

def _get_token(campaign: AutoCampaign) -> str:
    """Obtiene el token Wolkvox para una campaña."""
    from backend import get_authorization_headers, load_config

    load_config()
    headers = get_authorization_headers(campaign.server_name or None)
    return headers.get("wolkvox-token") or ""


def _write_report_file(log: AutoCampaignExecutionLog) -> str:
    """Escribe un archivo JSON con el resumen de la ejecución."""
    AUTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = (
        AUTO_UPLOAD_DIR / f"auto_campaign_report_{log.auto_campaign_id}_{log.id}.json"
    )
    payload = {
        "execution_id": log.id,
        "auto_campaign_id": log.auto_campaign_id,
        "start_time": (
            log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else ""
        ),
        "end_time": (
            log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else ""
        ),
        "records_fetched": log.records_fetched,
        "records_sent": log.records_sent,
        "records_failed": log.records_failed,
        "error_message": log.error_message or "",
        "csv_file_path": log.csv_file_path or "",
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(path)


# ---------------------------------------------------------------------------
# Generación de XLSX al finalizar
# ---------------------------------------------------------------------------

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

def _generate_xlsx_report(
    campaign: AutoCampaign,
    log: AutoCampaignExecutionLog,
) -> Optional[str]:
    """Genera un archivo XLSX con el reporte de la campaña desde la API de Wolkvox."""
    
    _log("=" * 60)
    _log("INICIANDO GENERACIÓN DE XLSX")
    
    try:
        # Verificar importaciones
        _log("Paso 1: Verificando importaciones...")
        try:
            from excel_report_builder import build_wolkvox_excel
            _log("  ✅ excel_report_builder importado")
        except ImportError as e:
            _log(f"  ❌ Error importando excel_report_builder: {e}", level="ERROR")
            return None
            
        try:
            from backend import get_authorization_headers, get_server
            _log("  ✅ backend importado")
        except ImportError as e:
            _log(f"  ❌ Error importando backend: {e}", level="ERROR")
            return None

        # Datos de la campaña
        server_name = (campaign.server_name or "").strip()
        campaign_name = (campaign.name or "").strip() or f"campaign_{campaign.id}"
        end_dt = log.end_time or datetime.utcnow()
        
        _log(f"Paso 2: Datos - server={server_name}, campaign={campaign_name}, fecha={end_dt}")

        # Construir fechas
        date_ini = end_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
        date_end = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d")
        
        _log(f"Paso 3: Fechas - ini={date_ini}, end={date_end}")

        def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
            if not s:
                return ""
            try:
                if "T" in s or len(s) > 10:
                    dt = datetime.fromisoformat(s)
                else:
                    from datetime import date
                    d = date.fromisoformat(s)
                    dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
                return dt.strftime("%Y%m%d%H%M%S")
            except Exception:
                return s

        date_ini_ts = _to_wolkvox_ts(date_ini, is_end=False)
        date_end_ts = _to_wolkvox_ts(date_end, is_end=True)
        
        _log(f"Paso 4: Timestamps - ini={date_ini_ts}, end={date_end_ts}")

        # Obtener servidor
        _log("Paso 5: Obteniendo configuración del servidor...")
        try:
            srv = get_server(server_name)
            _log(f"  Servidor encontrado: {srv}")
        except Exception as e:
            _log(f"  Servidor no encontrado: {e}")
            srv = None

        # Construir URL
        if srv:
            prefix = (srv.get("url") or "").strip().rstrip("/")
            base_url = prefix if prefix.lower().startswith("http") else f"https://wv{prefix}.wolkvox.com"
        else:
            if server_name.lower().startswith("http"):
                base_url = server_name.rstrip("/")
            else:
                base_url = f"https://wv{server_name}.wolkvox.com"
            base_url = base_url.rstrip("/")
            
        url = f"{base_url}/api/v2/reports_manager.php?api=cdr_1&date_ini={date_ini_ts}&date_end={date_end_ts}"
        _log(f"Paso 6: URL construida: {url}")

        # Obtener headers
        headers = get_authorization_headers(server_name) or {}
        _log(f"Paso 7: Headers obtenidos (token presente: {'wolkvox-token' in headers})")

        # Hacer request
        _log("Paso 8: Haciendo request a Wolkvox...")
        resp = requests.get(url, headers=headers, timeout=60)
        _log(f"  Response status: {resp.status_code}")
        _log(f"  Response length: {len(resp.text)} caracteres")
        _log(f"  Response preview: {resp.text[:300]}...")
        
        resp.raise_for_status()

        # Parsear JSON
        _log("Paso 9: Parseando JSON...")
        data_json = resp.json()
        _log(f"  Tipo de datos: {type(data_json)}")
        
        # Extraer filas
        rows = []
        if isinstance(data_json, list):
            rows = data_json
            _log(f"  Datos como lista: {len(rows)} elementos")
        elif isinstance(data_json, dict):
            _log(f"  Keys disponibles: {list(data_json.keys())}")
            if "data" in data_json and isinstance(data_json["data"], list):
                rows = data_json["data"]
                _log(f"  Datos desde 'data': {len(rows)} elementos")
            elif "files" in data_json and isinstance(data_json["files"], list):
                rows = data_json["files"]
                _log(f"  Datos desde 'files': {len(rows)} elementos")
            else:
                rows = [data_json]
                _log("  Envolviendo diccionario como único registro")
        else:
            rows = [{"raw": resp.text}]
            _log("  Guardando respuesta como texto")
        
        _log(f"Paso 10: Total registros extraídos: {len(rows)}")
        
        if rows and len(rows) > 0:
            _log(f"  Primer registro preview: {dict(list(rows[0].items())[:5]) if isinstance(rows[0], dict) else rows[0]}")

        # Generar XLSX
        _log("Paso 11: Generando archivo XLSX...")
        safe_server = _safe_filename(server_name)
        safe_campaign = _safe_filename(campaign_name)
        end_file_ts = end_dt.strftime("%Y%m%d_%H%M%S")
        filename = f"auto_campaign_report_{safe_server}_{safe_campaign}_{end_file_ts}.xlsx"
        
        _log(f"  Nombre archivo: {filename}")

        output_dir = AUTO_UPLOAD_DIR
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)
        
        _log(f"  Ruta salida: {output_path}")

        bio, _ = build_wolkvox_excel(rows=rows, filename=filename)
        with open(output_path, "wb") as f:
            f.write(bio.getvalue())

        _log(f"Paso 12: ✅ XLSX generado exitosamente: {output_path}")
        _log("=" * 60)
        return output_path

    except Exception as exc:
        _log(f"❌ ERROR en paso de generación XLSX: {type(exc).__name__}: {exc}", level="ERROR")
        _log(f"  Detalle completo: {repr(exc)}")
        
        import traceback
        _log(f"  Traceback: {traceback.format_exc()}", level="ERROR")
        _log("=" * 60)
        return None


# ---------------------------------------------------------------------------
# Verificación de límite de registros en Wolkvox
# ---------------------------------------------------------------------------

def _check_wolkvox_record_limit(campaign: AutoCampaign, token: str) -> int:
    """
    Verifica cuántos registros hay actualmente en la campaña de Wolkvox.
    
    Retorna:
        Número de registros actuales, o -1 si no se pudo determinar
    """
    from backend import get_authorization_headers
    
    server_name = (campaign.server_name or "").strip()
    base_url = _get_base_url_wolkvox(server_name)
    
    # Endpoint para consultar estado de la campaña
    check_url = f"{base_url}/api/v2/real_time.php?api=campaigns"
    
    try:
        headers = get_authorization_headers(server_name) or {}
        resp = requests.get(check_url, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            # Buscar la campaña específica en la respuesta
            campaign_id = str(campaign.wolkvox_campaign_id)
            
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
            
            _log(f"No se encontró la campaña {campaign_id} en la respuesta", level="WARN")
            return -1
        else:
            _log(f"Error consultando estado de campaña: {resp.status_code}", level="WARN")
            return -1
            
    except Exception as exc:
        _log(f"Error verificando límite de registros: {exc}", level="WARN")
        return -1


# ---------------------------------------------------------------------------
# Ejecución principal de la campaña automática
# ---------------------------------------------------------------------------

def run_auto_campaign(campaign_id: int) -> dict:
    """
    Ejecuta una campaña automática completa:

    1. Verifica límite de registros en Wolkvox
    2. Consulta BigQuery
    3. Mapea y envía datos a Wolkvox en lotes
    4. Inicia la campaña en Wolkvox
    5. Genera reporte XLSX al finalizar
    """
    with _running_lock:
        if campaign_id in running_campaigns:
            return {
                "success": False,
                "message": "La campaña automática ya está en ejecución.",
            }
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
    xlsx_path = None

    try:
        _log(f"Iniciando campaña automática id={campaign.id} nombre={campaign.name}")

        # 0. Obtener token y verificar límite de registros
        token = _get_token(campaign)
        if not token:
            raise RuntimeError("No se encontró token Wolkvox en config.json/servidor.")

        current_records = _check_wolkvox_record_limit(campaign, token)
        if current_records > 0:
            _log(f"Campaña Wolkvox tiene actualmente {current_records} registros")
            available_slots = MAX_RECORDS_WOLKVOX - current_records
            if available_slots <= 0:
                raise RuntimeError(
                    f"La campaña ya tiene {current_records} registros. "
                    f"Límite máximo: {MAX_RECORDS_WOLKVOX}. "
                    f"Debe limpiar la campaña antes de cargar nuevos registros."
                )
            _log(f"Espacios disponibles para nuevos registros: {available_slots}")

        # 1. Obtener datos de BigQuery
        rows = fetch_data_from_bigquery(campaign.bigquery_query)
        mapped_rows, columns = map_rows_for_wolkvox(rows, campaign.field_mapping or {})
        log.records_fetched = len(mapped_rows)
        _log(f"Registros obtenidos de BigQuery: {log.records_fetched}")

        if not mapped_rows:
            _log("No se obtuvieron registros de BigQuery", level="WARN")
            error_message = "La consulta no retornó registros"
        else:
            full_csv_path = generate_csv_from_data(
                mapped_rows, columns, campaign_id=campaign.id
            )
            log.csv_file_path = full_csv_path
            db.session.commit()
            _log(f"CSV generado: {full_csv_path}")

            # 2. Enviar datos en lotes
            for offset in range(0, len(mapped_rows), BATCH_SIZE):
                # Verificar si se solicitó detener
                with _running_lock:
                    if running_campaigns.get(campaign.id, {}).get("stop_requested"):
                        error_message = "Ejecución detenida por el usuario."
                        _log(f"Campaña id={campaign.id}: {error_message}", level="WARN")
                        break

                batch = mapped_rows[offset : offset + BATCH_SIZE]
                batch_path = generate_csv_from_data(
                    batch,
                    columns,
                    campaign_id=campaign.id,
                    batch_index=(offset // BATCH_SIZE) + 1,
                )

                result = send_csv_to_wolkvox(
                    batch_path,
                    campaign.wolkvox_add_record_endpoint,
                    token,
                    campaign.wolkvox_campaign_id,
                    server_name=campaign.server_name or "",
                )

                if result.get("success"):
                    batch_sent = result.get("records_sent", len(batch))
                    records_sent += batch_sent
                    _log(
                        f"Lote {offset // BATCH_SIZE + 1} enviado: {batch_sent} registros"
                    )
                else:
                    records_failed += len(batch)
                    response_body = (
                        (result.get("response") or {}).get("body")
                        if isinstance(result.get("response"), dict)
                        else ""
                    )
                    error_message = result.get("message") or "Error enviando lote a Wolkvox."
                    if response_body:
                        error_message = f"{error_message}: {str(response_body)[:500]}"
                    _log(
                        f"Lote {offset // BATCH_SIZE + 1} fallido: {error_message}",
                        level="ERROR",
                    )
                    
                    # Si es error de límite, no continuar con más lotes
                    if "licenses" in error_message.lower() or "50000" in error_message:
                        _log("Deteniendo envío por límite de licencias", level="ERROR")
                        break

            # 3. Iniciar campaña en Wolkvox solo si se enviaron registros exitosamente
            if records_sent > 0 and records_failed == 0:
                _log("Iniciando campaña en Wolkvox...")
                start_result = start_wolkvox_campaign(
                    campaign.wolkvox_add_record_endpoint,
                    token,
                    campaign.wolkvox_campaign_id,
                    server_name=campaign.server_name or "",
                )

                if start_result.get("success"):
                    _log("Campaña iniciada exitosamente en Wolkvox")
                else:
                    start_error = (
                        start_result.get("message")
                        or "No se pudo iniciar la campaña en Wolkvox."
                    )
                    _log(f"Inicio fallido: {start_error}", level="ERROR")
                    if not error_message:
                        error_message = start_error

        # 4. Actualizar log de ejecución
        log.records_sent = records_sent
        log.records_failed = records_failed
        log.error_message = error_message or None
        log.end_time = datetime.utcnow()

        # 5. Actualizar campaña
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

        # 6. Guardar reporte JSON
        json_path = _write_report_file(log)
        db.session.commit()
        _log(f"Reporte JSON guardado: {json_path}")

        # 7. Generar XLSX
        _log("Generando reporte XLSX...")
        xlsx_path = _generate_xlsx_report(campaign, log)

        if xlsx_path:
            log.report_file_path = xlsx_path
            db.session.commit()
            _log(
                f"Campaña id={campaign.id} finalizada exitosamente. "
                f"XLSX: {xlsx_path} "
                f"(fetched={log.records_fetched}, sent={records_sent}, failed={records_failed})"
            )
        else:
            # Si no se pudo generar XLSX, usar el JSON
            log.report_file_path = json_path
            db.session.commit()
            _log(
                f"Campaña id={campaign.id} finalizada. "
                f"XLSX no disponible, se guardó JSON. "
                f"(fetched={log.records_fetched}, sent={records_sent}, failed={records_failed})",
                level="WARN",
            )

        return {
            "success": records_failed == 0 and not error_message,
            "message": error_message or "Campaña automática ejecutada exitosamente.",
            "execution_id": log.id,
            "records_fetched": log.records_fetched,
            "records_sent": records_sent,
            "records_failed": records_failed,
            "csv_file_path": full_csv_path,
            "xlsx_path": xlsx_path,
        }

    except Exception as exc:
        db.session.rollback()
        error_message = str(exc)
        _log(f"Error en campaña id={campaign_id}: {error_message}", level="ERROR")

        # Intentar recuperar estado
        campaign = AutoCampaign.query.get(campaign_id)
        if campaign:
            campaign.running = False
            campaign.last_run = datetime.utcnow()

        log = AutoCampaignExecutionLog.query.get(log.id)
        if log:
            log.end_time = datetime.utcnow()
            log.records_sent = records_sent
            log.records_failed = records_failed or max(
                0, log.records_fetched - records_sent
            )
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

        # Asegurar que la campaña no quede en estado "running"
        campaign = AutoCampaign.query.get(campaign_id)
        if campaign and campaign.running:
            campaign.running = False
            db.session.commit()


# ---------------------------------------------------------------------------
# Inicio asíncrono
# ---------------------------------------------------------------------------

def start_auto_campaign_async(campaign_id: int, app) -> bool:
    """
    Inicia una campaña automática en un hilo de fondo.
    """
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

    _log(f"Campaña id={campaign_id} iniciada en hilo de fondo")
    return True