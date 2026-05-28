"""
Wolkvox — Borrar clientes de una campaña.

Registro en /config-apis:
  - archivo: BorrarClientesCampana
  - metodo: delete

Invocación (payload):
  - servidor: prefijo URL (https://host) — se resuelve desde server (nombre) o config.json
  - server: nombre del servidor registrado (opcional; usa su URL y token)
  - wolkvox-token (o wolkvox_token): token enviado en el header
  - campaign_id: identificador de la campaña (obligatorio)
  - Cualquier {{nombre_variable}} en la URL configurada: mismo nombre en el payload
"""

from __future__ import annotations

import requests

import api_handlers.Wolkvox_Carga_Clientes as _wolkvox
import api_handlers.wolkvox_utils as _wolkvox_auth


def _extract_campaign_id(payload: dict) -> str:
    value = payload.get("campaign_id")
    if value is not None and str(value).strip():
        return str(value).strip()
    return ""


def delete(api_config: dict, payload: dict | None = None) -> dict:
    """
    DELETE hacia Wolkvox para borrar los clientes de una campaña.

    Args:
        api_config: registro de config.json (name, url, ...)
        payload: parámetros de invocación del handler
    """
    payload = payload if isinstance(payload, dict) else {}
    base_url = (api_config or {}).get("url", "").strip()
    if not base_url:
        return {"success": False, "message": "La URL de la API no está configurada."}

    token = _wolkvox_auth.find_wolkvox_token(payload, api_config)
    if not token:
        return {
            "success": False,
            "message": (
                "El parámetro wolkvox-token es obligatorio en la invocación "
                "(o token del servidor / config global)."
            ),
        }

    campaign_id = _extract_campaign_id(payload)
    if not campaign_id:
        return {
            "success": False,
            "message": "El parámetro campaign_id es obligatorio en la invocación.",
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

    headers = _wolkvox_auth.build_wolkvox_headers(token)

    try:
        response = requests.delete(url, headers=headers, timeout=60)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:2000]}

        if response.ok:
            return {
                "success": True,
                "message": (
                    f"Clientes de campaña borrados correctamente "
                    f"(HTTP {response.status_code})"
                ),
                "status": response.status_code,
                "url": url,
                "request_headers": headers,
                "campaign_id": campaign_id,
                "data": data,
            }

        return {
            "success": False,
            "message": (
                f"No se pudieron borrar los clientes de la campaña "
                f"(HTTP {response.status_code})"
            ),
            "status": response.status_code,
            "url": url,
            "request_headers": headers,
            "campaign_id": campaign_id,
            "data": data,
        }
    except requests.Timeout:
        return {
            "success": False,
            "message": "Timeout al invocar Borrar clientes campaña Wolkvox.",
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}
