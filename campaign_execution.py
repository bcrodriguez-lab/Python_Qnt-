"""
Reglas y orquestación de APIs Wolkvox al ejecutar una campaña (Llamada / WhatsApp).

Flujo definido en get_campaign_execution_rules():
  1. Consultar campañas — verificar estado inicial
  2. Parar campaña
  3. Borrar clientes campaña
  4. Cargue de clientes (datos desde BigQuery)
  5. Monitoreo: consultar campañas cada N segundos y actualizar tablero
  6. Si la campaña terminó en monitoreo → parar campaña
"""

from __future__ import annotations

import io
import csv
import re
import time
import threading
from datetime import datetime
from typing import Any

from apis import get_api
from api_runner import invoke_handler
from bigquery import fetch_select_query_rows
from campaigns import build_api_invocation_base, requires_flujo_proceso, _campaign_to_dict
from conexion_bigquery import get_bigquery_client
from database import Campaign, db
from server_apis import is_server_api_active

# Nombres de API en config.json (deben coincidir con el registro en /config-apis)
API_CONSULTAR_CAMPANAS = "Consultar campañas"
API_PARAR_CAMPANA = "Parar campaña"
API_BORRAR_CLIENTES = "Borrar clientes campaña"
API_CARGUE_CLIENTES = "Cargue de clientes"

_running_lock = threading.Lock()
_running_campaign_ids: set[int] = set()

# Campos numéricos habituales en respuesta Wolkvox (ES / EN)
# real_time.php?api=campaigns usa: records, dial, answer, status, campaign (nombre)
_METRIC_ALIASES = {
    "total": (
        "records",
        "total",
        "total_clientes",
        "clientes",
        "registros",
        "uploaded",
        "cargados",
        "loaded",
    ),
    "llamados": (
        "dial",
        "llamados",
        "called",
        "completed",
        "completados",
        "processed",
        "marcados",
        "dialed",
        "marcaciones",
    ),
    "pendientes": (
        "pendientes",
        "pending",
        "remaining",
        "restantes",
        "por_llamar",
        "faltantes",
    ),
    "contactados": (
        "answer",
        "contactados",
        "contacted",
        "contacts",
        "answered",
        "contestadas",
    ),
    "clean": ("clean", "por_procesar", "to_process"),
}

_RUNNING_STATUS_TOKENS = frozenset(
    {
        "started",
        "start",
        "running",
        "run",
        "active",
        "activa",
        "activo",
        "en curso",
        "encurso",
        "playing",
        "play",
        "iniciada",
        "iniciado",
        "1",
    }
)

_FINISHED_STATUS_TOKENS = frozenset(
    {
        "0",
        "stopped",
        "stop",
        "detenida",
        "detenido",
        "finished",
        "finalizada",
        "finalizado",
        "completed",
        "completada",
        "completado",
        "inactive",
        "inactiva",
        "ended",
        "terminada",
        "terminado",
        "paused",
        "pausada",
        "pausado",
    }
)


def get_campaign_execution_rules() -> dict:
    """
    Reglas de ejecución de APIs al dispararse una campaña con flujo Wolkvox.
    Centraliza nombres de API, orden y parámetros de monitoreo.
    """
    return {
        "requires_flujo": True,
        "pre_start": [
            {
                "step": "consultar_estado_inicial",
                "api": API_CONSULTAR_CAMPANAS,
                "description": "Consultar campaña y verificar si ya terminó",
            },
        ],
        "prepare_campaign": [
            {
                "step": "parar_campana",
                "api": API_PARAR_CAMPANA,
                "description": "Detener la campaña en Wolkvox",
            },
            {
                "step": "borrar_clientes",
                "api": API_BORRAR_CLIENTES,
                "description": "Borrar clientes de la campaña",
            },
            {
                "step": "cargue_clientes",
                "api": API_CARGUE_CLIENTES,
                "description": "Cargar clientes desde BigQuery",
            },
        ],
        "monitor": {
            "api": API_CONSULTAR_CAMPANAS,
            "interval_seconds": 300,
            "max_cycles": 288,
            "description": "Actualizar tablero consultando campañas cada 5 minutos",
        },
        "on_finished": [
            {
                "step": "parar_campana_final",
                "api": API_PARAR_CAMPANA,
                "description": "Parar campaña cuando el monitoreo detecta que terminó",
            },
        ],
    }


def requires_wolkvox_execution(campaign: Campaign) -> bool:
    """True si la campaña debe ejecutar la secuencia Wolkvox."""
    if not requires_flujo_proceso(campaign.tipo_campana or ""):
        return False
    return bool((campaign.flujo_proceso_id or "").strip())


def _log(message: str, level: str = "INFO") -> None:
    from backend import log_task

    log_task(message, level=level)


def _log_tablero_metrics(campaign: Campaign, label: str) -> None:
    """Refleja métricas en el tablero (actividad en vivo) tras cada consulta."""
    from backend import log_activity

    faltantes = max(
        int(campaign.total_clientes or 0) - int(campaign.clientes_llamados or 0),
        0,
    )
    log_activity(
        f"[Tablero] {label} — {campaign.nombre}: "
        f"clientes={campaign.total_clientes or 0}, "
        f"llamados={campaign.clientes_llamados or 0}, "
        f"faltantes={faltantes}, "
        f"contactados={campaign.clientes_contactados or 0}"
    )


def _campaign_dict(campaign: Campaign) -> dict:
    return _campaign_to_dict(campaign)


def _invoke_named_api(
    api_name: str,
    campaign: Campaign,
    extra_payload: dict | None = None,
) -> dict:
    server = (campaign.servidor or "").strip()
    if not is_server_api_active(server, api_name):
        return {
            "success": False,
            "message": f"API '{api_name}' no está activa para el servidor '{server}'.",
        }

    api_config = get_api(api_name)
    if not api_config:
        return {"success": False, "message": f"API '{api_name}' no está configurada."}

    payload = build_api_invocation_base(_campaign_dict(campaign))
    if extra_payload:
        payload.update(extra_payload)

    archivo = api_config.get("archivo", "").strip()
    metodo = (api_config.get("metodo") or "get").strip().lower()
    return invoke_handler(archivo, metodo, api_config, payload)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (key or "").lower())


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _pick_metric(row: dict, aliases: tuple[str, ...]) -> int | None:
    if not isinstance(row, dict):
        return None
    normalized = {_normalize_key(k): v for k, v in row.items()}
    for alias in aliases:
        val = normalized.get(_normalize_key(alias))
        num = _coerce_int(val)
        if num is not None:
            return num
    return None


def _iter_dict_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dict_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dict_nodes(item)


def _campaign_label_matches(label: str, campaign_id: str) -> bool:
    """Coincide '32841' con '32841 - Nombre campaña ...' (formato Wolkvox real_time)."""
    label = str(label or "").strip()
    cid = str(campaign_id or "").strip()
    if not label or not cid:
        return False
    if label == cid:
        return True
    if label.startswith(cid):
        suffix = label[len(cid) :]
        return not suffix or suffix[0] in "-_ (|["
    return False


def _campaign_id_matches(row: dict, campaign_id: str) -> bool:
    cid = str(campaign_id).strip()
    if not cid:
        return False
    for key in (
        "campaign_id",
        "id",
        "campaignId",
        "campaignid",
        "id_campana",
        "idcampana",
    ):
        if str(row.get(key, "")).strip() == cid:
            return True
    for key in ("campaign", "nombre", "name", "campaign_name", "campana"):
        if _campaign_label_matches(str(row.get(key, "")), cid):
            return True
    normalized = {_normalize_key(k): v for k, v in row.items()}
    for key in ("campaignid", "id"):
        if str(normalized.get(key, "")).strip() == cid:
            return True
    if _campaign_label_matches(str(normalized.get("campaign", "")), cid):
        return True
    return False


def find_campaign_in_consultar_response(data: Any, campaign_id: str) -> dict | None:
    """Busca el registro de la campaña en la respuesta de Consultar campañas."""
    campaign_id = str(campaign_id or "").strip()
    if not campaign_id or data is None:
        return None

    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            for item in inner:
                if isinstance(item, dict) and _campaign_id_matches(item, campaign_id):
                    return item
        for key in ("data", "campaigns", "campanas", "result", "results"):
            nested = data.get(key)
            if nested is inner:
                continue
            found = find_campaign_in_consultar_response(nested, campaign_id)
            if found:
                return found
        if _campaign_id_matches(data, campaign_id):
            return data

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and _campaign_id_matches(item, campaign_id):
                return item

    for node in _iter_dict_nodes(data):
        if _campaign_id_matches(node, campaign_id):
            return node
    return None


def _parse_percent(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace("%", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _wolkvox_metrics(campaign_row: dict) -> dict[str, int | None]:
    """Extrae total / llamados / pendientes / contactados / clean de la fila Wolkvox."""
    total = _pick_metric(campaign_row, _METRIC_ALIASES["total"])
    llamados = _pick_metric(campaign_row, _METRIC_ALIASES["llamados"])
    contactados = _pick_metric(campaign_row, _METRIC_ALIASES["contactados"])
    clean = _pick_metric(campaign_row, _METRIC_ALIASES["clean"])
    pendientes = _pick_metric(campaign_row, _METRIC_ALIASES["pendientes"])

    if total is not None and llamados is not None:
        pendientes = max(total - llamados, 0)
    elif total is not None and pendientes is not None and llamados is None:
        llamados = max(total - pendientes, 0)
        pendientes = max(total - llamados, 0)

    if clean is not None and clean > 0:
        pendientes = max(pendientes or 0, clean)

    return {
        "total": total,
        "llamados": llamados,
        "pendientes": pendientes,
        "contactados": contactados,
        "clean": clean,
    }


def campaign_is_finished(campaign_row: dict | None) -> bool:
    """
    Determina si la campaña terminó de procesar/marcar todos los registros.

    Wolkvox real_time (api=campaigns):
      - records = registros cargados
      - dial = registros marcados/llamados
      - clean != 0 → aún hay registros por procesar (no terminar)
      - Terminada cuando clean == 0, dial >= records y records > 0
      - status puede seguir en 'started' aunque ya se marcó todo
    """
    if not campaign_row:
        return False

    metrics = _wolkvox_metrics(campaign_row)
    total = metrics["total"]
    llamados = metrics["llamados"]
    pendientes = metrics["pendientes"]
    clean = metrics["clean"]

    if clean is not None and clean != 0:
        return False

    if total is not None and total > 0 and llamados is not None and llamados >= total:
        return True

    if pendientes is not None and pendientes <= 0 and (total or 0) > 0:
        return True

    penetration = _parse_percent(
        campaign_row.get("penetration_now") or campaign_row.get("penetration_day")
    )
    if (
        penetration is not None
        and penetration >= 100.0
        and total
        and total > 0
        and llamados is not None
        and llamados >= total
    ):
        return True

    status_raw = None
    for key in (
        "status",
        "estado",
        "campaign_status",
        "state",
        "estado_campana",
    ):
        if key in campaign_row:
            status_raw = campaign_row.get(key)
            break

    if status_raw is not None:
        if isinstance(status_raw, bool):
            return status_raw is False
        token = str(status_raw).strip().lower()
        if token in _RUNNING_STATUS_TOKENS:
            return False
        if token in _FINISHED_STATUS_TOKENS:
            return True

    for key in ("running", "activa", "active"):
        if key not in campaign_row:
            continue
        raw = campaign_row.get(key)
        if isinstance(raw, bool):
            return raw is False
        token = str(raw).strip().lower()
        if token in _RUNNING_STATUS_TOKENS:
            return False
        if token in _FINISHED_STATUS_TOKENS or token in ("false", "no", "n"):
            return True

    return False


def apply_consultar_metrics_to_campaign(
    campaign: Campaign, campaign_row: dict | None
) -> None:
    """Actualiza métricas en SQLite para el tablero."""
    if not campaign_row:
        return
    metrics = _wolkvox_metrics(campaign_row)
    total = metrics["total"]
    llamados = metrics["llamados"]
    contactados = metrics["contactados"]
    clean = metrics["clean"]

    if total is not None:
        campaign.total_clientes = total
    elif llamados is not None and metrics["pendientes"] is not None:
        campaign.total_clientes = llamados + metrics["pendientes"]

    if llamados is not None:
        if clean is not None and clean > 0 and total is not None:
            campaign.clientes_llamados = max(0, total - clean)
        else:
            campaign.clientes_llamados = llamados
    if contactados is not None:
        campaign.clientes_contactados = contactados

    db.session.commit()


def _fetch_clientes_for_carga(campaign: Campaign) -> dict:
    consulta = (campaign.consulta or "").strip()
    if not consulta:
        return {"success": False, "message": "La campaña no tiene consulta BigQuery para el cargue."}

    bq_client = get_bigquery_client()
    if bq_client is None:
        return {"success": False, "message": "Cliente BigQuery no disponible."}

    return fetch_select_query_rows(bq_client, consulta)


def _rows_to_csv_text(rows: list[dict]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def refresh_campaign_status(campaign: Campaign) -> dict:
    """Consulta estado en Wolkvox y actualiza métricas (uso manual desde tablero)."""
    result, row, finished = _step_consultar(
        campaign, label="Actualización tablero"
    )
    if not result.get("success"):
        return result
    if not row:
        return {
            "success": False,
            "message": "Campaña no encontrada en la respuesta de Wolkvox.",
        }
    return {
        "success": True,
        "finished": finished,
        "message": "Estado actualizado.",
    }


def _step_consultar(
    campaign: Campaign,
    *,
    label: str,
) -> tuple[dict, dict | None, bool]:
    result = _invoke_named_api(API_CONSULTAR_CAMPANAS, campaign)
    finished = False
    row = None
    if result.get("success"):
        row = find_campaign_in_consultar_response(
            result.get("data"), campaign.flujo_proceso_id
        )
        if not row:
            _log(
                f"[API] {label} campaña id={campaign.id} flujo={campaign.flujo_proceso_id}: "
                "no se encontró la campaña en la respuesta Wolkvox",
                level="WARNING",
            )
        else:
            apply_consultar_metrics_to_campaign(campaign, row)
            finished = campaign_is_finished(row)
            metrics = _wolkvox_metrics(row)
            estado = "terminada" if finished else "en curso"
            _log_tablero_metrics(campaign, label)
            _log(
                f"[API] {label} campaña id={campaign.id} "
                f"flujo={campaign.flujo_proceso_id}: {estado} "
                f"(records={metrics['total']}, dial={metrics['llamados']}, "
                f"clean={metrics['clean']}, pendientes={metrics['pendientes']}, "
                f"status={row.get('status', '-')})"
            )
    else:
        _log(
            f"[API] {label} campaña id={campaign.id} error: {result.get('message')}",
            level="WARNING",
        )
    return result, row, finished


def _run_prepare_steps(campaign: Campaign) -> bool:
    rules = get_campaign_execution_rules()
    for step in rules["prepare_campaign"]:
        api_name = step["api"]
        if step["step"] == "cargue_clientes":
            fetch = _fetch_clientes_for_carga(campaign)
            if not fetch.get("success"):
                _log(
                    f"[API] Cargue clientes campaña id={campaign.id}: "
                    f"{fetch.get('message')}",
                    level="ERROR",
                )
                return False
            rows = fetch.get("rows") or []
            if not rows:
                _log(
                    f"[API] Cargue clientes campaña id={campaign.id}: sin filas en BigQuery",
                    level="WARNING",
                )
                return False
            extra = {"datos_clientes": _rows_to_csv_text(rows)}
            result = _invoke_named_api(api_name, campaign, extra)
            if result.get("success"):
                campaign.total_clientes = len(rows)
                campaign.clientes_llamados = 0
                campaign.clientes_contactados = 0
                db.session.commit()
                _log_tablero_metrics(campaign, "Tras cargue de clientes")
        elif step["step"] == "borrar_clientes":
            try:
                result = _invoke_named_api(api_name, campaign)
                if not result.get("success"):
                    _log(
                        f"[API] {step['description']} (id={campaign.id}): "
                        f"{result.get('message')} — se continúa con el cargue",
                        level="WARNING",
                    )
                else:
                    _log(f"[API] {step['description']} (id={campaign.id}): OK")
            except Exception as exc:
                _log(
                    f"[API] {step['description']} (id={campaign.id}): "
                    f"excepción {exc} — se continúa con el cargue",
                    level="WARNING",
                )
            continue
        else:
            result = _invoke_named_api(api_name, campaign)

        if not result.get("success"):
            _log(
                f"[API] {step['description']} (id={campaign.id}): "
                f"{result.get('message')}",
                level="ERROR",
            )
            return False
        _log(f"[API] {step['description']} (id={campaign.id}): OK")
    return True


def _run_monitor_loop(campaign: Campaign) -> bool:
    rules = get_campaign_execution_rules()
    monitor = rules["monitor"]
    interval = int(monitor.get("interval_seconds", 300))
    max_cycles = int(monitor.get("max_cycles", 288))

    for cycle in range(1, max_cycles + 1):
        _log(
            f"[API] Monitoreo campaña id={campaign.id} "
            f"ciclo {cycle}/{max_cycles} (cada {interval}s)"
        )
        _, row, finished = _step_consultar(
            campaign, label=f"Monitoreo #{cycle}"
        )
        if finished:
            _log(
                f"[API] Campaña id={campaign.id} flujo={campaign.flujo_proceso_id} "
                "detectada como terminada"
            )
            return True
        if cycle < max_cycles:
            time.sleep(interval)
    _log(
        f"[API] Monitoreo campaña id={campaign.id}: máximo de ciclos alcanzado sin fin",
        level="WARNING",
    )
    return False


def run_campaign_wolkvox_workflow(campaign_id: int) -> None:
    """Ejecuta la secuencia completa de APIs para una campaña."""
    campaign = Campaign.query.get(campaign_id)
    if not campaign:
        _log(f"[API] Campaña id={campaign_id} no encontrada", level="ERROR")
        return

    if not requires_wolkvox_execution(campaign):
        _log(
            f"[API] Campaña id={campaign.id} no requiere flujo Wolkvox; secuencia omitida"
        )
        return

    rules = get_campaign_execution_rules()
    _log(
        f"[API] Inicio secuencia Wolkvox campaña id={campaign.id} "
        f"({campaign.nombre}) flujo={campaign.flujo_proceso_id}"
    )

    for step in rules["pre_start"]:
        _step_consultar(campaign, label=step["description"])

    if not _run_prepare_steps(campaign):
        _log(
            f"[API] Secuencia abortada en preparación campaña id={campaign.id}",
            level="ERROR",
        )
        return

    finished = _run_monitor_loop(campaign)

    if finished:
        # 1) Parar campaña en Wolkvox
        for step in rules["on_finished"]:
            result = _invoke_named_api(step["api"], campaign)
            if result.get("success"):
                _log(f"[API] {step['description']} (id={campaign.id}): OK")
            else:
                _log(
                    f"[API] {step['description']} (id={campaign.id}): "
                    f"{result.get('message')}",
                    level="WARNING",
                )

        # 2) Marcar fin + opcional: generar archivo Excel y dejarlo listo para descarga
        # Nota: no es posible "descargar al navegador" desde este worker sin una petición HTTP.
        # En su lugar, generamos un XLSX formateado y lo dejamos en uploads/descargas como archivo.
        try:
            from excel_report_builder import build_wolkvox_excel, _safe_filename
            import os

            server_name = (campaign.servidor or "").strip()
            campaign_name = (campaign.nombre or "").strip() or f"campaign_{campaign.id}"
            end_dt = datetime.utcnow()

            # Ventana: día completo del end_dt
            date_ini = end_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
            date_end = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d")

            # Reutilizamos la lógica de /reports/download desde Wolkvox (sin endpoint)
            # Construimos URL y consumimos
            from backend import get_authorization_headers, get_server
            import requests

            def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
                if not s:
                    return ""
                if "T" in s or len(s) > 10:
                    dt = datetime.fromisoformat(s)
                else:
                    from datetime import date
                    d = date.fromisoformat(s)
                    dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
                return dt.strftime("%Y%m%d%H%M%S")

            date_ini_ts = _to_wolkvox_ts(date_ini, is_end=False)
            date_end_ts = _to_wolkvox_ts(date_end, is_end=True)

            srv = None
            try:
                srv = get_server(server_name)
            except Exception:
                srv = None

            if srv:
                prefix = (srv.get("url") or "").strip().rstrip("/")
                base_url = prefix if prefix.lower().startswith("http") else f"https://wv{prefix}.wolkvox.com"
            else:
                if server_name.lower().startswith("http"):
                    base_url = server_name.rstrip("/")
                else:
                    base_url = f"https://wv{server_name}.wolkvox.com"
                base_url = base_url.rstrip("/")

            url = (
                f"{base_url}/api/v2/reports_manager.php"
                f"?api=cdr_1"
                f"&date_ini={date_ini_ts}"
                f"&date_end={date_end_ts}"
            )

            headers = get_authorization_headers(server_name) or {}
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()

            data_json = resp.json()
            rows = []
            if isinstance(data_json, list):
                rows = data_json
            elif isinstance(data_json, dict):
                if "data" in data_json and isinstance(data_json["data"], list):
                    rows = data_json["data"]
                elif "files" in data_json and isinstance(data_json["files"], list):
                    rows = data_json["files"]
                else:
                    rows = [data_json]
            else:
                rows = [{"raw": resp.text}]

            safe_server = _safe_filename(server_name)
            safe_campaign = _safe_filename(campaign_name)
            end_file_ts = end_dt.strftime("%Y%m%d_%H%M%S")
            filename = f"auto_campaign_report_{safe_server}_{safe_campaign}_{end_file_ts}.xlsx"

            # Guardar en la carpeta uploads (o downloads si prefieres)
            output_dir = os.path.join(os.path.dirname(__file__), "uploads", "auto_campaigns")
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, filename)

            bio, _ = build_wolkvox_excel(rows=rows, filename=filename)
            with open(output_path, "wb") as f:
                f.write(bio.getvalue())

            _log(f"[API] XLSX generado al finalizar campaña id={campaign.id}: {output_path}")
        except Exception as exc:
            _log(f"[API] No se pudo generar XLSX al finalizar campaña id={campaign.id}: {exc}", level="WARNING")

    _log(f"[API] Secuencia Wolkvox finalizada campaña id={campaign.id}")


def start_campaign_wolkvox_workflow_async(campaign_id: int, app) -> bool:
    """
    Lanza la secuencia en un hilo de fondo.
    Retorna False si la campaña ya está en ejecución en este proceso.
    """
    with _running_lock:
        if campaign_id in _running_campaign_ids:
            return False
        _running_campaign_ids.add(campaign_id)

    def _worker():
        with app.app_context():
            try:
                run_campaign_wolkvox_workflow(campaign_id)
            except Exception as exc:
                _log(
                    f"[API] Error en secuencia Wolkvox campaña id={campaign_id}: {exc}",
                    level="ERROR",
                )
            finally:
                with _running_lock:
                    _running_campaign_ids.discard(campaign_id)

    threading.Thread(
        target=_worker,
        name=f"campaign-wolkvox-{campaign_id}",
        daemon=True,
    ).start()
    return True
