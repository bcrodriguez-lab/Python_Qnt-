"""
Carga dinamica de handlers en api_handlers/.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

from invocation_utils import enrich_invocation_payload

_URL_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

_INVOCATION_META_KEYS = frozenset({
    "wolkvox-token",
    "wolkvox_token",
    "token",
    "servidor",
    "server",
    "nombre_servidor",
    "server_name",
})

BASE_DIR = Path(__file__).resolve().parent
API_HANDLERS_DIR = BASE_DIR / "api_handlers"

_FILE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def list_handler_files() -> list[str]:
    """Lista modulos disponibles en api_handlers/ (sin .py)."""
    if not API_HANDLERS_DIR.exists():
        return []
    return sorted(
        p.stem
        for p in API_HANDLERS_DIR.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    )


def list_handler_methods(archivo: str) -> list[str]:
    """Lista funciones publicas del modulo (no empiezan por _)."""
    archivo = (archivo or "").strip()
    if not archivo or not _FILE_NAME_RE.match(archivo):
        return []
    path = API_HANDLERS_DIR / f"{archivo}.py"
    if not path.exists():
        return []
    try:
        module = importlib.import_module(f"api_handlers.{archivo}")
        return sorted(
            name
            for name in dir(module)
            if not name.startswith("_") and callable(getattr(module, name))
        )
    except Exception:
        return []


def validate_handler(archivo: str, metodo: str) -> dict:
    """Valida que existan el archivo y el metodo en api_handlers/."""
    archivo = (archivo or "").strip()
    metodo = (metodo or "").strip().lower()

    if not archivo:
        return {"success": False, "message": "El nombre del archivo es obligatorio."}
    if not _FILE_NAME_RE.match(archivo):
        return {
            "success": False,
            "message": "El archivo solo puede contener letras, numeros y guion bajo, y debe empezar con letra.",
        }

    path = API_HANDLERS_DIR / f"{archivo}.py"
    if not path.exists():
        return {
            "success": False,
            "message": f"No existe api_handlers/{archivo}.py. Cree el archivo antes de registrar la API.",
        }

    if not metodo:
        return {"success": False, "message": "El metodo de la API es obligatorio."}

    methods = list_handler_methods(archivo)
    if metodo not in methods:
        return {
            "success": False,
            "message": f"El metodo '{metodo}' no existe en {archivo}.py. Disponibles: {', '.join(methods) or '(ninguno)'}",
        }

    return {"success": True, "message": "Handler valido.", "methods": methods}


def extract_url_placeholders(url_template: str) -> list[str]:
    """Variables {{nombre}} en la URL configurada de la API."""
    return list(dict.fromkeys(_URL_PLACEHOLDER_RE.findall(url_template or "")))


def build_request_body_preview(
    archivo: str,
    payload: dict,
    url_template: str = "",
    api_config: dict | None = None,
) -> dict | list:
    """
    Construye el cuerpo HTTP que enviará el handler según archivo y payload de invocación.
    """
    archivo = (archivo or "").strip()
    payload = enrich_invocation_payload(payload if isinstance(payload, dict) else {}, api_config)
    url_vars = set(extract_url_placeholders(url_template))

    if archivo == "Wolkvox_Carga_Clientes":
        from api_handlers.Wolkvox_Carga_Clientes import transform_datos_clientes

        if "datos_clientes" not in payload:
            return {"_error": "Incluya datos_clientes en los parámetros (CSV, JSON o lista)."}
        return transform_datos_clientes(payload["datos_clientes"])

    if archivo in ("ConsultarCampanas", "PararCampana", "BorrarClientesCampana"):
        return {}

    if "body" in payload:
        return payload["body"]

    skip = _INVOCATION_META_KEYS | url_vars
    body_keys = {k: v for k, v in payload.items() if k not in skip}
    return body_keys if body_keys else payload


def build_request_url_preview(
    url_template: str, payload: dict, api_config: dict | None = None
) -> str:
    """URL final tras reemplazar {{variables}} (incluye {{servidor}})."""
    url_template = (url_template or "").strip()
    if not url_template:
        return ""
    payload = enrich_invocation_payload(payload if isinstance(payload, dict) else {}, api_config)
    if "{{" not in url_template:
        return url_template
    from api_handlers.Wolkvox_Carga_Clientes import resolve_url_template

    return resolve_url_template(url_template, payload)


def build_request_headers_preview(
    archivo: str, payload: dict, api_config: dict | None = None
) -> dict[str, str]:
    """Headers HTTP que enviará el handler (p. ej. wolkvox-token)."""
    archivo = (archivo or "").strip()
    payload = enrich_invocation_payload(payload if isinstance(payload, dict) else {}, api_config)
    _wolkvox_token_handlers = (
        "ConsultarCampanas",
        "Wolkvox_Carga_Clientes",
        "PararCampana",
        "BorrarClientesCampana",
    )
    if archivo not in _wolkvox_token_handlers:
        return {}
    from api_handlers.wolkvox_utils import build_wolkvox_headers, find_wolkvox_token

    token = find_wolkvox_token(payload, api_config or {})
    if not token:
        return {}
    json_body = archivo == "Wolkvox_Carga_Clientes"
    return build_wolkvox_headers(token, json_body=json_body)


def invoke_handler(archivo: str, metodo: str, api_config: dict, payload: dict | None = None) -> dict:
    """Invoca dinamicamente api_handlers.<archivo>.<metodo>(api_config, payload)."""
    check = validate_handler(archivo, metodo)
    if not check.get("success"):
        return check

    archivo = archivo.strip()
    metodo = metodo.strip().lower()
    payload = enrich_invocation_payload(payload or {}, api_config or {})

    try:
        module = importlib.import_module(f"api_handlers.{archivo}")
        func = getattr(module, metodo)
        result = func(api_config=api_config or {}, payload=payload)
        if isinstance(result, dict):
            return result
        return {"success": True, "message": "Ejecutado correctamente.", "data": result}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
