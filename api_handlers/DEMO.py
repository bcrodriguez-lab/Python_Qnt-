"""
API de referencia (metodo POST).

Copie este archivo en api_handlers/ con otro nombre (ej. MI_API.py)
y registre la API en el modulo /config-apis indicando:
  - archivo: nombre del modulo sin .py (ej. MI_API)
  - metodo: nombre de la funcion (ej. post)
"""

from __future__ import annotations

import requests


def post(api_config: dict, payload: dict | None = None) -> dict:
    """
    Ejecuta una peticion POST hacia la URL configurada.

    Args:
        api_config: registro de config.json (name, url, token, ...)
        payload: cuerpo JSON opcional para enviar

    Returns:
        dict con success, message y datos de respuesta
    """
    url = (api_config or {}).get("url", "").strip()
    if not url:
        return {"success": False, "message": "La URL de la API no esta configurada."}

    headers = {"Content-Type": "application/json"}
    token = (api_config or {}).get("token", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = payload if isinstance(payload, dict) else {}

    try:
        response = requests.post(url, json=body, headers=headers, timeout=30)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}

        if response.ok:
            return {
                "success": True,
                "message": f"DEMO POST exitoso (HTTP {response.status_code})",
                "status": response.status_code,
                "data": data,
            }

        return {
            "success": False,
            "message": f"DEMO POST fallo (HTTP {response.status_code})",
            "status": response.status_code,
            "data": data,
        }
    except requests.Timeout:
        return {"success": False, "message": "Timeout al invocar la API DEMO."}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
