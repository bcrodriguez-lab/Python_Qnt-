import csv
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import requests
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from auto_campaigns import calculate_next_run
from conexion_bigquery import get_bigquery_client
from database import AutoCampaign, AutoCampaignExecutionLog, db
from api_handlers.Wolkvox_Carga_Clientes import transform_datos_clientes, serialize_clientes_body
from services.query_validator import validate_and_normalize

BASE_DIR = Path(__file__).resolve().parent
AUTO_UPLOAD_DIR = BASE_DIR / "uploads" / "auto_campaigns"
BATCH_SIZE = 10000

_running_lock = threading.Lock()
running_campaigns: dict[int, dict] = {}


def _log(message: str, level: str = "INFO") -> None:
    try:
        from backend import log_task

        log_task(f"[AUTO-CAMPAIGN] {message}", level=level)
    except Exception:
        pass


def _safe_filename(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text)[:80]


def _render_query(query: str) -> str:
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
    if value is None:
        return ""
    if isinstance(value, (datetime,)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def fetch_data_from_bigquery(query: str, params=None) -> list[dict]:
    """
    Ejecuta una consulta BigQuery y valida que incluya campos requeridos.
    
    Acepta múltiples variaciones de nombres de columnas (ej: NOMBRE, nombre_cliente, 
    customer_name, etc.) y mapea automáticamente a campos estándar.
    
    Args:
        query: Consulta SQL SELECT/WITH
        params: Ignorado (compatibilidad)
    
    Returns:
        Lista de diccionarios normalizados
    
    Raises:
        ValueError: Si la consulta es inválida o falta un campo requerido
    """
    del params
    query_text = _render_query(query).strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", query_text, re.IGNORECASE):
        raise ValueError("Solo se permiten consultas SELECT o WITH para campañas automáticas.")

    client = get_bigquery_client()
    if client is None:
        raise RuntimeError("Cliente BigQuery no disponible.")

    rows = []
    for row in client.query(query_text).result():
        rows.append({key: _normalize_bigquery_value(value) for key, value in dict(row).items()})
    
    # Validar y normalizar campos
    success, normalized_rows, error_msg = validate_and_normalize(rows)
    if not success:
        raise ValueError(f"Validación de consulta fallida: {error_msg}")
    
    return normalized_rows


def map_rows_for_wolkvox(rows: list[dict], field_mapping: dict) -> tuple[list[dict], list[str]]:
    """
    Mapea filas normalizadas a campos Wolkvox.
    
    Los campos ya vienen normalizados de fetch_data_from_bigquery.
    Este mapeo solo reorganiza al formato esperado por Wolkvox.
    
    Args:
        rows: Filas con campos normalizados (ej: customer_name, customer_id, etc.)
        field_mapping: Mapeo {target_field: source_field} (generalmente 1:1 para campos normalizados)
    
    Returns:
        (mapped_rows, column_list)
    """
    columns = list(field_mapping.keys())
    
    if rows and field_mapping:
        available_columns = set(rows[0].keys())
        expected_sources = {str(value).strip().lower() for value in field_mapping.values() if value}
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


def generate_csv_from_data(data: list[dict], columns: list[str], *, campaign_id: int, batch_index=None) -> str:
    AUTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_batch_{batch_index}" if batch_index is not None else ""
    filename = f"auto_campaign_{campaign_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.csv"
    path = AUTO_UPLOAD_DIR / _safe_filename(filename)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    return str(path)


def _response_summary(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:2000]
    return {
        "status": response.status_code,
        "ok": response.ok,
        "body": body,
    }


def _resolve_wolkvox_url(endpoint: str, campaign_id: str, server_name: str = "") -> str:
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
    parts = urlsplit(add_record_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["api"] = "start"
    query["campaign_id"] = str(campaign_id)
    for key in ("type_campaign", "campaign_status"):
        query.pop(key, None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def start_wolkvox_campaign(endpoint: str, token: str, campaign_id: str, *, server_name: str = "") -> dict:
    add_record_url = _resolve_wolkvox_url(endpoint, campaign_id, server_name)
    url = _build_start_campaign_url(add_record_url, campaign_id)
    headers = {"wolkvox-token": token} if token else {}
    try:
        # Retry on transient errors (e.g., 409 conflict or 5xx)
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
                    _log(f"Wolkvox start returned {response.status_code} on attempt {attempt}. Will retry.", level="WARN")
                    if attempt < max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    else:
                        return {"success": False, "message": f"Wolkvox start HTTP {response.status_code} after {attempt} attempts.", "url": url, "response": summary}

                return {"success": False, "message": f"Wolkvox start HTTP {response.status_code}", "url": url, "response": summary}
            except requests.Timeout:
                _log(f"Timeout iniciando campaña en Wolkvox on attempt {attempt}.", level="WARN")
                if attempt < max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue
                return {"success": False, "message": "Timeout iniciando campaña en Wolkvox.", "url": url}
            except Exception as exc:
                _log(f"Error iniciando campaña Wolkvox on attempt {attempt}: {exc}", level="ERROR")
                return {"success": False, "message": str(exc), "url": url}
        return {"success": False, "message": "Error desconocido iniciando campaña Wolkvox.", "url": url, "response": last_summary}
    except Exception as exc:
        return {"success": False, "message": str(exc), "url": url}


def send_csv_to_wolkvox(
    csv_file_path: str,
    endpoint: str,
    token: str,
    campaign_id: str,
    *,
    server_name: str = "",
) -> dict:
    url = _resolve_wolkvox_url(endpoint, campaign_id, server_name)
    if not url:
        return {"success": False, "records_sent": 0, "message": "Endpoint add_record vacío."}
    headers = {"wolkvox-token": token, "Content-Type": "application/json"} if token else {"Content-Type": "application/json"}
    try:
        csv_content = Path(csv_file_path).read_text(encoding="utf-8-sig")
        body = transform_datos_clientes(csv_content)
        if not body:
            return {"success": False, "records_sent": 0, "message": "El CSV no contiene clientes para enviar.", "url": url}
        # Implement retry/backoff for transient Wolkvox conflicts or 5xx errors
        max_retries = 5
        backoff_factor = 1.5
        last_summary = None
        payload = serialize_clientes_body(body).encode("utf-8")
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, data=payload, timeout=120)
                summary = _response_summary(response)
                last_summary = summary
                # If successful, return immediately
                if response.ok:
                    return {
                        "success": True,
                        "records_sent": 0,
                        "message": f"Wolkvox HTTP {response.status_code}",
                        "url": url,
                        "response": summary,
                    }

                # If conflict (already processing) or server error, retry with backoff
                if response.status_code in (409, 429) or (500 <= response.status_code < 600):
                    _log(f"Wolkvox returned {response.status_code} on attempt {attempt}. Will retry.", level="WARN")
                    if attempt < max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    else:
                        # exhausted retries
                        return {
                            "success": False,
                            "records_sent": 0,
                            "message": f"Wolkvox HTTP {response.status_code} after {attempt} attempts.",
                            "url": url,
                            "response": summary,
                        }

                # Other non-retriable errors: return immediately with details
                return {
                    "success": False,
                    "records_sent": 0,
                    "message": f"Wolkvox HTTP {response.status_code}",
                    "url": url,
                    "response": summary,
                }
            except requests.Timeout:
                _log(f"Timeout cargando CSV a Wolkvox on attempt {attempt}.", level="WARN")
                if attempt < max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue
                return {"success": False, "records_sent": 0, "message": "Timeout cargando CSV a Wolkvox.", "url": url}
            except Exception as exc:
                _log(f"Error enviando a Wolkvox on attempt {attempt}: {exc}", level="ERROR")
                return {"success": False, "records_sent": 0, "message": str(exc), "url": url}
        # Fallback if loop finishes without return
        return {"success": False, "records_sent": 0, "message": "Error desconocido enviando a Wolkvox.", "url": url, "response": last_summary}
    except requests.Timeout:
        return {"success": False, "records_sent": 0, "message": "Timeout cargando CSV a Wolkvox.", "url": url}
    except Exception as exc:
        return {"success": False, "records_sent": 0, "message": str(exc), "url": url}


def request_stop_auto_campaign(campaign_id: int) -> bool:
    with _running_lock:
        state = running_campaigns.get(campaign_id)
        if not state:
            return False
        state["stop_requested"] = True
        return True


def is_auto_campaign_running(campaign_id: int) -> bool:
    with _running_lock:
        return campaign_id in running_campaigns


def _get_token(campaign: AutoCampaign) -> str:
    from backend import get_authorization_headers, load_config

    load_config()
    headers = get_authorization_headers(campaign.server_name or None)
    return headers.get("wolkvox-token") or ""


def _write_report_file(log: AutoCampaignExecutionLog) -> str:
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


def run_auto_campaign(campaign_id: int) -> dict:
    with _running_lock:
        if campaign_id in running_campaigns:
            return {"success": False, "message": "La campaña automática ya está en ejecución."}
        running_campaigns[campaign_id] = {"stop_requested": False, "started_at": datetime.now()}

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        with _running_lock:
            running_campaigns.pop(campaign_id, None)
        return {"success": False, "message": "No se encontró la campaña automática."}

    log = AutoCampaignExecutionLog(auto_campaign_id=campaign.id, start_time=datetime.utcnow())
    db.session.add(log)
    campaign.running = True
    db.session.commit()

    records_sent = 0
    records_failed = 0
    full_csv_path = ""
    error_message = ""

    try:
        _log(f"Iniciando id={campaign.id} nombre={campaign.name}")
        rows = fetch_data_from_bigquery(campaign.bigquery_query)
        mapped_rows, columns = map_rows_for_wolkvox(rows, campaign.field_mapping or {})
        log.records_fetched = len(mapped_rows)

        if mapped_rows:
            full_csv_path = generate_csv_from_data(mapped_rows, columns, campaign_id=campaign.id)
            log.csv_file_path = full_csv_path
            db.session.commit()

        token = _get_token(campaign)
        if mapped_rows and not token:
            raise RuntimeError("No se encontró token Wolkvox en config.json/servidor.")

        for offset in range(0, len(mapped_rows), BATCH_SIZE):
            with _running_lock:
                if running_campaigns.get(campaign.id, {}).get("stop_requested"):
                    error_message = "Ejecución detenida por el usuario."
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
                records_sent += len(batch)
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
                _log(f"id={campaign.id} lote fallido: {error_message}", level="ERROR")

        if mapped_rows and records_failed == 0 and records_sent > 0:
            start_result = start_wolkvox_campaign(
                campaign.wolkvox_add_record_endpoint,
                token,
                campaign.wolkvox_campaign_id,
                server_name=campaign.server_name or "",
            )
            if not start_result.get("success"):
                error_message = start_result.get("message") or "No se pudo iniciar la campaña en Wolkvox."
                response_body = (
                    (start_result.get("response") or {}).get("body")
                    if isinstance(start_result.get("response"), dict)
                    else ""
                )
                if response_body:
                    error_message = f"{error_message}: {str(response_body)[:500]}"
                _log(f"id={campaign.id} inicio fallido: {error_message}", level="ERROR")

        log.records_sent = records_sent
        log.records_failed = records_failed
        log.error_message = error_message or None
        log.end_time = datetime.utcnow()

        campaign.running = False
        campaign.last_run = datetime.utcnow()
        if campaign.schedule_type == "one_time":
            campaign.next_run = None
        elif campaign.schedule_type == "recurring":
            campaign.next_run = calculate_next_run(campaign.schedule_type, campaign.schedule_value, from_time=datetime.now())
        else:
            campaign.next_run = None

        db.session.commit()
        log.report_file_path = _write_report_file(log)
        db.session.commit()
        _log(
            f"Finalizada id={campaign.id}: fetched={log.records_fetched}, sent={records_sent}, failed={records_failed}"
        )
        return {
            "success": records_failed == 0 and not error_message,
            "message": error_message or "Campaña automática ejecutada.",
            "execution_id": log.id,
            "records_fetched": log.records_fetched,
            "records_sent": records_sent,
            "records_failed": records_failed,
            "csv_file_path": full_csv_path,
        }
    except Exception as exc:
        db.session.rollback()
        error_message = str(exc)
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
        _log(f"Error id={campaign_id}: {error_message}", level="ERROR")
        return {"success": False, "message": error_message, "execution_id": log.id if log else None}
    finally:
        with _running_lock:
            running_campaigns.pop(campaign_id, None)
        campaign = AutoCampaign.query.get(campaign_id)
        if campaign and campaign.running:
            campaign.running = False
            db.session.commit()


def start_auto_campaign_async(campaign_id: int, app) -> bool:
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
    return True
