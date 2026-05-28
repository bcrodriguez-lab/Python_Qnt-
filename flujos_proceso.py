"""
Flujos de proceso Wolkvox (persistencia en config.json).
El campo id se envía a las APIs como campaign_id.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from database import Campaign, db

_lock = threading.Lock()
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
FLUJOS_KEY = "flujos_proceso"


def _ensure_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump({}, handle, indent=2, ensure_ascii=False)


def _read_config() -> dict:
    _ensure_config()
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, ensure_ascii=False)


def _server_names(cfg: dict) -> list[str]:
    return [s.get("name", "").strip() for s in cfg.get("servers", []) if s.get("name")]


def _normalize_flujo(entry: dict) -> dict | None:
    if not isinstance(entry, dict):
        return None
    flujo_id = str(entry.get("id") or "").strip()
    nombre = str(entry.get("nombre") or "").strip()
    servidor = str(entry.get("servidor") or "").strip()
    if not flujo_id or not nombre or not servidor:
        return None
    return {
        "id": flujo_id,
        "nombre": nombre,
        "servidor": servidor,
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }


def load_flujos_proceso() -> dict:
    """Lista flujos de proceso desde config.json."""
    try:
        cfg = _read_config()
        raw = cfg.get(FLUJOS_KEY, [])
        if not isinstance(raw, list):
            raw = []
        flujos = []
        for item in raw:
            normalized = _normalize_flujo(item)
            if normalized:
                flujos.append(normalized)
        servers = set(_server_names(cfg))
        for flujo in flujos:
            flujo["server_exists"] = flujo["servidor"] in servers
            flujo["deletable"] = not _has_campaigns_for_flujo(flujo["id"])
        return {"success": True, "flujos": flujos, "servers": sorted(servers)}
    except Exception as exc:
        return {"success": False, "message": str(exc), "flujos": [], "servers": []}


def get_flujo(flujo_id: str) -> dict | None:
    flujo_id = str(flujo_id or "").strip()
    if not flujo_id:
        return None
    result = load_flujos_proceso()
    if not result.get("success"):
        return None
    for flujo in result.get("flujos", []):
        if flujo.get("id") == flujo_id:
            return dict(flujo)
    return None


def list_flujos_by_server(server_name: str) -> list[dict]:
    """Flujos filtrados por servidor (para lista desplegable en campañas)."""
    server_name = (server_name or "").strip()
    if not server_name:
        return []
    result = load_flujos_proceso()
    if not result.get("success"):
        return []
    return [
        {
            "id": f["id"],
            "nombre": f["nombre"],
            "label": f"{f['id']} - {f['nombre']}",
        }
        for f in result.get("flujos", [])
        if f.get("servidor") == server_name
    ]


def save_flujo(
    flujo_id: str,
    nombre: str,
    servidor: str,
    original_id: str | None = None,
) -> dict:
    """Crea o actualiza un flujo de proceso."""
    flujo_id = str(flujo_id or "").strip()
    nombre = (nombre or "").strip()
    servidor = (servidor or "").strip()
    original_id = (original_id or "").strip() or flujo_id

    if not flujo_id:
        return {"success": False, "message": "El id del flujo es obligatorio."}
    if not nombre:
        return {"success": False, "message": "El nombre del flujo es obligatorio."}
    if not servidor:
        return {"success": False, "message": "El servidor es obligatorio."}

    try:
        with _lock:
            cfg = _read_config()
            servers = _server_names(cfg)
            if servidor not in servers:
                return {
                    "success": False,
                    "message": f"El servidor '{servidor}' no está registrado.",
                }

            flujos = cfg.get(FLUJOS_KEY, [])
            if not isinstance(flujos, list):
                flujos = []

            existing_idx = next(
                (i for i, f in enumerate(flujos) if str(f.get("id", "")).strip() == original_id),
                None,
            )

            if existing_idx is None:
                if any(str(f.get("id", "")).strip() == flujo_id for f in flujos):
                    return {
                        "success": False,
                        "message": f"Ya existe un flujo con el id '{flujo_id}'.",
                    }
                flujos.append({
                    "id": flujo_id,
                    "nombre": nombre,
                    "servidor": servidor,
                    "created_at": datetime.utcnow().isoformat(),
                })
            else:
                if flujo_id != original_id and any(
                    str(f.get("id", "")).strip() == flujo_id
                    for i, f in enumerate(flujos)
                    if i != existing_idx
                ):
                    return {
                        "success": False,
                        "message": f"Ya existe un flujo con el id '{flujo_id}'.",
                    }

                entry = flujos[existing_idx]
                old_id = str(entry.get("id", "")).strip()
                entry["id"] = flujo_id
                entry["nombre"] = nombre
                entry["servidor"] = servidor
                entry["updated_at"] = datetime.utcnow().isoformat()

                if flujo_id != old_id:
                    _rename_flujo_in_campaigns(old_id, flujo_id)

            cfg[FLUJOS_KEY] = flujos
            _write_config(cfg)

        return {"success": True, "message": "Flujo de proceso guardado correctamente."}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def delete_flujo(flujo_id: str) -> dict:
    """Elimina un flujo de proceso."""
    flujo_id = str(flujo_id or "").strip()
    if not flujo_id:
        return {"success": False, "message": "El id del flujo es obligatorio."}

    if _has_campaigns_for_flujo(flujo_id):
        return {
            "success": False,
            "message": (
                f"No se puede eliminar el flujo '{flujo_id}': "
                "tiene campañas asociadas."
            ),
        }

    try:
        with _lock:
            cfg = _read_config()
            flujos = cfg.get(FLUJOS_KEY, [])
            if not isinstance(flujos, list):
                flujos = []
            new_flujos = [f for f in flujos if str(f.get("id", "")).strip() != flujo_id]
            if len(new_flujos) == len(flujos):
                return {"success": False, "message": "No se encontró el flujo de proceso."}
            cfg[FLUJOS_KEY] = new_flujos
            _write_config(cfg)
        return {"success": True, "message": "Flujo de proceso eliminado correctamente."}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def count_flujos_for_server(server_name: str) -> int:
    server_name = (server_name or "").strip()
    if not server_name:
        return 0
    result = load_flujos_proceso()
    if not result.get("success"):
        return 0
    return sum(1 for f in result.get("flujos", []) if f.get("servidor") == server_name)


def _has_campaigns_for_flujo(flujo_id: str) -> bool:
    flujo_id = str(flujo_id or "").strip()
    if not flujo_id:
        return False
    return (
        Campaign.query.filter(Campaign.flujo_proceso_id == flujo_id).first() is not None
    )


def _rename_flujo_in_campaigns(old_id: str, new_id: str) -> None:
    Campaign.query.filter(Campaign.flujo_proceso_id == old_id).update(
        {Campaign.flujo_proceso_id: new_id},
        synchronize_session=False,
    )
    db.session.commit()


def validate_flujo_for_campaign(
    flujo_proceso_id: str, servidor: str, tipo_campana: str
) -> tuple[str | None, str | None]:
    """
    Valida flujo para campaña WhatsApp/Llamada.
    Retorna (flujo_id_normalizado, mensaje_error).
    """
    from campaigns import requires_flujo_proceso

    if not requires_flujo_proceso(tipo_campana):
        return "", None

    flujo_id = str(flujo_proceso_id or "").strip()
    if not flujo_id:
        return None, "Debe seleccionar un flujo de proceso (id Wolkvox)."

    flujo = get_flujo(flujo_id)
    if not flujo:
        return None, f"No se encontró el flujo de proceso con id '{flujo_id}'."

    servidor = (servidor or "").strip()
    if servidor and flujo.get("servidor") != servidor:
        return None, (
            f"El flujo '{flujo_id}' pertenece al servidor '{flujo.get('servidor')}', "
            f"no a '{servidor}'."
        )

    return flujo_id, None
