#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Servicio de envío de SMS con BigQuery e Infobip.
VERSIÓN CORREGIDA - Con acortamiento de URLs y callbackData.
"""
from datetime import timedelta

import json
import re
import time 
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set
from uuid import uuid4

import requests
import logging
import pandas as pd

logger = logging.getLogger(__name__)
COLOMBIA_TZ = timezone(timedelta(hours=-5))

# ========== CONSTANTES ==========
PHONE_COLUMNS = (
    "celular", "telefono", "teléfono", "tel", "movil", "móvil", "phone",
    "numero", "número", "telephone", "tel1", "cel", "cellphone",
)
VARIABLE_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")
SMS_LOG_TABLE = "capable-arbor-209819.Temporal.SmsLog"
SCHEDULE_TABLE = "capable-arbor-209819.Temporal.ProgramacionSMS"
BLACKLIST_TABLE = "capable-arbor-209819.Tablas_Reporteria.Telefonos_Tutela"

BATCH_SIZE = 100
MAX_WORKERS = 3
MAX_REINTENTOS = 2


class SmsServiceError(Exception):
    """Error que se puede mostrar de forma segura en la interfaz."""
    pass


# ==================================================
# 📡 CLIENTE INFOBIP V2 (CON ACORTAMIENTO)
# ==================================================

class InfobipSenderV2:
    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"App {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def enviar_lote(self, lista_mensajes: List[Dict], reintentos: int = MAX_REINTENTOS) -> Optional[Dict]:
        """
        Envía un lote de mensajes a Infobip con ACORTAMIENTO DE URLs.
        """
        url = f"{self.base_url}/sms/2/text/advanced"
        
        payload = {
            "messages": lista_mensajes,
            "urlOptions": {
                "shortenUrl": True,
                "trackClicks": True,
                "removeProtocol": False
            }
        }
        
        logger.info(f"📤 Enviando lote de {len(payload['messages'])} SMS a Infobip (URLs se acortarán automáticamente)")
        logger.debug(f"📦 Payload: {json.dumps(payload, ensure_ascii=False)[:500]}...")
        
        for intento in range(1, reintentos + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=30)
                if resp.status_code in [200, 201]:
                    logger.info(f"✅ Lote enviado exitosamente (URLs acortadas)")
                    return resp.json()
                else:
                    logger.warning(f"⚠️ Intento {intento}/{reintentos}: Error {resp.status_code} - {resp.text[:200]}")
                    if intento < reintentos:
                        time.sleep(2 * intento)
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Intento {intento}/{reintentos}: {e}")
                if intento < reintentos:
                    time.sleep(2 * intento)
        return None

# ==================================================
# 📱 LIMPIEZA DE NÚMEROS
# ==================================================

def limpiar_numero(value: Any) -> Optional[str]:
    """Limpia y normaliza número de teléfono al formato E.164 de Colombia."""
    if value is None:
        return None
    
    # Convertir a string y eliminar todo lo que no sea dígito
    num = re.sub(r"\D", "", str(value))
    
    if not num:
        return None
    
    # Caso 1: Ya está en formato 57 + 10 dígitos (ej: 573001234567)
    if num.startswith("57") and len(num) == 12:
        return num
    
    # Caso 2: Número colombiano de 10 dígitos que empieza con 3
    if len(num) == 10 and num.startswith("3"):
        return "57" + num
    
    # Caso 3: Solo dígitos sin código de país - asumir Colombia
    if len(num) == 10:
        return "57" + num
    
    # Caso 4: Ya tiene código de país diferente
    if len(num) > 10:
        return num
    
    # Caso 5: Muy corto, inválido
    logger.debug(f"Número inválido (muy corto): {num}")
    return None

# ==================================================
# 🧠 MANEJO DE VARIABLES
# ==================================================

def extraer_variables(texto: str) -> List[str]:
    """Extrae variables {{nombre}} de la plantilla."""
    return re.findall(r"{{(.*?)}}", texto)


def remover_tildes(texto: str) -> str:
    """Elimina tildes del texto para evitar problemas con encoding SMS."""
    nfkd_form = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def validar_variables(rows: List[Dict], variables: List[str]) -> Tuple[List[str], Dict[str, int]]:
    """Valida que las variables existan en los datos y cuenta valores vacíos."""
    if not rows:
        return [], {}
    
    # Obtener columnas disponibles (case-insensitive)
    columns_lower = {str(key).strip().lower(): str(key) for key in rows[0].keys()}
    faltantes = []
    vacias = {}
    
    for var in variables:
        var_lower = var.strip().lower()
        if var_lower not in columns_lower:
            faltantes.append(var)
        else:
            # Contar valores vacíos o nulos
            real_col = columns_lower[var_lower]
            vacios_count = sum(
                1 for row in rows 
                if row.get(real_col) is None or str(row.get(real_col, "")).strip() == ""
            )
            if vacios_count > 0:
                vacias[var] = vacios_count
    
    return faltantes, vacias


def construir_mensaje(row: Dict, plantilla: str, variables: List[str]) -> str:
    """Construye el mensaje personalizado reemplazando variables."""
    msg = plantilla
    
    # Mapa de columnas (case-insensitive) para búsqueda flexible
    row_lower = {str(k).strip().lower(): v for k, v in row.items()}
    
    for var in variables:
        var_lower = var.strip().lower()
        valor = row_lower.get(var_lower, "")
        if valor is None:
            valor = ""
        # Reemplazar {{variable}} en la plantilla
        msg = re.sub(
            r"{{\s*" + re.escape(var) + r"\s*}}",
            str(valor),
            msg,
            flags=re.IGNORECASE
        )
    
    return remover_tildes(msg)


def detectar_columna_telefono(rows: List[Dict]) -> str:
    """Detecta la columna de teléfono en los datos (case-insensitive)."""
    if not rows:
        raise SmsServiceError("La consulta no devolvió registros.")
    
    columns = {str(key).strip().lower(): str(key) for key in rows[0]}
    
    for candidate in PHONE_COLUMNS:
        if candidate in columns:
            return columns[candidate]
    
    # Mostrar columnas disponibles para ayudar al usuario
    cols_disponibles = ", ".join(rows[0].keys())
    raise SmsServiceError(
        f"No se identificó una columna de teléfono. Columnas disponibles: {cols_disponibles}. "
        "Use un alias como 'celular', 'telefono', 'movil' o 'phone'."
    )


def preparar_sms(rows: List[Dict], plantilla: str) -> Tuple[List[Dict], Dict]:
    """
    Prepara los mensajes: valida números, detecta columnas, reemplaza variables.
    Retorna lista de mensajes preparados y diccionario con detalles.
    """
    if not plantilla or not plantilla.strip():
        raise SmsServiceError("La plantilla del SMS es obligatoria.")
    
    phone_column = detectar_columna_telefono(rows)
    variables = extraer_variables(plantilla)
    
    if not variables:
        raise SmsServiceError(
            "La plantilla no contiene variables ({{Nombre Cliente}}, {{Valor Oferta Esp 2}}, etc.). "
            "Agregue al menos una variable para personalizar los mensajes."
        )
    
    missing, empty_variables = validar_variables(rows, variables)
    
    if missing:
        raise SmsServiceError(
            f"Variables no encontradas en la consulta: {', '.join(missing)}. "
            "Verifique los nombres de las variables en la plantilla."
        )

    seen = set()
    prepared = []
    invalid_numbers = 0
    
    for row in rows:
        phone = limpiar_numero(row.get(phone_column))
        if not phone:
            invalid_numbers += 1
            continue
            print(f"📱 Fila: {row.get(phone_column)} -> Limpiado: '{phone}'")
        # Eliminar duplicados dentro del mismo lote
        if phone in seen:
            continue
        seen.add(phone)
        
        prepared.append({
            "phone": phone,
            "text": construir_mensaje(row, plantilla, variables),
            "row": row
        })
    
    return prepared, {
        "phone_column": phone_column,
        "invalid_numbers": invalid_numbers,
        "duplicates": len(rows) - invalid_numbers - len(prepared),
        "empty_variables": empty_variables,
        "total_validos": len(prepared)
    }


def preview_sms(rows: List[Dict], plantilla: str, limit: int = 3) -> Dict:
    """Genera una vista previa sin enviar."""
    try:
        prepared, details = preparar_sms(rows, plantilla)
        
        preview_items = []
        for item in prepared[:limit]:
            preview_items.append({
                "telefono": item["phone"],
                "mensaje": item["text"],
                "longitud": len(item["text"])
            })
        
        return {
            "success": True,
            "total_validos": len(prepared),
            "preview": preview_items,
            "phone_column": details.get("phone_column"),
            "invalid_numbers": details.get("invalid_numbers", 0),
            "duplicates": details.get("duplicates", 0),
            "empty_variables": details.get("empty_variables", {}),
            "total_filas": len(rows)
        }
    except SmsServiceError as e:
        return {"success": False, "message": str(e)}


# ==================================================
# 📤 ENVÍO DE LOTES CON CALLBACKDATA Y ACORTAMIENTO
# ==================================================

def enviar_sms_desde_filas(
    rows: List[Dict], 
    plantilla: str, 
    config: Dict, 
    client=None,
    campaign: str = "", 
    usuario: str = "", 
    query_sql: str = "", 
    allow_resend: bool = False
) -> Dict:
    """
    Función principal: valida, genera mensajes, envía por lotes con callbackData.
    """
    api_key = (config.get("api_key") or "").strip()
    base_url = (config.get("base_url") or "").strip()
    sender_id = (config.get("sender_id") or "").strip()
    
    # Validar configuración
    if not api_key or api_key.startswith("REEMPLAZAR_") or not base_url or not sender_id:
        raise SmsServiceError(
            "Configure infobip.api_key, infobip.base_url e infobip.sender_id en config.json antes de enviar."
        )
    
    # Preparar mensajes
    prepared, details = preparar_sms(rows, plantilla)
    print(f"🔍 DEBUG - Preparados: {len(prepared)} mensajes")
    print(f"🔍 DEBUG - Details: {details}")
    for i, item in enumerate(prepared):
        print(f"🔍 DEBUG - Preparado {i}: phone={item['phone']}")
    # Aplicar validaciones (lista negra, duplicados)
    if client:
        phones = [item["phone"] for item in prepared]
        blocked = verificar_lista_negra(client, phones)
        duplicates = verificar_duplicados(client, phones)
        
        allowed = [item for item in prepared if item["phone"] not in blocked]
        
        if not allow_resend and duplicates:
            allowed = [item for item in allowed if item["phone"] not in duplicates]
        
        details.update({
            "excluidos_lista_negra": len(blocked),
            "duplicados_hoy": len(duplicates),
            "total_validos": len(allowed)
        })
        
        prepared = allowed
    # En enviar_sms_desde_filas, después de verificar duplicados
    print(f"🔍 DEBUG - Blocked: {blocked}")
    print(f"🔍 DEBUG - Duplicates: {duplicates}")
    print(f"🔍 DEBUG - Allow resend: {allow_resend}")
    print(f"🔍 DEBUG - Allowed antes del filtro: {[item['phone'] for item in allowed]}")

    print(f"🔍 DEBUG - Aplicando filtro de duplicados...")
    print(f"🔍 DEBUG - Allowed después del filtro: {[item['phone'] for item in allowed]}")
    if not prepared:
        raise SmsServiceError("No hay destinatarios válidos después de aplicar las validaciones.")
    
    # Construir mensajes para Infobip con callbackData
    batch_size = max(1, min(int(config.get("batch_size", BATCH_SIZE)), 500))
    messages = []
    
    for item in prepared:
        #  callbackData con todos los datos de la fila
        datos_fila = {}
        for key, value in item["row"].items():
            if value is not None and str(value).strip() != "":
                datos_fila[key] = str(value).strip()
            else:
                datos_fila[key] = ""
        
        callback_str = json.dumps(datos_fila, ensure_ascii=False)
        if len(callback_str) > 4000:
            callback_str = callback_str[:3997] + "..."
        
        messages.append({
            "from": sender_id,
            "destinations": [{"to": item["phone"]}],
            "text": item["text"],
            "callbackData": callback_str
        })
    
    # Dividir en lotes
    lotes = [messages[i:i + batch_size] for i in range(0, len(messages), batch_size)]
    
    logger.info(f"📦 Total mensajes: {len(messages)}, Lotes: {len(lotes)}, Batch size: {batch_size}")
    
    # Enviar lotes
    sender = InfobipSenderV2(api_key, base_url)
    results = []
    max_workers = max(1, min(int(config.get("max_workers", MAX_WORKERS)), 5))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(sender.enviar_lote, lote) for lote in lotes]
        for future in as_completed(futures):
            try:
                result = future.result(timeout=60)
                if result:
                    results.append({"success": True, "data": result})
                    # Log de resultados individuales
                    if "messages" in result:
                        for msg in result["messages"]:
                            status = msg.get("status", {}).get("name", "?")
                            logger.info(f"   📱 {msg.get('to', '?')} | {status}")
                else:
                    results.append({"success": False, "message": "Error en lote"})
            except Exception as e:
                logger.error(f"Error en lote: {e}")
                results.append({"success": False, "message": f"Error en lote: {e}"})
    
    # Procesar resultados
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    bulk_ids = []
    
    for r in successful:
        data = r.get("data", {})
        if data.get("bulkId"):
            bulk_ids.append(data["bulkId"])
    
    # Recolectar errores
    errores = []
    for result in failed:
        errores.append(result.get("message", "Error desconocido"))
    
    # Guardar en SmsLog
    if client and prepared:
        try:
            guardar_sms_log(
                client, prepared, 
                campaign=campaign, 
                usuario=usuario,
                bulk_ids=bulk_ids, 
                reenvios=set(), 
                status="enviado", 
                plantilla=plantilla
            )
        except Exception as e:
            logger.error(f"Error guardando logs: {e}")
    
    enviados = len(successful) * batch_size  # Aproximado
    fallidos = len(failed) * batch_size
    
    return {
        "total_preparados": len(messages),
        "lotes_enviados": len(successful),
        "lotes_fallidos": len(failed),
        "enviados": enviados,
        "fallidos": fallidos,
        "bulk_ids": bulk_ids,
        "errores": errores[:10],
        "details": details
    }


def guardar_sms_log(
    client, 
    mensajes: List[Dict], 
    *, 
    campaign: str, 
    usuario: str,
    bulk_ids: List[str], 
    reenvios: set, 
    status: str, 
    plantilla: str = ""
) -> None:
    """Guarda registros en SmsLog."""
    now = datetime.now(timezone.utc).isoformat()
    bulk_id = bulk_ids[0] if bulk_ids else None
    
    records = []
    for item in mensajes:
        records.append({
            "telefono": item["phone"],
            "mensaje": item["text"],
            "plantilla": plantilla,
            "consulta_sql": "",
            "fecha_envio": now,
            "resultado": status,
            "bulk_id": bulk_id,
            "error": "",
            "campana": campaign or "",
            "usuario": usuario or "",
            "es_reenvio": item["phone"] in reenvios,
            "fecha_creacion": now,
            "fecha_actualizacion": now,
        })
    
    if records and client:
        try:
            errors = client.insert_rows_json(SMS_LOG_TABLE, records)
            if errors:
                logger.error(f"Error guardando logs: {errors}")
            else:
                logger.info(f"✅ {len(records)} registros guardados en SmsLog")
        except Exception as e:
            logger.error(f"Error guardando logs: {e}")

def guardar_sms_log(
    client, 
    mensajes: List[Dict], 
    *, 
    campaign: str, 
    usuario: str,
    bulk_ids: List[str], 
    reenvios: set, 
    status: str, 
    plantilla: str = ""
) -> None:
    """Guarda registros en SmsLog vía INSERT (query job, no streaming insert)."""
    from google.cloud import bigquery

    now = datetime.now(timezone.utc).isoformat()
    bulk_id = bulk_ids[0] if bulk_ids else None

    if not mensajes or not client:
        return

    insert_sql = f"""
        INSERT INTO `{SMS_LOG_TABLE}` (
            telefono, mensaje, plantilla, consulta_sql, fecha_envio,
            resultado, bulk_id, error, campana, usuario, es_reenvio,
            fecha_creacion, fecha_actualizacion
        )
        VALUES
    """
    # Construir múltiples filas con parámetros indexados
    value_rows = []
    parameters = []
    for i, item in enumerate(mensajes):
        value_rows.append(
            f"(@telefono_{i}, @mensaje_{i}, @plantilla_{i}, @consulta_sql_{i}, @fecha_envio_{i}, "
            f"@resultado_{i}, @bulk_id_{i}, @error_{i}, @campana_{i}, @usuario_{i}, @es_reenvio_{i}, "
            f"@fecha_creacion_{i}, @fecha_actualizacion_{i})"
        )
        parameters.extend([
            bigquery.ScalarQueryParameter(f"telefono_{i}", "STRING", item["phone"]),
            bigquery.ScalarQueryParameter(f"mensaje_{i}", "STRING", item["text"]),
            bigquery.ScalarQueryParameter(f"plantilla_{i}", "STRING", plantilla),
            bigquery.ScalarQueryParameter(f"consulta_sql_{i}", "STRING", ""),
            bigquery.ScalarQueryParameter(f"fecha_envio_{i}", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter(f"resultado_{i}", "STRING", status),
            bigquery.ScalarQueryParameter(f"bulk_id_{i}", "STRING", bulk_id),
            bigquery.ScalarQueryParameter(f"error_{i}", "STRING", ""),
            bigquery.ScalarQueryParameter(f"campana_{i}", "STRING", campaign or ""),
            bigquery.ScalarQueryParameter(f"usuario_{i}", "STRING", usuario or ""),
            bigquery.ScalarQueryParameter(f"es_reenvio_{i}", "BOOL", item["phone"] in reenvios),
            bigquery.ScalarQueryParameter(f"fecha_creacion_{i}", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter(f"fecha_actualizacion_{i}", "TIMESTAMP", now),
        ])

    insert_sql += ", ".join(value_rows)
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)

    try:
        client.query(insert_sql, job_config=job_config).result()
        logger.info(f"✅ {len(mensajes)} registros guardados en SmsLog (vía query job)")
    except Exception as e:
        logger.error(f"Error guardando logs: {e}")


def guardar_programacion(
    client,
    query: str,
    plantilla: str,
    *,
    campaign: str = "",
    usuario: str = "",
    allow_resend: bool = False,
    total_dest: int = 0,
    tipo_programacion: str = "simple",
    fecha_programada: str = None,      # Para simple: "2026-08-18T08:00:00"
    hora_inicio: str = None,           # Para recurrente: "08:00"
    fecha_fin: str = None,             # Para recurrente: "2026-09-25"
) -> str:
    """Guarda una programación en BigQuery."""
    from google.cloud import bigquery

    schedule_id = str(uuid4())
    now = datetime.now(COLOMBIA_TZ).isoformat()

    insert_sql = """
    INSERT INTO `capable-arbor-209819.Temporal.ProgramacionSMS`
    (id, tipo_programacion, consulta_sql, plantilla, campana, usuario, estado,
     total_destinatarios, confirmar_reenvio, fecha_programada, hora_inicio, fecha_fin,
     fecha_creacion, fecha_actualizacion)
    VALUES (
        @id, @tipo_prog, @consulta, @plantilla_param, @campana, @usuario, @estado,
        @total_dest, @conf_reenvio, @fecha_prog, @hora_inicio, @fecha_fin,
        @now, @now
    )
    """

    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("id", "STRING", schedule_id),
        bigquery.ScalarQueryParameter("tipo_prog", "STRING", tipo_programacion),
        bigquery.ScalarQueryParameter("consulta", "STRING", query),
        bigquery.ScalarQueryParameter("plantilla_param", "STRING", plantilla),
        bigquery.ScalarQueryParameter("campana", "STRING", campaign or ""),
        bigquery.ScalarQueryParameter("usuario", "STRING", usuario or ""),
        bigquery.ScalarQueryParameter("estado", "STRING", "pendiente"),
        bigquery.ScalarQueryParameter("total_dest", "INT64", total_dest),
        bigquery.ScalarQueryParameter("conf_reenvio", "BOOL", allow_resend),
        bigquery.ScalarQueryParameter("fecha_prog", "TIMESTAMP", fecha_programada),
        bigquery.ScalarQueryParameter("hora_inicio", "STRING", hora_inicio),
        bigquery.ScalarQueryParameter("fecha_fin", "STRING", fecha_fin),
        bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
    ])

    try:
        client.query(insert_sql, job_config=job_config).result()
        logger.info(f"✅ Programación guardada: {schedule_id} ({tipo_programacion})")
        return schedule_id
    except Exception as e:
        raise SmsServiceError(f"No se pudo guardar la programación: {e}")

def verificar_lista_negra(client, phones: List[str]) -> Set[str]:
    """Verifica qué números están en la lista negra."""
    if not phones:
        return set()
    
    if not client:
        raise SmsServiceError("No hay conexión a BigQuery para validar lista negra.")
    
    try:
        from google.cloud import bigquery
        
        # Normalizar a 10 dígitos para comparar con la tabla
        phones_10 = set()
        for p in phones:
            cleaned = limpiar_numero(p)
            if not cleaned:
                continue
            # Convertir a formato 10 dígitos (sin 57)
            if cleaned.startswith("57") and len(cleaned) == 12:
                phones_10.add(cleaned[2:])
            elif len(cleaned) == 10:
                phones_10.add(cleaned)
        
        if not phones_10:
            return set()
        
        # Consultar la tabla
        query = f"""
            SELECT DISTINCT Telefono AS telefono
            FROM `{BLACKLIST_TABLE}`
            WHERE Telefono IN UNNEST(@telefonos)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("telefonos", "STRING", list(phones_10))
            ]
        )
        
        rows = client.query(query, job_config=job_config).result()
        blocked_10 = {str(row.telefono).strip() for row in rows}
        
        logger.info(f"📋 Lista negra encontrados: {blocked_10}")
        
        # Encontrar qué teléfonos originales están bloqueados
        result = set()
        for p in phones:
            cleaned = limpiar_numero(p)
            if not cleaned:
                continue
            
            # Convertir a 10 dígitos para comparar
            if cleaned.startswith("57") and len(cleaned) == 12:
                phone_10 = cleaned[2:]
            else:
                phone_10 = cleaned
            
            if phone_10 in blocked_10:
                result.add(p)
        
        if result:
            logger.info(f"🚫 {len(result)} números en lista negra: {sorted(result)}")
        else:
            logger.info(f"✅ Ningún número en lista negra")
        
        return result
    
    except SmsServiceError:
        raise
    except Exception as e:
        logger.error(f"Error al validar lista negra: {e}")
        raise SmsServiceError(f"Error al validar lista negra: {e}")


def verificar_duplicados(client, phones: List[str]) -> Set[str]:
    """Verifica qué números ya recibieron SMS hoy."""
    if not phones or not client:
        return set()
    
    try:
        from google.cloud import bigquery
        
        # Consultar envíos del día actual
        query = f"""
            SELECT DISTINCT telefono
            FROM `{SMS_LOG_TABLE}`
            WHERE DATE(fecha_envio, 'America/Bogota') = CURRENT_DATE('America/Bogota')
              AND telefono IN UNNEST(@telefonos)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("telefonos", "STRING", phones)
            ]
        )
        
        rows = client.query(query, job_config=job_config).result()
        duplicates = {row.telefono for row in rows}
        
        if duplicates:
            logger.info(f"🔄 {len(duplicates)} números ya recibieron SMS hoy")
        
        return duplicates
    
    except Exception as e:
        logger.warning(f"Error verificando duplicados: {e}")
        return set()


def aplicar_validaciones(
    rows: List[Dict], 
    plantilla: str, 
    client, 
    allow_resend: bool = False
) -> Tuple[List[Dict], Dict]:
    """Aplica validaciones de lista negra y duplicados."""
    prepared, details = preparar_sms(rows, plantilla)
    
    if not prepared:
        return [], details
    
    phones = [item["phone"] for item in prepared]
    
    blocked = verificar_lista_negra(client, phones)
    duplicates = verificar_duplicados(client, phones)
    
    # Excluir números bloqueados
    allowed = [item for item in prepared if item["phone"] not in blocked]
    
    # Excluir duplicados (a menos que se permita reenvío)
    if not allow_resend and duplicates:
        allowed = [item for item in allowed if item["phone"] not in duplicates]
    
    details.update({
        "total_consulta": len(rows),
        "excluidos_lista_negra": len(blocked),
        "telefonos_lista_negra": sorted(blocked),
        "duplicados_hoy": len(duplicates),
        "telefonos_duplicados": sorted(duplicates),
        "total_validos": len(allowed)
    })
    
    return allowed, details


def obtener_lista_negra(client) -> List[Dict]:
    """Obtiene la lista negra desde Tablas_Reporteria.Telefonos_Tutela."""
    query = f"""
        SELECT 
            Telefono AS telefono,
            Motivo AS motivo,
            Fecha AS fecha_creacion
        FROM `{BLACKLIST_TABLE}`
        ORDER BY Fecha DESC
        LIMIT 500
    """
    try:
        df = client.query(query).to_dataframe()
        return df.to_dict('records') if not df.empty else []
    except Exception as e:
        logger.error(f"Error obteniendo lista negra: {e}")
        return []

# ==================================================
#  MENSAJES AUTOMÁTICOS POR OPERACIÓN
# ==================================================

MENSAJE_OPERACION_TABLE = "capable-arbor-209819.Temporal.Mensaje_Operacion"

def obtener_mensaje_por_operacion(client, id_campana: str, operador: str, intensidad: str) -> Optional[Dict]:
    """Busca un mensaje en Mensaje_Operacion por campaña, operador e intensidad."""
    if not client:
        return None
    try:
        query = f"""
            SELECT id_mensaje, mensaje, intensidad
            FROM `{MENSAJE_OPERACION_TABLE}`
            WHERE id_campana = '{id_campana}'
              AND LOWER(operador) = LOWER('{operador}')
              AND LOWER(intensidad) = LOWER('{intensidad}')
            LIMIT 1
        """
        df = client.query(query).to_dataframe()
        if not df.empty:
            return df.iloc[0].to_dict()
        return None
    except Exception as e:
        logger.warning(f"Error buscando mensaje de operación: {e}")
        return None

def aplicar_mensaje_operacion(rows: List[Dict], client, col_campana: str = "Id Campaña", 
                               col_operador: str = "operador", col_intensidad: str = "intensidad") -> List[Dict]:
    """
    Hace JOIN entre los datos de la consulta y Mensaje_Operacion.
    Agrega el campo 'mensaje_auto' a cada fila con la plantilla correspondiente.
    """
    if not client or not rows:
        return rows
    
    try:
        # Obtener todos los mensajes de operación
        query = f"SELECT * FROM `{MENSAJE_OPERACION_TABLE}`"
        df_mensajes = client.query(query).to_dataframe()
        
        if df_mensajes.empty:
            return rows
        
        for row in rows:
            id_campana = str(row.get(col_campana, "")).strip()
            operador = str(row.get(col_operador, "")).strip()
            intensidad = str(row.get(col_intensidad, "")).strip()
            
            match = df_mensajes[
                (df_mensajes["id_campana"] == id_campana) &
                (df_mensajes["operador"].str.lower() == operador.lower()) &
                (df_mensajes["intensidad"].str.lower() == intensidad.lower())
            ]
            
            if not match.empty:
                best = match.iloc[0]
                row["mensaje_auto"] = best["mensaje"]
                row["id_mensaje_auto"] = best["id_mensaje"]
            else:
                row["mensaje_auto"] = None
                row["id_mensaje_auto"] = None
        
        return rows
    except Exception as e:
        logger.warning(f"Error aplicando mensaje de operación: {e}")
        return rows