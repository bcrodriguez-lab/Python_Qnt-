"""Datos agregados para el tablero principal."""

from datetime import datetime

from database import Campaign
from servers import load_servers


def _today_range(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _campaign_estado(campaign: Campaign, now: datetime) -> tuple[str, str]:
    """Devuelve (codigo, etiqueta) para la columna Estado del tablero."""
    clientes = int(campaign.total_clientes or 0)
    llamados = int(campaign.clientes_llamados or 0)
    faltantes = max(clientes - llamados, 0)

    if campaign.fecha_inicio and campaign.fecha_inicio >= now and not campaign.ejecutada:
        return "programada", "Programada"
    if campaign.ejecutada and clientes > 0 and faltantes <= 0:
        return "terminada", "Terminada"
    if campaign.ejecutada:
        return "en_curso", "En curso"
    if campaign.fecha_inicio and campaign.fecha_inicio < now:
        return "pendiente", "Pendiente de ejecución"
    return "programada", "Programada"


def _campaign_row(campaign: Campaign, now: datetime) -> dict:
    clientes = int(campaign.total_clientes or 0)
    llamados = int(campaign.clientes_llamados or 0)
    contactados = int(campaign.clientes_contactados or 0)
    faltantes = max(clientes - llamados, 0)
    estado, estado_label = _campaign_estado(campaign, now)

    return {
        "id": campaign.id,
        "nombre": campaign.nombre,
        "servidor": campaign.servidor or "",
        "clientes": clientes,
        "llamados": llamados,
        "faltantes": faltantes,
        "contactados": contactados,
        "tipo_campana": campaign.tipo_campana or "",
        "programada": bool(campaign.fecha_inicio and campaign.fecha_inicio >= now),
        "ejecutada": bool(campaign.ejecutada),
        "estado": estado,
        "estado_label": estado_label,
        "fecha_inicio": (
            campaign.fecha_inicio.strftime("%Y-%m-%d %H:%M")
            if campaign.fecha_inicio
            else ""
        ),
    }


def get_dashboard_data() -> dict:
    servers_count = 0
    servers_result = load_servers()
    if servers_result.get("success"):
        servers_count = len(servers_result.get("servers", []))

    now = datetime.now()
    day_start, day_end = _today_range(now)
    campaigns = (
        Campaign.query.filter(
            Campaign.fecha_inicio.isnot(None),
            Campaign.fecha_inicio >= day_start,
            Campaign.fecha_inicio <= day_end,
        )
        .order_by(Campaign.fecha_inicio.asc(), Campaign.id.asc())
        .all()
    )
    campaigns_total = len(campaigns)
    campaigns_scheduled = sum(
        1 for c in campaigns if c.fecha_inicio and c.fecha_inicio >= now
    )

    sum_clientes = 0
    sum_llamados = 0
    sum_contactados = 0
    campaign_rows = []

    for c in campaigns:
        row = _campaign_row(c, now)
        sum_clientes += row["clientes"]
        sum_llamados += row["llamados"]
        sum_contactados += row["contactados"]
        campaign_rows.append(row)

    progress_percent = (
        round(100 * sum_llamados / sum_clientes, 1) if sum_clientes > 0 else 0
    )

    return {
        "servers_count": servers_count,
        "campaigns_total": campaigns_total,
        "campaigns_scheduled": campaigns_scheduled,
        "clients_total": sum_clientes,
        "clients_called": sum_llamados,
        "clients_contacted": sum_contactados,
        "clients_pending": max(sum_clientes - sum_llamados, 0),
        "progress_percent": progress_percent,
        "campaign_rows": campaign_rows,
    }


def refresh_dashboard_from_wolkvox() -> dict:
    """
    Consulta Wolkvox (API Consultar campañas) para las campañas de hoy con flujo
    y actualiza métricas en SQLite antes de devolver los datos del tablero.
    """
    from campaign_execution import requires_wolkvox_execution, refresh_campaign_status

    now = datetime.now()
    day_start, day_end = _today_range(now)
    campaigns = (
        Campaign.query.filter(
            Campaign.fecha_inicio.isnot(None),
            Campaign.fecha_inicio >= day_start,
            Campaign.fecha_inicio <= day_end,
        )
        .order_by(Campaign.fecha_inicio.asc(), Campaign.id.asc())
        .all()
    )

    refreshed = 0
    skipped = 0
    errors: list[str] = []

    for campaign in campaigns:
        if not requires_wolkvox_execution(campaign):
            skipped += 1
            continue
        outcome = refresh_campaign_status(campaign)
        if outcome.get("success"):
            refreshed += 1
        else:
            errors.append(
                f"{campaign.nombre}: {outcome.get('message', 'Error desconocido')}"
            )

    payload = get_dashboard_data()
    payload["wolkvox_refreshed"] = refreshed
    payload["wolkvox_skipped"] = skipped
    if errors:
        payload["wolkvox_errors"] = errors
    return payload
