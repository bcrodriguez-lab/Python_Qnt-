"""
Wolkvox — Carga de clientes a campaña.

Registro en /config-apis:
  - archivo: Wolkvox_Carga_Clientes
  - metodo: post

Invocación (payload):
  - servidor: prefijo URL (https://host) — se resuelve desde server (nombre) o config.json
  - server: nombre del servidor registrado (opcional; usa su URL y token)
  - wolkvox-token (o wolkvox_token): token enviado en el header
  - campaign_id: identificador de la campaña (obligatorio; va en la URL como {{campaign_id}})
  - Cualquier {{nombre_variable}} en la URL configurada: mismo nombre en el payload
  - datos_clientes: CSV, dict o list[dict] → JSON del cuerpo POST
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import OrderedDict
from pathlib import Path

import requests

from api_handlers.wolkvox_utils import build_wolkvox_headers, find_wolkvox_token

_URL_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

_SALIDA_EJEMPLO_PATH = Path(__file__).resolve().parent / "ejemplo_body_carga_cliente_salida.json"
_ENTRADA_EJEMPLO_PATH = Path(__file__).resolve().parent / "ejemplo_body_carga_cliente_entrada.csv"

_FALLBACK_CLIENT_FIELDS = (
    "customer_name",
    "customer_last_name",
    "id_type",
    "customer_id",
    "age",
    "gender",
    "country",
    "state",
    "city",
    "zone",
    "address",
    "opt1",
    "opt2",
    "opt3",
    "opt4",
    "opt5",
    "opt6",
    "opt7",
    "opt8",
    "opt9",
    "opt10",
    "opt11",
    "opt12",
    "tel1",
    "tel2",
    "tel3",
    "tel4",
    "tel5",
    "tel6",
    "tel7",
    "tel8",
    "tel9",
    "tel10",
    "tel_extra",
    "email",
    "recall_date",
    "recall_telephone",
)


def _load_client_fields_order() -> tuple[str, ...]:
    """Orden de columnas según ejemplo_body_carga_cliente_entrada.csv / salida.json."""
    try:
        with _ENTRADA_EJEMPLO_PATH.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
        if header:
            fields = tuple(cell.strip() for cell in header if cell is not None)
            if fields:
                return fields
    except Exception:
        pass
    try:
        with _SALIDA_EJEMPLO_PATH.open(encoding="utf-8") as handle:
            records = json.load(handle)
        if isinstance(records, list) and records and isinstance(records[0], dict):
            return tuple(records[0].keys())
    except Exception:
        pass
    return _FALLBACK_CLIENT_FIELDS


CLIENT_FIELDS: tuple[str, ...] = _load_client_fields_order()


def load_ejemplo_datos_clientes_csv() -> str:
    """Contenido del CSV de ejemplo (encabezado + fila de datos)."""
    try:
        return _ENTRADA_EJEMPLO_PATH.read_text(encoding="utf-8-sig").strip()
    except Exception:
        return ",".join(CLIENT_FIELDS)


def _normalize_cell(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_header_name(name: str) -> str:
    return _normalize_cell(name).lower()


def _is_header_row(cells: list[str]) -> bool:
    if not cells:
        return False
    first = _normalize_header_name(cells[0])
    if first == "customer_name":
        return True
    normalized = [_normalize_header_name(cell) for cell in cells[: len(CLIENT_FIELDS)]]
    expected = [_normalize_header_name(field) for field in CLIENT_FIELDS]
    return normalized == expected


def _row_to_client_record_from_cells(cells: list[str]) -> OrderedDict[str, str]:
    """
    Mapeo posicional: columna i del CSV → campo i (mismo orden que entrada/salida de ejemplo).
    """
    record: OrderedDict[str, str] = OrderedDict()
    for index, field in enumerate(CLIENT_FIELDS):
        value = cells[index] if index < len(cells) else ""
        record[field] = _normalize_cell(value)
    return record


def _row_to_client_record(row: dict) -> OrderedDict[str, str]:
    """Convierte un dict (p. ej. list[dict]) usando nombres de columna."""
    record: OrderedDict[str, str] = OrderedDict()
    normalized_row = {_normalize_header_name(key): value for key, value in row.items()}
    for field in CLIENT_FIELDS:
        record[field] = _normalize_cell(
            row.get(field, normalized_row.get(_normalize_header_name(field)))
        )
    return record


def serialize_clientes_body(records: list[OrderedDict[str, str]]) -> str:
    """Serializa el cuerpo POST conservando el orden de campos de cada registro."""
    return json.dumps(records, ensure_ascii=False)


def _parse_csv_datos_clientes(text: str) -> list[OrderedDict[str, str]]:
    content = text.strip()
    if not content:
        return []

    rows = list(csv.reader(io.StringIO(content, newline="")))
    if not rows:
        return []

    start_index = 0
    if _is_header_row(rows[0]):
        start_index = 1

    records: list[OrderedDict[str, str]] = []
    for cells in rows[start_index:]:
        if not cells:
            continue
        if not any(_normalize_cell(value) for value in cells):
            continue
        records.append(_row_to_client_record_from_cells(cells))
    return records


def transform_datos_clientes(datos_clientes) -> list[dict]:
    """
    Transforma datos_clientes al JSON del cuerpo POST Wolkvox.

    Acepta:
      - str CSV (como ejemplo_body_carga_cliente_entrada.csv)
      - list[dict] con columnas del CSV
      - dict (un solo cliente)
    """
    if datos_clientes is None:
        return []

    if isinstance(datos_clientes, bytes):
        datos_clientes = datos_clientes.decode("utf-8-sig")

    if isinstance(datos_clientes, str):
        return _parse_csv_datos_clientes(datos_clientes)

    if isinstance(datos_clientes, dict):
        return [_row_to_client_record(datos_clientes)]

    if isinstance(datos_clientes, list):
        if not datos_clientes:
            return []
        if all(isinstance(item, dict) for item in datos_clientes):
            return [_row_to_client_record(item) for item in datos_clientes]
        raise ValueError("datos_clientes debe ser CSV, dict o list[dict].")

    raise ValueError("Formato de datos_clientes no soportado.")


def extract_url_placeholders(url_template: str) -> list[str]:
    """Nombres de variables {{nombre}} definidas en la URL de configuración."""
    return list(dict.fromkeys(_URL_PLACEHOLDER_RE.findall(url_template or "")))


def resolve_url_template(url_template: str, payload: dict) -> str:
    """
    Reemplaza {{nombre_variable}} en la URL por payload[nombre_variable].
    Los valores se codifican para uso seguro en query/path.
    """
    from urllib.parse import quote

    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        value = payload.get(name)
        if value is None or not str(value).strip():
            missing.append(name)
            return match.group(0)
        text = str(value).strip()
        if text.lower().startswith(("http://", "https://")):
            return text.rstrip("/")
        return quote(text, safe="")

    resolved = _URL_PLACEHOLDER_RE.sub(_replace, url_template.strip())
    if missing:
        raise ValueError(
            f"Faltan parámetros de invocación para la URL: {', '.join(missing)}"
        )
    if "{{" in resolved or "}}" in resolved:
        raise ValueError("La URL aún contiene variables sin reemplazar.")
    return resolved


def _build_request_url(url_template: str, payload: dict) -> str:
    return resolve_url_template(url_template, payload)


def _extract_campaign_id(payload: dict) -> str:
    value = payload.get("campaign_id")
    if value is not None and str(value).strip():
        return str(value).strip()
    return ""


def post(api_config: dict, payload: dict | None = None) -> dict:
    """
    POST hacia Wolkvox campaign.php con token en header y parámetros en URL.

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

    campaign_id = _extract_campaign_id(payload)
    if not campaign_id:
        return {
            "success": False,
            "message": "El parámetro campaign_id es obligatorio en la invocación.",
        }

    missing_url_params = [
        name
        for name in extract_url_placeholders(base_url)
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

    if "datos_clientes" not in payload:
        return {
            "success": False,
            "message": "El parámetro datos_clientes es obligatorio en la invocación.",
        }

    try:
        body = transform_datos_clientes(payload["datos_clientes"])
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    if not body:
        return {
            "success": False,
            "message": "datos_clientes no contiene registros de clientes.",
        }

    try:
        url = _build_request_url(base_url, payload)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    headers = build_wolkvox_headers(token, json_body=True)

    try:
        body_json = serialize_clientes_body(body)
        response = requests.post(
            url,
            data=body_json.encode("utf-8"),
            headers=headers,
            timeout=60,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:2000]}

        if response.ok:
            return {
                "success": True,
                "message": f"Carga de clientes exitosa (HTTP {response.status_code})",
                "status": response.status_code,
                "url": url,
                "campaign_id": campaign_id,
                "data": data,
            }

        return {
            "success": False,
            "message": f"Carga de clientes falló (HTTP {response.status_code})",
            "status": response.status_code,
            "url": url,
            "campaign_id": campaign_id,
            "data": data,
        }
    except requests.Timeout:
        return {"success": False, "message": "Timeout al invocar Wolkvox Carga Clientes."}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
