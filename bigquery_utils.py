#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utilidades para subir datos a BigQuery desde JSON.
Mapea los campos del reporte CDR a la tabla Temporal_Robot_Campañas.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from google.oauth2 import service_account
from google.auth import default
from google.cloud import bigquery
from google.api_core.exceptions import NotFound
import os
import json

logger = logging.getLogger(__name__)

# ========== CONFIGURACIÓN ==========
PROJECT_ID = "capable-arbor-209819"
DATASET_ID = "Temporal"
TABLE_NAME = "Temporal_Robot_Campañas"
FULL_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}"

BASE_DIR_CRED = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR_CRED, "config", "google_key.json")

def obtener_credenciales():
    """Obtiene las credenciales para BigQuery."""
    if os.path.exists(CREDENTIALS_PATH):
        try:
            credentials = service_account.Credentials.from_service_account_file(
                CREDENTIALS_PATH,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            logger.info(f"✅ Credenciales cargadas desde: {CREDENTIALS_PATH}")
            logger.info(f"✅ Usando proyecto: {PROJECT_ID}")
            return credentials, PROJECT_ID
        except Exception as e:
            logger.warning(f"⚠️ Error cargando credenciales: {e}")
    
    try:
        credentials, _ = default()
        logger.info(f"✅ Credenciales desde default (gcloud)")
        logger.info(f"✅ Usando proyecto: {PROJECT_ID}")
        return credentials, PROJECT_ID
    except Exception as e:
        logger.error(f"❌ No se pudieron obtener credenciales: {e}")
        raise

def obtener_esquema_tabla():
    """Obtiene las columnas de la tabla destino desde BigQuery."""
    try:
        credentials, project = obtener_credenciales()
        client = bigquery.Client(project=project, credentials=credentials)
        table = client.get_table(FULL_TABLE)
        columnas = [col.name for col in table.schema]
        logger.info(f"📋 Columnas en la tabla: {columnas}")
        return columnas
    except NotFound:
        logger.warning(f"⚠️ La tabla {FULL_TABLE} no existe. Se creará con autodetect.")
        return None
    except Exception as e:
        logger.error(f"❌ Error obteniendo esquema: {e}")
        return None

def crear_dataframe_crudo(data: dict, servidor: str, fecha: str, columnas_tabla: list = None) -> pd.DataFrame:
    """
    Crea un DataFrame con los datos crudos del CDR, mapeando campos.
    """
    try:
        # Extraer filas del JSON
        rows = []
        if isinstance(data, dict) and 'data' in data:
            rows = data['data']
        elif isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = [data]
        
        if not rows:
            logger.warning(f"⚠️ Sin datos para procesar: {servidor} - {fecha}")
            return pd.DataFrame()
        
        # Crear DataFrame con los datos crudos
        df_raw = pd.DataFrame(rows)
        
        # Función auxiliar para obtener valor string seguro
        def safe_str(val):
            if pd.isna(val):
                return ''
            return str(val)
        
        def safe_int(val):
            try:
                return int(float(val)) if pd.notna(val) else 0
            except (ValueError, TypeError):
                return 0
        
        # Mapeo de columnas (nombre en DataFrame -> origen en JSON o valor por defecto)
        mapeo_columnas = {
            'Fecha_dia': {
                'origen': 'date',
                'tipo': 'str_date',
                'default': ''
            },
            'DATE': {
                'origen': 'date',
                'tipo': 'str_datetime',
                'default': ''
            },
            'TELEPHONE': {
                'origen': 'telephone',
                'tipo': 'int',
                'default': 0
            },
            'COD_ACT': {
                'origen': 'cod_act',
                'tipo': 'str',
                'default': ''
            },
            'Grupo_Operador': {
                'origen': 'skill_name',
                'tipo': 'str',
                'default': ''
            },
            'Operado_Por__c': {
                'origen': 'agent_id',
                'tipo': 'str',
                'default': ''
            },
            'CONN_ID': {
                'origen': 'conn_id',
                'tipo': 'str',
                'default': ''
            },
            'Contacto__c': {
                'origen': 'customer_id',
                'tipo': 'str',
                'default': ''
            },
            'Contacto_Identificado_Robot': {
                'origen': None,
                'tipo': 'int',
                'default': 0
            },
            'Gestion_Humano': {
                'origen': None,
                'tipo': 'int',
                'default': 0
            },
            'Contacto_Identificado_Humano': {
                'origen': None,
                'tipo': 'int',
                'default': 0
            },
            'Venta_Humano_Identificado': {
                'origen': None,
                'tipo': 'int',
                'default': 0
            },
            'Ultimo_Contacto': {
                'origen': 'date',
                'tipo': 'str_datetime',
                'default': ''
            },
            'Entidad_principal': {
                'origen': 'type_interaction',
                'tipo': 'str',
                'default': ''
            },
            'localizado_historico': {
                'origen': 'hang_up',
                'tipo': 'str',
                'default': ''
            },
            'Gestion_Marcador': {
                'origen': 'cod_act_2',
                'tipo': 'str',
                'default': ''
            },
            'CAMPAIGN_ID': {
                'origen': 'campaign_id',
                'tipo': 'str',
                'default': ''
            },
            'FILE_NAME': {
                'origen': None,
                'tipo': 'str',
                'default': ''
            },
            'TELEPHONE_y': {
                'origen': 'destiny',
                'tipo': 'str',
                'default': ''
            },
            'RESULT': {
                'origen': None,
                'tipo': 'str',
                'default': ''
            },
            'COD_CAMPAIGN': {
                'origen': 'campaign_id',
                'tipo': 'str',
                'default': ''
            },
            'Nombre_Campaña': {
                'origen': None,
                'tipo': 'str',
                'default': ''
            }
        }
        
        # Si conocemos las columnas de la tabla, filtramos el mapeo
        if columnas_tabla:
            mapeo_columnas = {k: v for k, v in mapeo_columnas.items() if k in columnas_tabla}
        
        # Crear DataFrame resultado
        df_resultado = pd.DataFrame()
        
        # Procesar fechas
        fecha_series = pd.to_datetime(df_raw.get('date', ''), errors='coerce')
        
        for col, config in mapeo_columnas.items():
            origen = config['origen']
            tipo = config['tipo']
            default = config['default']
            
            if origen is None:
                # Columna sin origen, usar valor por defecto
                if tipo == 'int':
                    df_resultado[col] = default
                else:
                    df_resultado[col] = default
            elif origen == 'date':
                # Fechas
                if tipo == 'str_date':
                    df_resultado[col] = fecha_series.dt.strftime('%Y-%m-%d').fillna('')
                elif tipo == 'str_datetime':
                    df_resultado[col] = fecha_series.dt.strftime('%Y-%m-%d %H:%M:%S').fillna('')
            else:
                # Otras columnas
                if origen in df_raw.columns:
                    if tipo == 'int':
                        df_resultado[col] = df_raw[origen].apply(safe_int)
                    else:
                        df_resultado[col] = df_raw[origen].apply(safe_str)
                else:
                    # Columna no encontrada en JSON, usar default
                    if tipo == 'int':
                        df_resultado[col] = default
                    else:
                        df_resultado[col] = default
        
        # Asegurar tipos correctos
        int_columns = ['TELEPHONE', 'Contacto_Identificado_Robot', 'Gestion_Humano', 
                       'Contacto_Identificado_Humano', 'Venta_Humano_Identificado']
        for col in int_columns:
            if col in df_resultado.columns:
                df_resultado[col] = pd.to_numeric(df_resultado[col], errors='coerce').fillna(0).astype('int64')
        
        str_columns = [col for col in df_resultado.columns if col not in int_columns]
        for col in str_columns:
            df_resultado[col] = df_resultado[col].astype('object')
        
        logger.info(f"✅ DataFrame creado con {len(df_resultado)} registros y {len(df_resultado.columns)} columnas")
        return df_resultado
        
    except Exception as e:
        logger.error(f"❌ Error creando DataFrame: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

def subir_a_bigquery(df: pd.DataFrame) -> bool:
    """Sube el DataFrame a BigQuery usando WRITE_APPEND."""
    try:
        if df.empty:
            logger.warning("⚠️ DataFrame vacío, no se subirá nada.")
            return True
        
        credentials, project = obtener_credenciales()
        client = bigquery.Client(project=project, credentials=credentials)
        
        # Verificar que las columnas coinciden con la tabla
        try:
            table = client.get_table(FULL_TABLE)
            columnas_bq = [col.name for col in table.schema]
            columnas_df = list(df.columns)
            columnas_validas = [col for col in columnas_df if col in columnas_bq]
            if len(columnas_validas) < len(columnas_df):
                logger.warning(f"⚠️ Se eliminarán columnas no existentes en la tabla: {set(columnas_df) - set(columnas_bq)}")
                df = df[columnas_validas]
        except NotFound:
            logger.info("ℹ️ La tabla no existe, se creará automáticamente.")
        
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            autodetect=True,
        )
        
        load_job = client.load_table_from_dataframe(df, FULL_TABLE, job_config=job_config)
        load_job.result()
        
        logger.info(f"✅ BigQuery: {len(df)} registros subidos (append) a {FULL_TABLE}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo a BigQuery: {e}")
        import traceback
        traceback.print_exc()
        return False

def subir_json_a_bigquery(data: dict, servidor: str, fecha: str, tipo_reporte: str = "CDR") -> bool:
    """
    Función principal: procesa el JSON, crea el DataFrame con todas las columnas
    y lo sube a BigQuery.
    """
    try:
        # Obtener columnas de la tabla
        columnas_tabla = obtener_esquema_tabla()
        
        # Crear DataFrame con datos crudos (NO resumen del embudo)
        df = crear_dataframe_crudo(data, servidor, fecha, columnas_tabla)
        
        if df.empty:
            logger.warning(f"⚠️ No se pudo crear el DataFrame para {servidor} - {fecha}")
            return False
        
        return subir_a_bigquery(df)
        
    except Exception as e:
        logger.error(f"❌ Error en subir_json_a_bigquery: {e}")
        import traceback
        traceback.print_exc()
        return False