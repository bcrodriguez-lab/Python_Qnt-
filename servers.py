import json
import os
import threading
from datetime import datetime

from campaigns import has_pending_campaigns_for_server, pending_campaigns_for_server

_lock = threading.Lock()
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _ensure_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2, ensure_ascii=False)


def _read_config() -> dict:
    _ensure_config()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_servers():
    """Carga la lista de servidores desde `config.json`."""
    try:
        cfg = _read_config()
        servers = cfg.get("servers", [])
        options = cfg.get("options", {})
        for server in servers:
            name = server.get("name", "")
            server["deletable"] = not has_pending_campaigns_for_server(name)
        return {"success": True, "servers": servers, "options": options}
    except Exception as e:
        return {"success": False, "message": str(e), "servers": []}


def get_server(name: str) -> dict | None:
    if not name:
        return None
    name = name.strip()
    try:
        cfg = _read_config()
        for server in cfg.get("servers", []):
            if server.get("name") == name:
                return dict(server)
        return None
    except Exception:
        return None


def get_server_url_prefix(server_name: str) -> str:
    """URL base del servidor (prefijo para armar la URL del API)."""
    server = get_server(server_name)
    if not server:
        return ""
    return (server.get("url") or "").strip().rstrip("/")


def get_config_servidor_default() -> str:
    """Valor de config.json → servidor (pruebas manuales de API)."""
    try:
        cfg = _read_config()
    except Exception:
        return ""
    for key in ("servidor", "Servidor"):
        value = cfg.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().rstrip("/")
    return ""


def save_server(name: str, url: str, token: str, original_name: str = None) -> dict:
    """Guarda o actualiza un servidor. Si original_name difiere, renombra el servidor."""
    if not name or not url:
        return {"success": False, "message": "El nombre y la URL son obligatorios."}

    name = name.strip()
    url = url.strip()
    token = token.strip() if token else ""
    original_name = (original_name or "").strip() or name

    try:
        with _lock:
            cfg = _read_config()
            servers = cfg.get("servers", [])

            existing_idx = next(
                (i for i, s in enumerate(servers) if s.get("name") == original_name),
                None,
            )
            if existing_idx is None:
                if any(s.get("name") == name for s in servers):
                    return {"success": False, "message": "Ya existe un servidor con ese nombre."}
                servers.append({
                    "name": name,
                    "url": url,
                    "token": token,
                    "created_at": datetime.utcnow().isoformat(),
                })
            else:
                if name != original_name and any(
                    s.get("name") == name for i, s in enumerate(servers) if i != existing_idx
                ):
                    return {"success": False, "message": "Ya existe un servidor con ese nombre."}

                entry = servers[existing_idx]
                entry["name"] = name
                entry["url"] = url
                entry["token"] = token
                entry["updated_at"] = datetime.utcnow().isoformat()

                if name != original_name:
                    _rename_server_in_campaigns(original_name, name)
                    from server_apis import on_server_renamed
                    on_server_renamed(original_name, name)

            cfg["servers"] = servers
            _write_config(cfg)

        return {"success": True, "message": "Servidor guardado correctamente.", "servers": servers}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _rename_server_in_campaigns(old_name: str, new_name: str) -> None:
    from database import Campaign, db

    Campaign.query.filter(Campaign.servidor == old_name).update(
        {Campaign.servidor: new_name}, synchronize_session=False
    )
    db.session.commit()


def delete_server(name: str) -> dict:
    """Elimina un servidor si no tiene campañas pendientes de ejecución."""
    if not name:
        return {"success": False, "message": "El nombre del servidor es obligatorio."}

    name = name.strip()
    from flujos_proceso import count_flujos_for_server

    if count_flujos_for_server(name) > 0:
        return {
            "success": False,
            "message": (
                f"No se puede eliminar el servidor '{name}': "
                "tiene flujos de proceso asociados."
            ),
        }
    if has_pending_campaigns_for_server(name):
        pending = pending_campaigns_for_server(name)
        names = ", ".join(f"#{c['id']} {c['nombre']}" for c in pending[:3])
        extra = f" (+{len(pending) - 3} más)" if len(pending) > 3 else ""
        return {
            "success": False,
            "message": (
                f"No se puede eliminar el servidor '{name}': tiene campañas programadas "
                f"que aún no se han ejecutado ({names}{extra})."
            ),
        }

    try:
        with _lock:
            cfg = _read_config()
            servers = cfg.get("servers", [])
            new_servers = [s for s in servers if s.get("name") != name]
            if len(new_servers) == len(servers):
                return {"success": False, "message": "No se encontró el servidor."}

            cfg["servers"] = new_servers
            _write_config(cfg)

        from server_apis import on_server_deleted
        on_server_deleted(name)

        return {"success": True, "message": "Servidor eliminado correctamente.", "servers": new_servers}
    except Exception as e:
        return {"success": False, "message": str(e)}
