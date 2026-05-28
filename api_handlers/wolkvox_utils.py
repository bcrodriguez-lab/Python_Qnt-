"""
Utilidades compartidas para handlers Wolkvox (token en header).
"""

from __future__ import annotations

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

_TOKEN_KEYS = ("wolkvox-token", "wolkvox_token", "token")


def _normalize_key(key: str) -> str:
    return str(key).lower().replace("_", "-")


def _read_config_file() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _lookup_server_token(server_name: str) -> str:
    name = (server_name or "").strip()
    if not name:
        return ""
    for server in _read_config_file().get("servers", []):
        if (server.get("name") or "").strip() == name:
            value = server.get("token")
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _lookup_api_token(api_name: str) -> str:
    name = (api_name or "").strip()
    if not name:
        return ""
    for api in _read_config_file().get("apis", []):
        if (api.get("name") or "").strip() == name:
            value = api.get("token")
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _lookup_global_token() -> str:
    token = _read_config_file().get("wolkvox-token")
    if token is not None and str(token).strip():
        return str(token).strip()
    return ""


def find_wolkvox_token(payload: dict | None, api_config: dict | None = None) -> str:
    """
    Resuelve el token Wolkvox para el header.

    Orden: payload (wolkvox-token / wolkvox_token / token, sin distinguir mayúsculas),
    api_config, token de la API en config.json por nombre, servidor en payload,
    token global wolkvox-token en config.json.
    """
    payload = payload if isinstance(payload, dict) else {}
    api_config = api_config if isinstance(api_config, dict) else {}

    wanted = {_normalize_key(key) for key in _TOKEN_KEYS}
    for key, value in payload.items():
        if _normalize_key(key) in wanted:
            text = str(value).strip() if value is not None else ""
            if text:
                return text

    for key in _TOKEN_KEYS:
        value = api_config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    api_name = (api_config.get("name") or payload.get("api_name") or "").strip()
    token = _lookup_api_token(api_name)
    if token:
        return token

    server_name = (
        payload.get("server")
        or payload.get("nombre_servidor")
        or payload.get("server_name")
        or api_config.get("server")
        or api_config.get("nombre_servidor")
        or api_config.get("server_name")
        or ""
    )
    token = _lookup_server_token(str(server_name).strip())
    if token:
        return token

    return _lookup_global_token()


def build_wolkvox_headers(token: str, *, json_body: bool = False) -> dict[str, str]:
    """Header HTTP con nombre exacto wolkvox-token (requerido por Wolkvox)."""
    headers = {"wolkvox-token": token}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers
