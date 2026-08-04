
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import pandas as pd

logger = logging.getLogger(__name__)

EMAIL_LOG_TABLE = "capable-arbor-209819.Temporal.EmailLog"
EMAIL_COLUMNS = ("email", "correo", "e-mail", "mail", "correo_electronico")


class EmailServiceError(Exception):
    """Error personalizado para el servicio de email."""
    pass


def detectar_columna_email(rows: List[Dict]) -> str:
    """Detecta la columna de email en los datos."""
    if not rows:
        raise EmailServiceError("La consulta no devolvió registros.")
    
    columns = {str(key).strip().lower(): str(key) for key in rows[0]}
    
    for candidate in EMAIL_COLUMNS:
        if candidate in columns:
            return columns[candidate]
    
    cols_disponibles = ", ".join(rows[0].keys())
    raise EmailServiceError(
        f"No se identificó una columna de email. Columnas disponibles: {cols_disponibles}. "
        "Use un alias como 'email', 'correo', 'mail'."
    )


def extraer_variables(texto: str) -> List[str]:
    """Extrae variables {{nombre}} del texto."""
    return re.findall(r"{{(.*?)}}", texto)


def construir_contenido(row: Dict, plantilla: str) -> str:
    """Construye el contenido reemplazando variables."""
    contenido = plantilla
    row_lower = {str(k).strip().lower(): v for k, v in row.items()}
    
    variables = extraer_variables(plantilla)
    for var in variables:
        var_lower = var.strip().lower()
        valor = row_lower.get(var_lower, "")
        if valor is None:
            valor = ""
        contenido = re.sub(
            r"{{\s*" + re.escape(var) + r"\s*}}",
            str(valor),
            contenido,
            flags=re.IGNORECASE
        )
    
    return contenido


def preview_email(rows: List[Dict], plantilla: str, asunto: str, limit: int = 3) -> Dict:
    """Genera una vista previa del email."""
    try:
        email_column = detectar_columna_email(rows)
        variables = extraer_variables(plantilla)
        
        preview_items = []
        for row in rows[:limit]:
            email = str(row.get(email_column, ""))
            contenido = construir_contenido(row, plantilla)
            asunto_personalizado = construir_contenido(row, asunto)
            
            preview_items.append({
                "email": email,
                "asunto": asunto_personalizado,
                "contenido": contenido
            })
        
        return {
            "success": True,
            "total_filas": len(rows),
            "email_column": email_column,
            "variables": variables,
            "preview": preview_items
        }
    
    except EmailServiceError as e:
        return {"success": False, "message": str(e)}


def guardar_email_log(client, registros: List[Dict]) -> None:
    """Guarda registros en EmailLog (BigQuery)."""
    if not client or not registros:
        return
    
    try:
        errors = client.insert_rows_json(EMAIL_LOG_TABLE, registros)
        if errors:
            logger.error(f"Error guardando logs de email: {errors}")
        else:
            logger.info(f"✅ {len(registros)} registros guardados en EmailLog")
    except Exception as e:
        logger.error(f"Error guardando logs: {e}")