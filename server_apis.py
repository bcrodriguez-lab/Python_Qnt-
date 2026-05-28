"""
Asignación de APIs activas por servidor (persistencia en config.json).
"""

import json
import os
import threading

_lock = threading.Lock()
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
SERVER_APIS_KEY = "server_apis"


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


def _server_names(cfg: dict) -> list[str]:
    return [s.get("name", "").strip() for s in cfg.get("servers", []) if s.get("name")]


def _api_names(cfg: dict) -> list[str]:
    return [a.get("name", "").strip() for a in cfg.get("apis", []) if a.get("name")]


def _sync_server_apis(cfg: dict) -> dict:
    """Alinea server_apis con servidores y APIs actuales; conserva valores existentes."""
    servers = _server_names(cfg)
    apis = _api_names(cfg)
    raw = cfg.get(SERVER_APIS_KEY)
    if not isinstance(raw, dict):
        raw = {}

    synced: dict[str, dict[str, bool]] = {}
    for server in servers:
        prev_server = raw.get(server, {})
        if not isinstance(prev_server, dict):
            prev_server = {}
        synced[server] = {}
        for api in apis:
            synced[server][api] = bool(prev_server.get(api, False))

    cfg[SERVER_APIS_KEY] = synced
    return synced


def load_assignment_matrix() -> dict:
    """Matriz servidor × API para la interfaz."""
    try:
        with _lock:
            cfg = _read_config()
            matrix = _sync_server_apis(cfg)
            _write_config(cfg)

        servers = [{"name": n} for n in _server_names(cfg)]
        apis = [{"name": n} for n in _api_names(cfg)]
        return {
            "success": True,
            "servers": servers,
            "apis": apis,
            "assignments": matrix,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc),
            "servers": [],
            "apis": [],
            "assignments": {},
        }


def is_server_api_active(server: str, api: str) -> bool:
    """Indica si una API está activa para un servidor en la matriz server_apis."""
    server = (server or "").strip()
    api = (api or "").strip()
    if not server or not api:
        return False
    try:
        with _lock:
            cfg = _read_config()
            matrix = _sync_server_apis(cfg)
        return bool(matrix.get(server, {}).get(api, False))
    except Exception:
        return False


def set_server_api_active(server: str, api: str, active: bool) -> dict:
    """Activa o desactiva una API en un servidor."""
    server = (server or "").strip()
    api = (api or "").strip()
    if not server or not api:
        return {"success": False, "message": "Servidor y API son obligatorios."}

    try:
        with _lock:
            cfg = _read_config()
            if server not in _server_names(cfg):
                return {"success": False, "message": f"No existe el servidor '{server}'."}
            if api not in _api_names(cfg):
                return {"success": False, "message": f"No existe la API '{api}'."}

            matrix = _sync_server_apis(cfg)
            matrix[server][api] = bool(active)
            cfg[SERVER_APIS_KEY] = matrix
            _write_config(cfg)

        estado = "activada" if active else "desactivada"
        return {
            "success": True,
            "message": f"API '{api}' {estado} en servidor '{server}'.",
            "active": bool(active),
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def on_server_renamed(old_name: str, new_name: str) -> None:
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return
    try:
        with _lock:
            cfg = _read_config()
            raw = cfg.get(SERVER_APIS_KEY, {})
            if not isinstance(raw, dict) or old_name not in raw:
                _sync_server_apis(cfg)
                _write_config(cfg)
                return
            raw[new_name] = raw.pop(old_name)
            cfg[SERVER_APIS_KEY] = raw
            _sync_server_apis(cfg)
            _write_config(cfg)
    except Exception:
        pass


def on_server_deleted(server_name: str) -> None:
    server_name = (server_name or "").strip()
    if not server_name:
        return
    try:
        with _lock:
            cfg = _read_config()
            raw = cfg.get(SERVER_APIS_KEY, {})
            if isinstance(raw, dict) and server_name in raw:
                del raw[server_name]
                cfg[SERVER_APIS_KEY] = raw
            _sync_server_apis(cfg)
            _write_config(cfg)
    except Exception:
        pass


def on_api_renamed(old_name: str, new_name: str) -> None:
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return
    try:
        with _lock:
            cfg = _read_config()
            raw = cfg.get(SERVER_APIS_KEY, {})
            if not isinstance(raw, dict):
                return
            for server, apis_map in raw.items():
                if isinstance(apis_map, dict) and old_name in apis_map:
                    apis_map[new_name] = apis_map.pop(old_name)
            cfg[SERVER_APIS_KEY] = raw
            _sync_server_apis(cfg)
            _write_config(cfg)
    except Exception:
        pass


def on_api_deleted(api_name: str) -> None:
    api_name = (api_name or "").strip()
    if not api_name:
        return
    try:
        with _lock:
            cfg = _read_config()
            raw = cfg.get(SERVER_APIS_KEY, {})
            if isinstance(raw, dict):
                for server, apis_map in raw.items():
                    if isinstance(apis_map, dict) and api_name in apis_map:
                        del apis_map[api_name]
                cfg[SERVER_APIS_KEY] = raw
            _sync_server_apis(cfg)
            _write_config(cfg)
    except Exception:
        pass
