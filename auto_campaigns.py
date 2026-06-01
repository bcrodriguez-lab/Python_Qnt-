import json
import threading
from datetime import datetime, timedelta

from campaigns import TIPO_CAMPANA_CON_FLUJO
from database import AutoCampaign, AutoCampaignExecutionLog, db

SCHEDULE_TYPES = ("manual", "one_time", "recurring")


def parse_auto_campaign_id(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _loads_json(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def calculate_next_run(schedule_type: str, schedule_value, *, from_time=None):
    if schedule_type == "manual":
        return None

    if schedule_type == "one_time":
        return _parse_datetime(schedule_value)

    value = _loads_json(schedule_value, {})
    base = from_time or datetime.now()
    hours = value.get("interval_hours")
    days = value.get("interval_days")
    minutes = value.get("interval_minutes")
    try:
        delta = timedelta(
            days=int(days or 0),
            hours=int(hours or 0),
            minutes=int(minutes or 0),
        )
    except (TypeError, ValueError):
        delta = timedelta()
    if delta.total_seconds() <= 0:
        delta = timedelta(hours=24)
    return base + delta


def auto_campaign_to_dict(campaign: AutoCampaign) -> dict:
    latest_log = (
        AutoCampaignExecutionLog.query.filter_by(auto_campaign_id=campaign.id)
        .order_by(AutoCampaignExecutionLog.start_time.desc())
        .first()
    )
    return {
        "id": campaign.id,
        "name": campaign.name,
        "operation": campaign.operation or "",
        "type": campaign.type or "",
        "campaign_type": campaign.campaign_type or "",
        "description": campaign.description or "",
        "start_date": campaign.start_date.strftime("%Y-%m-%d %H:%M:%S") if campaign.start_date else "",
        "user_name": campaign.user_name or "",
        "bigquery_query": campaign.bigquery_query,
        "wolkvox_add_record_endpoint": campaign.wolkvox_add_record_endpoint,
        "wolkvox_delete_records_endpoint": campaign.wolkvox_delete_records_endpoint or "",
        "wolkvox_campaign_id": campaign.wolkvox_campaign_id,
        "server_name": campaign.server_name or "",
        "flujo_proceso_id": campaign.flujo_proceso_id or "",
        "field_mapping": campaign.field_mapping or {},
        "schedule_type": campaign.schedule_type,
        "schedule_value": campaign.schedule_value,
        "status": bool(campaign.status),
        "running": bool(campaign.running),
        "last_run": campaign.last_run.strftime("%Y-%m-%d %H:%M:%S") if campaign.last_run else "",
        "next_run": campaign.next_run.strftime("%Y-%m-%d %H:%M:%S") if campaign.next_run else "",
        "last_precount": int(campaign.last_precount or 0),
        "created_at": campaign.created_at.strftime("%Y-%m-%d %H:%M:%S") if campaign.created_at else "",
        "updated_at": campaign.updated_at.strftime("%Y-%m-%d %H:%M:%S") if campaign.updated_at else "",
        "latest_execution_id": latest_log.id if latest_log else None,
        "latest_records_fetched": latest_log.records_fetched if latest_log else 0,
        "latest_records_sent": latest_log.records_sent if latest_log else 0,
        "latest_records_failed": latest_log.records_failed if latest_log else 0,
        "latest_error": latest_log.error_message if latest_log else "",
    }


def execution_log_to_dict(log: AutoCampaignExecutionLog) -> dict:
    return {
        "id": log.id,
        "auto_campaign_id": log.auto_campaign_id,
        "start_time": log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else "",
        "end_time": log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else "",
        "records_fetched": log.records_fetched,
        "records_sent": log.records_sent,
        "records_failed": log.records_failed,
        "error_message": log.error_message or "",
        "csv_file_path": log.csv_file_path or "",
        "report_file_path": log.report_file_path or "",
    }


def validate_auto_campaign_payload(data: dict, *, partial=False) -> tuple[dict | None, str | None]:
    data = data or {}
    required = (
        "name",
        "operation",
        "type",
        "campaign_type",
        "server_name",
        "user_name",
        "bigquery_query",
        "wolkvox_add_record_endpoint",
        "wolkvox_campaign_id",
        "field_mapping",
        "schedule_type",
    )
    if not partial:
        missing = [key for key in required if data.get(key) in (None, "")]
        if missing:
            return None, f"Campos obligatorios faltantes: {', '.join(missing)}."

    payload = {}
    for key in (
        "name",
        "operation",
        "type",
        "campaign_type",
        "description",
        "user_name",
        "bigquery_query",
        "wolkvox_add_record_endpoint",
        "wolkvox_delete_records_endpoint",
        "wolkvox_campaign_id",
        "server_name",
        "flujo_proceso_id",
    ):
        if key in data:
            payload[key] = (data.get(key) or "").strip()

    if "start_date" in data:
        payload["start_date"] = _parse_datetime(data.get("start_date"))

    if "field_mapping" in data:
        mapping = _loads_json(data.get("field_mapping"), None)
        if not isinstance(mapping, dict) or not mapping:
            return None, "field_mapping debe ser un JSON objeto, por ejemplo {\"strategy\":\"strategy\"}."
        payload["field_mapping"] = mapping

    if "schedule_type" in data:
        schedule_type = (data.get("schedule_type") or "manual").strip()
        if schedule_type not in SCHEDULE_TYPES:
            return None, "schedule_type debe ser manual, one_time o recurring."
        payload["schedule_type"] = schedule_type

    if "schedule_value" in data:
        raw_value = data.get("schedule_value")
        payload["schedule_value"] = _loads_json(raw_value, raw_value)

    if "status" in data:
        status = data.get("status")
        payload["status"] = status if isinstance(status, bool) else str(status).lower() in ("1", "true", "on", "yes")

    flujo_proceso_id = (data.get("flujo_proceso_id") or "").strip()
    if "flujo_proceso_id" in data:
        payload["flujo_proceso_id"] = flujo_proceso_id

    tipo_campana = (payload.get("campaign_type") or data.get("campaign_type") or "").strip()
    if tipo_campana and tipo_campana in TIPO_CAMPANA_CON_FLUJO and not flujo_proceso_id:
        return None, "Debe seleccionar un flujo de proceso para campañas Llamada o WhatsApp."

    schedule_type = payload.get("schedule_type") or data.get("schedule_type") or "manual"
    schedule_value = payload.get("schedule_value", data.get("schedule_value"))
    if schedule_type == "one_time" and not schedule_value and payload.get("start_date"):
        schedule_value = payload["start_date"].strftime("%Y-%m-%d %H:%M:%S")
        payload["schedule_value"] = schedule_value
    payload["next_run"] = calculate_next_run(schedule_type, schedule_value)
    return payload, None


def list_auto_campaigns() -> list[dict]:
    rows = AutoCampaign.query.order_by(AutoCampaign.id.desc()).all()
    return [auto_campaign_to_dict(row) for row in rows]


def get_auto_campaign(campaign_id: int) -> dict | None:
    campaign = AutoCampaign.query.get(campaign_id)
    return auto_campaign_to_dict(campaign) if campaign else None


def create_auto_campaign(data: dict) -> dict:
    payload, error = validate_auto_campaign_payload(data)
    if error:
        return {"success": False, "message": error}
    try:
        campaign = AutoCampaign(**payload)
        db.session.add(campaign)
        db.session.commit()
        return {"success": True, "message": "Campaña automática creada.", "campaign": auto_campaign_to_dict(campaign)}
    except Exception as exc:
        db.session.rollback()
        return {"success": False, "message": str(exc)}


def update_auto_campaign(campaign_id: int, data: dict) -> dict:
    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return {"success": False, "message": "No se encontró la campaña automática."}
    payload, error = validate_auto_campaign_payload(data, partial=True)
    if error:
        return {"success": False, "message": error}
    try:
        for key, value in payload.items():
            setattr(campaign, key, value)
        db.session.commit()
        return {"success": True, "message": "Campaña automática actualizada.", "campaign": auto_campaign_to_dict(campaign)}
    except Exception as exc:
        db.session.rollback()
        return {"success": False, "message": str(exc)}


def delete_auto_campaign(campaign_id: int) -> dict:
    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return {"success": False, "message": "No se encontró la campaña automática."}
    if campaign.running:
        return {"success": False, "message": "No se puede borrar una campaña en ejecución."}
    try:
        AutoCampaignExecutionLog.query.filter_by(auto_campaign_id=campaign_id).delete()
        db.session.delete(campaign)
        db.session.commit()
        return {"success": True, "message": "Campaña automática eliminada."}
    except Exception as exc:
        db.session.rollback()
        return {"success": False, "message": str(exc)}


def list_execution_logs(campaign_id: int) -> list[dict]:
    logs = (
        AutoCampaignExecutionLog.query.filter_by(auto_campaign_id=campaign_id)
        .order_by(AutoCampaignExecutionLog.start_time.desc())
        .all()
    )
    return [execution_log_to_dict(log) for log in logs]


def check_auto_campaigns_schedule(app=None) -> dict:
    from auto_campaign_executor import is_auto_campaign_running, start_auto_campaign_async

    now = datetime.now()
    due = (
        AutoCampaign.query.filter(
            AutoCampaign.status.is_(True),
            AutoCampaign.next_run.isnot(None),
            AutoCampaign.next_run <= now,
        )
        .order_by(AutoCampaign.next_run.asc())
        .all()
    )
    started = 0
    skipped = 0
    for campaign in due:
        if campaign.running or is_auto_campaign_running(campaign.id):
            skipped += 1
            continue
        if app is not None:
            if start_auto_campaign_async(campaign.id, app):
                started += 1
            else:
                skipped += 1
        else:
            thread = threading.Thread(target=start_auto_campaign_async, args=(campaign.id, app), daemon=True)
            thread.start()
            started += 1
    return {"success": True, "due": len(due), "started": started, "skipped": skipped}
