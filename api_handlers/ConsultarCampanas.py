"""
Wolkvox — Consultar campañas.

Registro en /config-apis:
  - archivo: ConsultarCampanas
  - metodo: get

Invocación (payload):
  - servidor: prefijo URL (https://host) — se resuelve desde server (nombre) o config.json
  - server: nombre del servidor registrado (opcional; usa su URL y token)
  - wolkvox-token (o wolkvox_token): token enviado en el header
  - Cualquier {{nombre_variable}} en la URL configurada: mismo nombre en el payload
"""

from __future__ import annotations

import requests

import api_handlers.Wolkvox_Carga_Clientes as _wolkvox
from api_handlers.wolkvox_utils import build_wolkvox_headers, find_wolkvox_token


def get(api_config: dict, payload: dict | None = None) -> dict:
    """
    GET hacia Wolkvox con token en header y parámetros resueltos en la URL.

    Args:
        api_config: registro de config.json (name, url, ...)
        payload: parámetros de invocación del handler
    """
    payload = payload if isinstance(payload, dict) else {}
    base_url = (api_config or {}).get("url", "").strip()
    if not base_url:
        return {"success": False, "message": "La URL de la API no está configurada."}

    token = find_wolkvox_token(payload, api_config)
    if not token:
        return {
            "success": False,
            "message": (
                "El parámetro wolkvox-token es obligatorio en la invocación "
                "(o token del servidor / config global)."
            ),
        }

    missing_url_params = [
        name
        for name in _wolkvox.extract_url_placeholders(base_url)
        if not str(payload.get(name, "")).strip()
    ]
    if missing_url_params:
        return {
            "success": False,
            "message": (
                "Faltan parámetros de invocación para la URL: "
                f"{', '.join(missing_url_params)}"
            ),
        }

    try:
        url = _wolkvox.resolve_url_template(base_url, payload)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    headers = build_wolkvox_headers(token)

    try:
        response = requests.get(url, headers=headers, timeout=60)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:2000]}

        if response.ok:
            return {
                "success": True,
                "message": f"Consulta de campañas exitosa (HTTP {response.status_code})",
                "status": response.status_code,
                "url": url,
                "request_headers": headers,
                "data": data,
            }

        return {
            "success": False,
            "message": f"Consulta de campañas falló (HTTP {response.status_code})",
            "status": response.status_code,
            "url": url,
            "request_headers": headers,
            "data": data,
        }
    except requests.Timeout:
        return {"success": False, "message": "Timeout al consultar campañas Wolkvox."}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
