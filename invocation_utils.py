"""
Resolución del prefijo de URL (servidor) para invocaciones de API.
"""

from __future__ import annotations

from servers import get_config_servidor_default, get_server_url_prefix

_SERVER_NAME_KEYS = ("server", "nombre_servidor", "server_name")


def looks_like_url(value: str) -> bool:
    text = (value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def get_server_name(payload: dict | None, api_config: dict | None = None) -> str:
    """Nombre del servidor registrado (no la URL base)."""
    payload = payload if isinstance(payload, dict) else {}
    api_config = api_config if isinstance(api_config, dict) else {}

    for key in _SERVER_NAME_KEYS:
        value = (payload.get(key) or api_config.get(key) or "").strip()
        if value and not looks_like_url(value):
            return value

    servidor = (payload.get("servidor") or "").strip()
    if servidor and not looks_like_url(servidor) and get_server_url_prefix(servidor):
        return servidor

    return ""


def resolve_servidor_prefix(payload: dict | None, api_config: dict | None = None) -> str:
    """
    Prefijo de URL para {{servidor}} en la definición del API.

    Orden: servidor (URL explícita) → server (nombre) → servidor (nombre) → config.json servidor.
    """
    payload = payload if isinstance(payload, dict) else {}
    api_config = api_config if isinstance(api_config, dict) else {}

    servidor = (payload.get("servidor") or api_config.get("servidor") or "").strip()
    if servidor and looks_like_url(servidor):
        return servidor.rstrip("/")

    server_name = get_server_name(payload, api_config)
    if server_name:
        prefix = get_server_url_prefix(server_name)
        if prefix:
            return prefix.rstrip("/")

    if servidor:
        prefix = get_server_url_prefix(servidor)
        if prefix:
            return prefix.rstrip("/")

    default_prefix = get_config_servidor_default()
    if default_prefix:
        return default_prefix.rstrip("/")

    return ""


def enrich_invocation_payload(
    payload: dict | None, api_config: dict | None = None
) -> dict:
    """
    Completa payload de invocación con servidor (URL base) y server (nombre).

    Permite una sola definición de API con {{servidor}} en la URL para distintos servidores.
    """
    enriched = dict(payload or {})
    api_config = api_config if isinstance(api_config, dict) else {}

    server_name = get_server_name(enriched, api_config)
    prefix = resolve_servidor_prefix(enriched, api_config)

    if prefix:
        enriched["servidor"] = prefix
    if server_name:
        enriched.setdefault("server", server_name)

    return enriched
