
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
    """Guarda registros en EmailLog usando INSERT SQL con escaping seguro."""
    if not client or not registros:
        return
    
    logger.info(f"📧 Guardando {len(registros)} registros con INSERT SQL")
    
    try:
        for registro in registros:
            id_val = _escape_sql(registro.get("id", ""))
            email_val = _escape_sql(registro.get("email", ""))
            asunto_val = _escape_sql(registro.get("asunto", ""))
            contenido_val = _escape_sql(registro.get("contenido", ""))
            campana_id_val = _escape_sql(registro.get("campana_id", ""))
            campana_nombre_val = _escape_sql(registro.get("campana_nombre", ""))
            fecha_envio_val = registro.get("fecha_envio", "")
            resultado_val = _escape_sql(registro.get("resultado", ""))
            bulk_id_val = _escape_sql(registro.get("bulk_id", ""))
            error_val = _escape_sql(registro.get("error", ""))
            campana_val = _escape_sql(registro.get("campana", ""))
            usuario_val = _escape_sql(registro.get("usuario", ""))
            fecha_creacion_val = registro.get("fecha_creacion", "")
            fecha_actualizacion_val = registro.get("fecha_actualizacion", "")
            
            insert_sql = f"""
                INSERT INTO `{EMAIL_LOG_TABLE}` 
                (id, email, asunto, contenido, campana_id, campana_nombre, 
                 fecha_envio, resultado, bulk_id, error, campana, usuario, 
                 fecha_creacion, fecha_actualizacion)
                VALUES (
                    '{id_val}', '{email_val}', '{asunto_val}', '''{contenido_val}''', 
                    '{campana_id_val}', '{campana_nombre_val}', 
                    TIMESTAMP('{fecha_envio_val}'), '{resultado_val}', 
                    '{bulk_id_val}', '{error_val}', '{campana_val}', 
                    '{usuario_val}', TIMESTAMP('{fecha_creacion_val}'), 
                    TIMESTAMP('{fecha_actualizacion_val}')
                )
            """
            client.query(insert_sql).result()
        
        logger.info(f"✅ {len(registros)} registros guardados en EmailLog")
    except Exception as e:
        logger.error(f"Error guardando logs: {e}")

def traducir_a_member(html: str, mapeo: Dict[str, int]) -> str:
    import re
    
    resultado = html
    variables = re.findall(r"{{\s*([^{}]+?)\s*}}", html)
    
    for var in variables:
        var_original = var.strip()
        var_lower = var_original.lower()
        var_guion = var_lower.replace(" ", "_")
        var_sin_espacios = var_lower.replace(" ", "")
        
        campo_id = None
        if var_lower in mapeo:
            campo_id = mapeo[var_lower]
        elif var_guion in mapeo:
            campo_id = mapeo[var_guion]
        elif var_sin_espacios in mapeo:
            campo_id = mapeo[var_sin_espacios]
        
        if campo_id:
            resultado = re.sub(
                r"{{\s*" + re.escape(var_original) + r"\s*}}",
                f"%Member:CustomField{campo_id}%",
                resultado,
                flags=re.IGNORECASE
            )
            logger.info(f"✅ Mapeado: '{var_original}' → CustomField{campo_id}")
        else:
            logger.warning(f"⚠️ Variable sin mapeo: '{var_original}' (buscada como: {var_lower}, {var_guion}, {var_sin_espacios})")
    
    return resultado

def execute_email_schedule(schedule_id: str):
    """Ejecuta un envío de email programado."""
    try:
        query_sql = f"""
            SELECT * FROM `capable-arbor-209819.Temporal.ProgramacionEmail`
            WHERE id = '{schedule_id}' AND estado = 'pendiente'
        """
        df = bq_client.query(query_sql).to_dataframe()
        if df.empty:
            logger.warning(f"Programación {schedule_id} no encontrada")
            return

        prog = df.iloc[0].to_dict()
        
        # Simular el request al endpoint de envío
        with app.test_request_context():
            from flask import jsonify
            # Aquí iría la lógica de envío...
        
    except Exception as exc:
        logger.exception(f"Error ejecutando programación {schedule_id}")

def validar_variables_plantilla(rows: List[Dict], plantilla: str, asunto: str = "") -> Dict:
    """
    Valida que todas las variables de la plantilla existan en los datos.
    Retorna errores si faltan columnas.
    """
    import re
    
    # Extraer todas las variables de la plantilla y el asunto
    vars_plantilla = re.findall(r"{{\s*([^{}]+?)\s*}}", plantilla)
    vars_asunto = re.findall(r"{{\s*([^{}]+?)\s*}}", asunto) if asunto else []
    todas_vars = set(vars_plantilla + vars_asunto)
    
    if not rows:
        return {
            "valido": False,
            "error": "La consulta no devolvió registros.",
            "variables_faltantes": list(todas_vars),
            "variables_encontradas": []
        }
    
    # Columnas disponibles en la primera fila
    columnas_disponibles = {str(k).strip().lower() for k in rows[0].keys()}
    
    # Verificar cada variable
    faltantes = []
    encontradas = []
    
    for var in todas_vars:
        var_lower = var.strip().lower()
        if var_lower in columnas_disponibles:
            encontradas.append(var)
        else:
            faltantes.append(var)
    
    return {
        "valido": len(faltantes) == 0,
        "error": f"Faltan {len(faltantes)} variables en la consulta: {', '.join(faltantes)}" if faltantes else None,
        "variables_faltantes": faltantes,
        "variables_encontradas": encontradas,
        "total_variables": len(todas_vars),
        "columnas_disponibles": list(columnas_disponibles)
    }

def _escape_sql(valor: str) -> str:
    """Escapa un valor para ser usado en SQL."""
    if valor is None:
        return ""
    # Escapar backslash primero, luego comillas simples
    return str(valor).replace("\\", "\\\\").replace("'", "\\'")