from datetime import datetime

from sqlalchemy import or_

from database import Campaign, db

TIPO_CAMPANA_OPTIONS = ("Email", "Llamada", "SMS", "WhatsApp")
TIPO_CAMPANA_CON_FLUJO = ("Llamada", "WhatsApp")


def requires_flujo_proceso(tipo_campana: str) -> bool:
    """True si el tipo de campaña exige flujo de proceso (id → campaign_id en APIs)."""
    if not tipo_campana:
        return False
    normalized = tipo_campana.strip()
    return any(
        option.lower() == normalized.lower() for option in TIPO_CAMPANA_CON_FLUJO
    )


def validate_tipo_campana(value: str) -> str | None:
    """Devuelve el valor normalizado o None si no es válido."""
    if not value:
        return None
    normalized = value.strip()
    for option in TIPO_CAMPANA_OPTIONS:
        if option.lower() == normalized.lower():
            return option
    return None


def _campaign_to_dict(campaign: Campaign) -> dict:
    fecha = campaign.fecha_inicio
    fecha_str = fecha.strftime("%Y-%m-%d %H:%M:%S") if fecha else ""
    editable = bool(fecha and fecha >= datetime.now())
    return {
        "id": campaign.id,
        "nombre": campaign.nombre,
        "operacion": campaign.operacion,
        "tipo": campaign.tipo,
        "tipo_campana": campaign.tipo_campana or "",
        "flujo_proceso_id": campaign.flujo_proceso_id or "",
        "descripcion": campaign.descripcion or "",
        "usuario": campaign.usuario,
        "fecha_inicio": fecha_str,
        "consulta": campaign.consulta,
        "servidor": campaign.servidor or "",
        "api": campaign.api or "",
        "total_clientes": int(campaign.total_clientes or 0),
        "clientes_llamados": int(campaign.clientes_llamados or 0),
        "clientes_contactados": int(campaign.clientes_contactados or 0),
        "editable": editable,
    }


def has_pending_campaigns_for_api(api_name: str) -> bool:
    """True si hay campañas con esa API cuya fecha de inicio aún no ha pasado."""
    if not api_name or not api_name.strip():
        return False
    name = api_name.strip()
    pending = Campaign.query.filter(
        Campaign.api == name,
        Campaign.fecha_inicio >= datetime.now(),
    ).first()
    return pending is not None


def pending_campaigns_for_api(api_name: str) -> list[dict]:
    """Lista campañas pendientes asociadas a la API."""
    if not api_name or not api_name.strip():
        return []
    name = api_name.strip()
    rows = (
        Campaign.query.filter(
            Campaign.api == name,
            Campaign.fecha_inicio >= datetime.now(),
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )
    return [{"id": c.id, "nombre": c.nombre, "fecha_inicio": c.fecha_inicio.strftime("%Y-%m-%d %H:%M:%S")} for c in rows]


def has_pending_campaigns_for_server(server_name: str) -> bool:
    """True si hay campañas con ese servidor cuya fecha de inicio aún no ha pasado."""
    if not server_name or not server_name.strip():
        return False
    name = server_name.strip()
    pending = Campaign.query.filter(
        Campaign.servidor == name,
        Campaign.fecha_inicio >= datetime.now(),
    ).first()
    return pending is not None


def pending_campaigns_for_server(server_name: str) -> list[dict]:
    """Lista campañas pendientes (no ejecutadas) asociadas al servidor."""
    if not server_name or not server_name.strip():
        return []
    name = server_name.strip()
    rows = (
        Campaign.query.filter(
            Campaign.servidor == name,
            Campaign.fecha_inicio >= datetime.now(),
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )
    return [{"id": c.id, "nombre": c.nombre, "fecha_inicio": c.fecha_inicio.strftime("%Y-%m-%d %H:%M:%S")} for c in rows]


def parse_campaign_id(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_campaigns(
    start_date=None,
    end_date=None,
    search=None,
    server=None,
    operacion=None,
    tipo=None,
    usuario=None,
    page=1,
    page_size=20,
) -> dict:
    try:
        query = Campaign.query

        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Campaign.fecha_inicio >= start_dt)
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            query = query.filter(Campaign.fecha_inicio <= end_dt)
        if search:
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Campaign.nombre.ilike(term),
                    Campaign.operacion.ilike(term),
                    Campaign.tipo.ilike(term),
                    Campaign.tipo_campana.ilike(term),
                    Campaign.usuario.ilike(term),
                    Campaign.consulta.ilike(term),
                    Campaign.descripcion.ilike(term),
                )
            )
        if server:
            query = query.filter(Campaign.servidor == server.strip())
        if operacion:
            query = query.filter(Campaign.operacion == operacion.strip())
        if tipo:
            query = query.filter(Campaign.tipo == tipo.strip())
        if usuario:
            query = query.filter(Campaign.usuario == usuario.strip())

        query = query.order_by(Campaign.fecha_inicio.desc(), Campaign.id.desc())
        pagination = query.paginate(page=page, per_page=page_size, error_out=False)
        campaigns = [_campaign_to_dict(c) for c in pagination.items]

        return {
            "success": True,
            "campaigns": campaigns,
            "total": pagination.total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(pagination.pages, 1),
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "campaigns": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
        }


def get_campaign(campaign_id: int) -> dict | None:
    campaign = Campaign.query.get(campaign_id)
    return _campaign_to_dict(campaign) if campaign else None


def build_api_invocation_base(campaign_dict: dict) -> dict:
    """
    Parámetros base para invocar APIs Wolkvox desde una campaña.
    Incluye campaign_id cuando la campaña tiene flujo de proceso asociado.
    """
    payload: dict = {
        "server": campaign_dict.get("servidor") or "",
        "servidor": campaign_dict.get("servidor") or "",
    }
    flujo_id = (campaign_dict.get("flujo_proceso_id") or "").strip()
    if flujo_id:
        payload["campaign_id"] = flujo_id
    return payload


def get_all_campaigns() -> list[dict]:
    campaigns = Campaign.query.order_by(Campaign.id.asc()).all()
    return [_campaign_to_dict(c) for c in campaigns]


def create_campaign(data: dict) -> dict:
    try:
        campaign = Campaign(
            nombre=data["nombre"],
            operacion=data["operacion"],
            tipo=data["tipo"],
            tipo_campana=data.get("tipo_campana") or "",
            flujo_proceso_id=data.get("flujo_proceso_id") or "",
            fecha_inicio=data["fecha_inicio"],
            consulta=data["consulta"],
            descripcion=data.get("descripcion") or "",
            usuario=data["usuario"],
            servidor=data.get("servidor") or data.get("server") or "",
            total_clientes=int(data.get("total_clientes") or 0),
            clientes_llamados=int(data.get("clientes_llamados") or 0),
            clientes_contactados=int(data.get("clientes_contactados") or 0),
        )
        db.session.add(campaign)
        db.session.commit()
        return {
            "success": True,
            "message": "Campaña guardada correctamente.",
            "campaign": _campaign_to_dict(campaign),
        }
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}


def update_campaign(campaign_id: int, data: dict) -> dict:
    try:
        campaign = Campaign.query.get(campaign_id)
        if not campaign:
            return {"success": False, "message": "No se encontró la campaña."}

        campaign.nombre = data["nombre"]
        campaign.operacion = data["operacion"]
        campaign.tipo = data["tipo"]
        campaign.tipo_campana = data.get("tipo_campana") or ""
        campaign.flujo_proceso_id = data.get("flujo_proceso_id") or ""
        campaign.fecha_inicio = data["fecha_inicio"]
        campaign.consulta = data["consulta"]
        campaign.descripcion = data.get("descripcion") or ""
        campaign.usuario = data["usuario"]
        campaign.servidor = data.get("servidor") or data.get("server") or ""
        campaign.total_clientes = int(data.get("total_clientes") or 0)
        campaign.clientes_llamados = int(data.get("clientes_llamados") or 0)
        campaign.clientes_contactados = int(data.get("clientes_contactados") or 0)

        db.session.commit()
        return {
            "success": True,
            "message": "Campaña actualizada correctamente.",
            "campaign": _campaign_to_dict(campaign),
        }
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}


def delete_campaign(campaign_id: int) -> dict:
    try:
        campaign = Campaign.query.get(campaign_id)
        if not campaign:
            return {"success": False, "message": "No se encontró la campaña."}

        db.session.delete(campaign)
        db.session.commit()
        return {"success": True, "message": "Campaña eliminada correctamente."}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}
