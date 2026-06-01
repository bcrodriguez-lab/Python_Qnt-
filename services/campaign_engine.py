import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import threading
import time
from datetime import datetime
from typing import Optional

from database import Campaign, db
from campaigns import requires_flujo_proceso
from bigquery import fetch_select_query_rows, get_bigquery_client
from csv_service import save_dataframe_to_csv
from wolkvox_service import upload_csv_to_campaign

import pandas as pd

_running_lock = threading.Lock()
_running_campaign_ids: set[int] = set()


def _log(message: str, level: str = "INFO") -> None:
    from backend import log_task
    log_task(f"[AUTO-WORKFLOW] {message}", level=level)


def execute_bigquery_query(campaign: Campaign) -> dict:
    """Execute BigQuery query from campaign and return results."""
    consulta = (campaign.consulta or "").strip()
    if not consulta:
        return {"success": False, "message": "La campaña no tiene consulta BigQuery definida."}

    bq_client = get_bigquery_client()
    if bq_client is None:
        return {"success": False, "message": "Cliente BigQuery no disponible."}

    result = fetch_select_query_rows(bq_client, consulta)
    if not result.get("success"):
        return result

    rows = result.get("rows") or []
    if not rows:
        return {"success": True, "rows": [], "total": 0, "message": "Consulta sin resultados."}

    df = pd.DataFrame(rows)
    return {"success": True, "df": df, "rows": rows, "total": len(rows), "message": f"Se obtuvieron {len(rows)} filas."}


def generate_csv_from_results(df: pd.DataFrame, campaign: Campaign) -> dict:
    """Generate CSV file from BigQuery results DataFrame."""
    if df is None or df.empty:
        return {"success": False, "message": "DataFrame vacío, no se genera CSV."}

    filename = f"campaign_{campaign.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        csv_path = save_dataframe_to_csv(df, filename)
        return {"success": True, "csv_path": csv_path, "message": f"CSV generado: {csv_path}"}
    except Exception as e:
        return {"success": False, "message": f"Error generando CSV: {str(e)}"}


def upload_to_wolkvox(campaign: Campaign, csv_path: str) -> dict:
    """Upload CSV to Wolkvox campaign using add_record endpoint."""
    if not campaign.flujo_proceso_id:
        return {"success": False, "message": "La campaña no tiene flujo_proceso_id (campaign_id)."}

    server_name = (campaign.servidor or "").strip() or None

    return upload_csv_to_campaign(
        campaign_id=str(campaign.flujo_proceso_id),
        csv_file_path=csv_path,
        server_name=server_name,
        api_name="Cargue de clientes",
    )


def track_execution(campaign: Campaign, total_rows: int, upload_result: dict, execution_start: datetime) -> None:
    """Track execution in database."""
    campaign.total_clientes = total_rows
    campaign.ejecutada = True
    db.session.commit()

    duration = (datetime.now() - execution_start).total_seconds()
    status = "EXITOSA" if upload_result.get("success") else "FALLIDA"
    _log(
        f"Campaña id={campaign.id} ({campaign.nombre}): {status}, "
        f"filas={total_rows}, duracion={duration:.1f}s"
    )


def run_automatic_campaign_workflow(campaign_id: int, app=None, restart_cycle: bool = False) -> Optional[dict]:
    """
    Run the full automatic campaign workflow.

    Workflow steps:
    1. Execute BigQuery query from campaign
    2. Generate CSV from results
    3. Upload CSV to Wolkvox using add_record endpoint
    4. Track execution in database
    5. Optionally restart the cycle

    Args:
        campaign_id: The ID of the campaign to execute.
        app: Flask application instance for async execution context.
        restart_cycle: If True, restart workflow after completion.

    Returns:
        dict | None: Execution result with success status and details.
    """
    campaign = Campaign.query.get(campaign_id)
    if not campaign:
        return None

    if not requires_flujo_proceso(campaign.tipo_campana or ""):
        _log(f"Campaña id={campaign_id} no requiere flujo automático", level="WARNING")
        return {"success": False, "message": "Campaña no requiere flujo automático."}

    if app is not None:
        return start_automatic_workflow_async(campaign_id, app, restart_cycle)

    return _execute_workflow(campaign, restart_cycle)


def _execute_workflow(campaign: Campaign, restart_cycle: bool) -> dict:
    """Execute the automatic workflow synchronously."""
    with _running_lock:
        if campaign.id in _running_campaign_ids:
            return {"success": False, "message": "Campaña ya en ejecución."}
        _running_campaign_ids.add(campaign.id)

    try:
        execution_start = datetime.now()
        _log(f"Iniciando workflow automático campaña id={campaign.id} ({campaign.nombre})")

        bq_result = execute_bigquery_query(campaign)
        if not bq_result.get("success"):
            return {"success": False, "message": bq_result.get("message")}

        csv_result = generate_csv_from_results(bq_result.get("df"), campaign)
        if not csv_result.get("success"):
            return {"success": False, "message": csv_result.get("message")}

        upload_result = upload_to_wolkvox(campaign, csv_result.get("csv_path"))
        track_execution(campaign, bq_result.get("total", 0), upload_result, execution_start)

        result = {
            "success": upload_result.get("success", False),
            "campaign_id": campaign.id,
            "campaign_name": campaign.nombre,
            "total_rows": bq_result.get("total", 0),
            "csv_path": csv_result.get("csv_path"),
            "upload_result": upload_result,
            "message": upload_result.get("message", "Workflow completado."),
        }

        if restart_cycle and upload_result.get("success"):
            _log(f"Reiniciando ciclo para campaña id={campaign.id}")
            campaign.ejecutada = False
            db.session.commit()
            return _execute_workflow(campaign, restart_cycle=False)

        return result
    finally:
        with _running_lock:
            _running_campaign_ids.discard(campaign.id)


def start_automatic_workflow_async(campaign_id: int, app, restart_cycle: bool = False) -> bool:
    """
    Launch the automatic workflow in a background thread.

    Returns False if the campaign is already running in this process.
    """
    with _running_lock:
        if campaign_id in _running_campaign_ids:
            return False
        _running_campaign_ids.add(campaign_id)

    def _worker():
        with app.app_context():
            try:
                run_automatic_campaign_workflow(campaign_id, app=None, restart_cycle=restart_cycle)
            except Exception as exc:
                _log(f"Error en workflow automático campaña id={campaign_id}: {exc}", level="ERROR")
            finally:
                with _running_lock:
                    _running_campaign_ids.discard(campaign_id)

    threading.Thread(
        target=_worker,
        name=f"auto-campaign-{campaign_id}",
        daemon=True,
    ).start()
    return True


def is_campaign_running(campaign_id: int) -> bool:
    """Check if a campaign is currently being executed."""
    with _running_lock:
        return campaign_id in _running_campaign_ids


def run_all_pending_campaigns(app=None, max_retries: int = 3) -> dict:
    """
    Execute all pending campaigns (from current day) in automatic mode.

    Args:
        app: Flask application instance for async execution context.
        max_retries: Maximum retry attempts for failed uploads.

    Returns:
        dict: Summary with counts of executed/failed campaigns.
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start.replace(hour=23, minute=59, second=59)

    pending_campaigns = (
        Campaign.query.filter(
            Campaign.fecha_inicio >= today_start,
            Campaign.fecha_inicio <= today_end,
            Campaign.ejecutada.is_(False),
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )

    results = {"executed": 0, "failed": 0, "skipped": 0, "details": []}

    for campaign in pending_campaigns:
        if not (campaign.consulta or "").strip():
            results["skipped"] += 1
            results["details"].append({"id": campaign.id, "status": "skipped", "reason": "no_query"})
            continue

        result = run_automatic_campaign_workflow(campaign.id, app)
        if result:
            if result.get("success"):
                results["executed"] += 1
            else:
                results["failed"] += 1
            results["details"].append({"id": campaign.id, "status": "executed" if result.get("success") else "failed", "result": result})

    return results


def execute_with_retry(campaign: Campaign, max_retries: int = 3, retry_delay: float = 5.0) -> dict:
    """Execute campaign workflow with retry logic on failure."""
    last_result = None

    for attempt in range(1, max_retries + 1):
        _log(f"Intento {attempt}/{max_retries} campaña id={campaign.id}")
        last_result = _execute_workflow(campaign, restart_cycle=False)

        if last_result.get("success"):
            return last_result

        if attempt < max_retries:
            _log(f"Reintentando en {retry_delay}s...", level="WARNING")
            time.sleep(retry_delay)

    return last_result