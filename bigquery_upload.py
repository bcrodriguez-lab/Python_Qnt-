#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Módulo para subir datos a BigQuery desde archivos JSON (CDR y AMD)
"""

import os
import json
import pandas as pd
import logging
from datetime import datetime
from google.cloud import bigquery
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# ========== CONFIGURACIÓN ==========
PROJECT_ID = "capable-arbor-209819"
BASE_DIR_CRED = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR_CRED, "config", "google_key.json")

# Tablas de destino
TABLE_CONSOLIDADO = f"{PROJECT_ID}.Temporal.Embudo_Consolidado"
TABLE_CONSOLIDADO_AMD = f"{PROJECT_ID}.Temporal.Embudo_Consolidado_AMD"

# ========== CREDENCIALES ==========

def obtener_credenciales():
    """Obtiene las credenciales para BigQuery."""
    if os.path.exists(CREDENTIALS_PATH):
        try:
            credentials = service_account.Credentials.from_service_account_file(
                CREDENTIALS_PATH,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            logger.info(f"✅ Credenciales cargadas desde: {CREDENTIALS_PATH}")
            return credentials
        except Exception as e:
            logger.warning(f"⚠️ Error cargando credenciales: {e}")
    
    try:
        from google.auth import default
        credentials, _ = default()
        logger.info("✅ Credenciales desde default (gcloud)")
        return credentials
    except Exception as e:
        logger.error(f"❌ No se pudieron obtener credenciales: {e}")
        return None

def get_bq_client():
    """Obtiene un cliente de BigQuery."""
    credentials = obtener_credenciales()
    if credentials:
        return bigquery.Client(project=PROJECT_ID, credentials=credentials)
    return None

# ========== FUNCIONES DE SUBIDA ==========

def subir_json_a_bigquery(data_json: dict, servidor: str, fecha: str, tipo_reporte: str = "CDR") -> bool:
    """
    Sube datos JSON a BigQuery.
    
    Args:
        data_json: Datos en formato JSON (con 'data' o lista)
        servidor: Nombre del servidor
        fecha: Fecha en formato YYYY-MM-DD
        tipo_reporte: "CDR" o "AMD"
    
    Returns:
        bool: True si la subida fue exitosa
    """
    try:
        # Extraer datos
        if isinstance(data_json, dict) and 'data' in data_json:
            rows = data_json['data']
        elif isinstance(data_json, list):
            rows = data_json
        else:
            logger.warning(f"⚠️ Formato de datos no reconocido para {servidor}")
            return False
        
        if not rows:
            logger.warning(f"⚠️ No hay datos para {servidor} - {fecha}")
            return False
        
        # Convertir a DataFrame
        df = pd.DataFrame(rows)
        
        # Agregar columnas de metadatos
        df['servidor'] = servidor
        df['fecha_descarga'] = fecha
        df['tipo_reporte'] = tipo_reporte
        df['fecha_procesamiento'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Determinar tabla destino
        if tipo_reporte.upper() == "AMD":
            table_id = TABLE_CONSOLIDADO_AMD
        else:
            table_id = TABLE_CONSOLIDADO
        
        # Subir a BigQuery
        client = get_bq_client()
        if not client:
            logger.error("❌ No se pudo obtener cliente de BigQuery")
            return False
        
        # Eliminar registros anteriores para este servidor y fecha
        query_delete = f"""
            DELETE FROM `{table_id}`
            WHERE servidor = '{servidor}'
              AND fecha_descarga = '{fecha}'
              AND tipo_reporte = '{tipo_reporte}'
        """
        try:
            job = client.query(query_delete)
            job.result()
            logger.info(f"   🗑️ Eliminados registros anteriores de {servidor} en {table_id}")
        except Exception as e:
            logger.warning(f"   ⚠️ No se pudieron eliminar registros anteriores: {e}")
        
        # Configurar job de carga
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            autodetect=True,
        )
        
        # Subir datos
        logger.info(f"   📤 Subiendo {len(df):,} registros a {table_id}...")
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        
        logger.info(f"   ✅ Subida exitosa: {len(df):,} registros a {table_id}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo a BigQuery: {e}")
        import traceback
        traceback.print_exc()
        return False

def subir_cdr_a_bigquery(df: pd.DataFrame, servidor: str, fecha: str) -> bool:
    """
    Sube un DataFrame de CDR a BigQuery.
    
    Args:
        df: DataFrame con los datos
        servidor: Nombre del servidor
        fecha: Fecha en formato YYYY-MM-DD
    
    Returns:
        bool: True si la subida fue exitosa
    """
    try:
        if df.empty:
            logger.warning(f"⚠️ DataFrame vacío para {servidor}")
            return False
        
        # Agregar columnas de metadatos
        df['servidor'] = servidor
        df['fecha_descarga'] = fecha
        df['tipo_reporte'] = 'CDR'
        df['fecha_procesamiento'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        client = get_bq_client()
        if not client:
            logger.error("❌ No se pudo obtener cliente de BigQuery")
            return False
        
        # Eliminar registros anteriores
        query_delete = f"""
            DELETE FROM `{TABLE_CONSOLIDADO}`
            WHERE servidor = '{servidor}'
              AND fecha_descarga = '{fecha}'
              AND tipo_reporte = 'CDR'
        """
        try:
            job = client.query(query_delete)
            job.result()
        except Exception as e:
            logger.warning(f"   ⚠️ No se pudieron eliminar registros anteriores: {e}")
        
        # Subir datos
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            autodetect=True,
        )
        
        logger.info(f"   📤 Subiendo {len(df):,} registros CDR a {TABLE_CONSOLIDADO}...")
        job = client.load_table_from_dataframe(df, TABLE_CONSOLIDADO, job_config=job_config)
        job.result()
        
        logger.info(f"   ✅ CDR subido: {len(df):,} registros")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo CDR a BigQuery: {e}")
        return False

def subir_amd_a_bigquery(df: pd.DataFrame, servidor: str, fecha: str) -> bool:
    """
    Sube un DataFrame de AMD a BigQuery.
    
    Args:
        df: DataFrame con los datos
        servidor: Nombre del servidor
        fecha: Fecha en formato YYYY-MM-DD
    
    Returns:
        bool: True si la subida fue exitosa
    """
    try:
        if df.empty:
            logger.warning(f"⚠️ DataFrame vacío para {servidor}")
            return False
        
        # Agregar columnas de metadatos
        df['servidor'] = servidor
        df['fecha_descarga'] = fecha
        df['tipo_reporte'] = 'AMD'
        df['fecha_procesamiento'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        client = get_bq_client()
        if not client:
            logger.error("❌ No se pudo obtener cliente de BigQuery")
            return False
        
        # Eliminar registros anteriores
        query_delete = f"""
            DELETE FROM `{TABLE_CONSOLIDADO_AMD}`
            WHERE servidor = '{servidor}'
              AND fecha_descarga = '{fecha}'
              AND tipo_reporte = 'AMD'
        """
        try:
            job = client.query(query_delete)
            job.result()
        except Exception as e:
            logger.warning(f"   ⚠️ No se pudieron eliminar registros anteriores: {e}")
        
        # Subir datos
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            autodetect=True,
        )
        
        logger.info(f"   📤 Subiendo {len(df):,} registros AMD a {TABLE_CONSOLIDADO_AMD}...")
        job = client.load_table_from_dataframe(df, TABLE_CONSOLIDADO_AMD, job_config=job_config)
        job.result()
        
        logger.info(f"   ✅ AMD subido: {len(df):,} registros")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo AMD a BigQuery: {e}")
        return False

# ========== FUNCIONES DE VERIFICACIÓN ==========

def verificar_tablas() -> dict:
    """
    Verifica que las tablas existan en BigQuery.
    
    Returns:
        dict: Estado de cada tabla
    """
    client = get_bq_client()
    if not client:
        return {'error': 'No se pudo obtener cliente'}
    
    resultado = {}
    for table_id in [TABLE_CONSOLIDADO, TABLE_CONSOLIDADO_AMD]:
        try:
            client.get_table(table_id)
            resultado[table_id] = '✅ Existe'
        except Exception:
            resultado[table_id] = '❌ No existe'
    
    return resultado

def crear_tablas_si_no_existen():
    """
    Crea las tablas necesarias si no existen.
    """
    client = get_bq_client()
    if not client:
        logger.error("❌ No se pudo obtener cliente de BigQuery")
        return False
    
    # Schema para Embudo_Consolidado
    schema_consolidado = [
        bigquery.SchemaField("servidor", "STRING"),
        bigquery.SchemaField("fecha_descarga", "STRING"),
        bigquery.SchemaField("tipo_reporte", "STRING"),
        bigquery.SchemaField("fecha_procesamiento", "STRING"),
    ]
    
    for table_id in [TABLE_CONSOLIDADO, TABLE_CONSOLIDADO_AMD]:
        try:
            client.get_table(table_id)
            logger.info(f"✅ Tabla {table_id} ya existe")
        except Exception:
            # Crear tabla
            table = bigquery.Table(table_id, schema=schema_consolidado)
            table = client.create_table(table)
            logger.info(f"✅ Tabla {table_id} creada")
    
    return True

# ========== EJECUCIÓN DIRECTA ==========

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Verificar conexión
    client = get_bq_client()
    if client:
        logger.info("✅ Conexión a BigQuery exitosa")
        logger.info(f"📋 Tablas: {verificar_tablas()}")
    else:
        logger.error("❌ No se pudo conectar a BigQuery")