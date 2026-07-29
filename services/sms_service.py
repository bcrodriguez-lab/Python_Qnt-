"""Envio de SMS Infobip a partir de filas obtenidas desde BigQuery."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

PHONE_COLUMNS = (
    "celular", "telefono", "teléfono", "tel", "movil", "móvil", "phone",
    "numero", "número", "telephone", "tel1",
)
VARIABLE_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")


class SmsServiceError(ValueError):
    """Error que se puede mostrar de forma segura en la interfaz."""


class InfobipSenderV2:
    def __init__(self, api_key: str, base_url: str, *, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"App {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def enviar_lote(self, mensajes: list[dict], reintentos: int = 2) -> dict:
        url = f"{self.base_url}/sms/2/text/advanced"
        payload = {"messages": mensajes, "urlOptions": {
            "shortenUrl": True, "trackClicks": True, "removeProtocol": False,
        }}
        last_error = "Error desconocido de Infobip."
        for intento in range(1, reintentos + 1):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
                if response.status_code in (200, 201):
                    return {"success": True, "data": response.json()}
                last_error = f"Infobip respondió {response.status_code}: {response.text[:300]}"
            except requests.RequestException as exc:
                last_error = f"Error de conexión con Infobip: {exc}"
            if intento < reintentos:
                time.sleep(2 * intento)
        return {"success": False, "message": last_error}


def limpiar_numero(value: Any) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("57") and len(digits) == 12 and digits[2] == "3":
        return digits
    if len(digits) == 10 and digits.startswith("3"):
        return f"57{digits}"
    return None


def remover_tildes(texto: str) -> str:
    normalized = unicodedata.normalize("NFKD", texto)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def extraer_variables(plantilla: str) -> list[str]:
    return list(dict.fromkeys(match.group(1).strip() for match in VARIABLE_PATTERN.finditer(plantilla)))


def detectar_columna_telefono(rows: list[dict]) -> str:
    if not rows:
        raise SmsServiceError("La consulta no devolvió registros.")
    columns = {str(key).strip().lower(): str(key) for key in rows[0]}
    for candidate in PHONE_COLUMNS:
        if candidate in columns:
            return columns[candidate]
    raise SmsServiceError(
        "No se identificó una columna de teléfono. Use un alias como celular, telefono, movil o phone."
    )


def validar_variables(rows: list[dict], variables: list[str]) -> tuple[list[str], dict[str, int]]:
    columns = set(rows[0]) if rows else set()
    missing = [variable for variable in variables if variable not in columns]
    empty = {
        variable: sum(row.get(variable) is None or str(row.get(variable)).strip() == "" for row in rows)
        for variable in variables if variable in columns
    }
    return missing, {key: value for key, value in empty.items() if value}


def construir_mensaje(row: dict, plantilla: str) -> str:
    def replace(match: re.Match) -> str:
        value = row.get(match.group(1).strip())
        return "" if value is None else str(value).strip()
    return remover_tildes(VARIABLE_PATTERN.sub(replace, plantilla)).strip()


def preparar_sms(rows: list[dict], plantilla: str) -> tuple[list[dict], dict]:
    if not plantilla or not plantilla.strip():
        raise SmsServiceError("La plantilla del SMS es obligatoria.")
    phone_column = detectar_columna_telefono(rows)
    variables = extraer_variables(plantilla)
    missing, empty_variables = validar_variables(rows, variables)
    if missing:
        raise SmsServiceError(f"Variables no encontradas en la consulta: {', '.join(missing)}.")

    seen, prepared = set(), []
    invalid_numbers = 0
    for row in rows:
        phone = limpiar_numero(row.get(phone_column))
        if not phone:
            invalid_numbers += 1
            continue
        if phone in seen:
            continue
        seen.add(phone)
        prepared.append({"phone": phone, "text": construir_mensaje(row, plantilla), "row": row})
    return prepared, {"phone_column": phone_column, "invalid_numbers": invalid_numbers,
                      "duplicates": len(rows) - invalid_numbers - len(prepared),
                      "empty_variables": empty_variables}


def preview_sms(rows: list[dict], plantilla: str, limit: int = 3) -> dict:
    prepared, details = preparar_sms(rows, plantilla)
    return {"total_validos": len(prepared), "preview": [
        {"telefono": item["phone"], "mensaje": item["text"], "longitud": len(item["text"])}
        for item in prepared[:limit]
    ], **details}


def enviar_sms_desde_filas(rows: list[dict], plantilla: str, config: dict) -> dict:
    api_key, base_url, sender_id = (config.get("api_key") or "").strip(), (config.get("base_url") or "").strip(), (config.get("sender_id") or "").strip()
    if not api_key or api_key.startswith("REEMPLAZAR_") or not base_url or not sender_id:
        raise SmsServiceError("Configura infobip.api_key, infobip.base_url e infobip.sender_id antes de enviar.")
    prepared, details = preparar_sms(rows, plantilla)
    if not prepared:
        raise SmsServiceError("No hay números válidos para enviar.")
    batch_size = max(1, min(int(config.get("batch_size", 500)), 500))
    messages = []
    for item in prepared:
        callback = json.dumps(item["row"], ensure_ascii=False, default=str)
        messages.append({"from": sender_id, "destinations": [{"to": item["phone"]}],
                         "text": item["text"], "callbackData": callback[:4000]})
    batches = [messages[index:index + batch_size] for index in range(0, len(messages), batch_size)]
    sender = InfobipSenderV2(api_key, base_url)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(config.get("max_workers", 3)), 5))) as executor:
        futures = [executor.submit(sender.enviar_lote, batch) for batch in batches]
        for future in as_completed(futures):
            results.append(future.result())
    successful = [result for result in results if result.get("success")]
    bulk_ids = [result["data"].get("bulkId") for result in successful if result["data"].get("bulkId")]
    return {"total_preparados": len(messages), "lotes_enviados": len(successful),
            "lotes_fallidos": len(results) - len(successful), "bulk_ids": bulk_ids,
            "errores": [result.get("message") for result in results if not result.get("success")], **details}
