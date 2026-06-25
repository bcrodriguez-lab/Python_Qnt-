"""
Validación y mapeo de campos para consultas BigQuery.

Transforma alias de consultas variados al esquema estándar de Wolkvox,
acepta múltiples variaciones de nombres y genera errores solo si falta
un campo crítico.
"""

from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mapeo de aliases variados → campo estándar
# Cada campo estándar acepta múltiples variaciones (case-insensitive)
FIELD_ALIASES = {
    "customer_name": [
        "customer_name", "nombre", "customer_nombre",
        "nombre_cliente", "nombreclient", "first_name",
        "nombre_clientee", "initcap_nombre"
    ],
    "customer_last_name": [
        "customer_last_name", "apellido", "last_name",
        "apellido_cliente", "surname"
    ],
    "id_type": [
        "id_type", "tipoid", "tipo_id", "tipo_documento",
        "id_documento", "document_type"
    ],
    "customer_id": [
        "customer_id", "contacto__c", "contacto",
        "cliente_id", "id_cliente", "customer_code"
    ],
    "age": [
        "age", "edad", "años", "age_years"
    ],
    "gender": [
        "gender", "sexo", "genero__c", "genero", "género",
        "sex", "male_female"
    ],
    "country": [
        "country", "pais", "país", "country_code"
    ],
    "state": [
        "state", "departamento", "depto", "provincia",
        "state_code"
    ],
    "city": [
        "city", "ciudad", "municipio", "town"
    ],
    "zone": [
        "zone", "zona", "area", "region"
    ],
    "address": [
        "address", "direccion", "dirección", "street",
        "full_address"
    ],
    "opt1": [
        "opt1", "opcion1", "option1", "operado_por__c",
        "operado_por", "operador", "campaign_id"
    ],
    "opt2": [
        "opt2", "opcion2", "option2", "row_number",
        "rownum"
    ],
    "opt3": [
        "opt3", "opcion3", "option3", "saldo_capital_cliente",
        "saldo", "balance"
    ],
    "opt4": ["opt4", "opcion4", "option4"],
    "opt5": [
        "opt5", "opcion5", "option5", "barridos_tel",
        "barridos", "attempts"
    ],
    "opt6": ["opt6", "opcion6", "option6"],
    "opt7": ["opt7", "opcion7", "option7"],
    "opt8": ["opt8", "opcion8", "option8"],
    "opt9": ["opt9", "opcion9", "option9"],
    "opt10": ["opt10", "opcion10", "option10"],
    "opt11": ["opt11", "opcion11", "option11"],
    "opt12": ["opt12", "opcion12", "option12"],
    "tel1": [
        "tel1", "telefono_1", "telefono1", "phone1",
        "telefono", "phone", "telephone1"
    ],
    "tel2": ["tel2", "telefono_2", "telefono2", "phone2", "telephone2"],
    "tel3": ["tel3", "telefono_3", "telefono3", "phone3", "telephone3"],
    "tel4": ["tel4", "telefono_4", "telefono4", "phone4", "telephone4"],
    "tel5": ["tel5", "telefono_5", "telefono5", "phone5", "telephone5"],
    "tel6": ["tel6", "telefono_6", "telefono6", "phone6", "telephone6"],
    "tel7": ["tel7", "telefono_7", "telefono7", "phone7", "telephone7"],
    "tel8": ["tel8", "telefono_8", "telefono8", "phone8", "telephone8"],
    "tel9": ["tel9", "telefono_9", "telefono9", "phone9", "telephone9"],
    "tel10": [
        "tel10", "telefono_10", "telefono10", "phone10",
        "telephone10"
    ],
    "tel_extra": [
        "tel_extra", "otrostel", "otros_tel", "extra_phone",
        "phone_extra", "additional_phone"
    ],
    "email": [
        "email", "email_1", "email1", "correo",
        "correo_electronico", "e_mail"
    ],
    "recall_date": [
        "recall_date", "date_recall", "fecha_recall",
        "recall_fecha", "fecha_recordatorio"
    ],
    "recall_telephone": [
        "recall_telephone", "tel_recall", "telefono_recall",
        "recall_tel", "telefono_recordatorio"
    ],
}

# Campos requeridos (si faltan → error)
REQUIRED_FIELDS = {
    "customer_name",
    "customer_id",
}

# Campos opcionales pero útiles
OPTIONAL_FIELDS = set(FIELD_ALIASES.keys()) - REQUIRED_FIELDS


def _normalize_alias(alias: str) -> str:
    """Normaliza un alias: minúsculas, espacios → guiones bajos."""
    normalized = str(alias).strip().lower()
    normalized = re.sub(r"[\s\-]+", "_", normalized)
    normalized = re.sub(r"[^\w]", "", normalized)
    return normalized


def build_alias_map() -> dict[str, str]:
    """
    Crea mapa: alias_normalizado → campo_estándar.
    
    Retorna dict con todas las variaciones normalizadas apuntando
    al campo estándar correspondiente.
    """
    alias_map = {}
    for standard_field, variations in FIELD_ALIASES.items():
        for variation in variations:
            normalized = _normalize_alias(variation)
            alias_map[normalized] = standard_field
    return alias_map


_ALIAS_MAP = build_alias_map()


def map_column_name(alias: str) -> str | None:
    """
    Mapea un alias de columna al campo estándar.
    
    Args:
        alias: Nombre de columna desde la consulta (p. ej., "NOMBRE", "Contacto__c")
    
    Returns:
        Campo estándar (p. ej., "customer_name") o None si no se encuentra mapeo.
    """
    normalized = _normalize_alias(alias)
    return _ALIAS_MAP.get(normalized)


def validate_query_results(
    rows: list[dict],
    available_columns: set[str] | None = None,
) -> tuple[bool, str | None]:
    """
    Valida que los resultados de una consulta tengan los campos requeridos.
    
    Args:
        rows: Filas retornadas de BigQuery
        available_columns: Columnas disponibles (se extrae de rows si no se proporciona)
    
    Returns:
        (success: bool, error_message: str | None)
    """
    if not rows:
        return True, None
    
    if available_columns is None:
        available_columns = set(rows[0].keys())
    
    # Mapear columnas disponibles a campos estándar
    mapped_columns = set()
    for col in available_columns:
        mapped = map_column_name(col)
        if mapped:
            mapped_columns.add(mapped)
    
    # Verificar que todos los campos requeridos estén presentes
    missing = REQUIRED_FIELDS - mapped_columns
    if missing:
        return False, f"Faltan campos requeridos en la consulta: {', '.join(sorted(missing))}"
    
    return True, None


def normalize_row(row: dict) -> dict:
    """
    Normaliza una fila de resultados mapeando alias a campos estándar.
    
    Args:
        row: Fila con alias variados
    
    Returns:
        Fila normalizada con campos estándar. Campos no mapeados se ignoran.
    """
    normalized = {}
    for alias, value in row.items():
        standard_field = map_column_name(alias)
        if standard_field:
            normalized[standard_field] = value
    return normalized


def normalize_rows(rows: list[dict]) -> list[dict]:
    """
    Normaliza múltiples filas de resultados.
    
    Args:
        rows: Lista de filas con alias variados
    
    Returns:
        Lista de filas normalizadas.
    """
    return [normalize_row(row) for row in rows]


def validate_and_normalize(
    rows: list[dict],
) -> tuple[bool, list[dict], str | None]:
    """
    Valida y normaliza consulta en un paso.
    
    Args:
        rows: Filas de BigQuery
    
    Returns:
        (success: bool, normalized_rows: list[dict], error_message: str | None)
    """
    if not rows:
        return True, [], None
    
    # Validar
    success, error = validate_query_results(rows)
    if not success:
        return False, [], error
    
    # Normalizar
    normalized = normalize_rows(rows)
    return True, normalized, None


def get_field_mapping_example() -> dict[str, str]:
    """Retorna ejemplo del mapeo de campos para documentación."""
    return {
        field: field
        for field in sorted(FIELD_ALIASES.keys())
    }


def describe_field_aliases() -> dict[str, list[str]]:
    """Retorna todas las variaciones aceptadas por campo."""
    return FIELD_ALIASES.copy()
