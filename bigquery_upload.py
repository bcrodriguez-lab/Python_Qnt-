#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Módulo para procesar archivos XLSX descargados y subirlos a BigQuery.
Busca archivos tanto en la raíz de la fecha (CDR) como en la subcarpeta 'campanas' (AMD).
"""

import os
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from google.oauth2 import service_account
from google.auth import default
from google.cloud import bigquery
import json
import re

# Configurar logging
logger = logging.getLogger(__name__)

# ========== CONFIGURACIÓN ==========
PROJECT_ID = "capable-arbor-209819"
DATASET_ID = "Temporal"
TABLE_NAME = "Temporal_Robot_Campañas"
FULL_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}"

# Ruta al archivo de credenciales
BASE_DIR_CRED = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR_CRED, "config", "google_key.json")

def cargar_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

CONFIG = cargar_config()
BASE_DIR = CONFIG.get('base_dir', r"G:\Unidades compartidas\Analitica\Embudo de Conversión\Proyecto Robot Omnicanal\Producción")

def obtener_credenciales():
    if os.path.exists(CREDENTIALS_PATH):
        try:
            credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)
            project = "QNTAnalytics"  # <--- Forzar el proyecto correcto
            logger.info(f"✅ Usando proyecto: {project}")
            return credentials, project
        except Exception as e:
            logger.warning(f"⚠️ Error cargando credenciales: {e}")

def leer_archivos_desde_ruta(ruta: str):
    """Lee todos los archivos XLSX de una ruta específica (sin subcarpetas)"""
    if not os.path.exists(ruta):
        return None
    
    files = [f for f in os.listdir(ruta) if f.lower().endswith((".xlsx", ".xls")) and os.path.isfile(os.path.join(ruta, f))]
    if not files:
        return None
    
    logger.info(f"📄 Encontrados {len(files)} archivos en: {os.path.basename(ruta)}")
    
    lista_bases = []
    for archivo in files:
        ruta_archivo = os.path.join(ruta, archivo)
        try:
            df = pd.read_excel(ruta_archivo, engine='openpyxl')
            lista_bases.append(df)
        except Exception as e:
            logger.error(f"❌ Error leyendo {archivo}: {e}")
    
    if not lista_bases:
        return None
    
    df_final = pd.concat(lista_bases, ignore_index=True)
    return df_final

def procesar_y_subir_a_bigquery(fecha: str = None) -> bool:
    """
    Procesa los archivos XLSX de una fecha y los sube a BigQuery.
    Busca en:
    1. Raíz de la fecha (archivos CDR: servidor_fecha.xlsx)
    2. Subcarpeta 'campanas' (archivos AMD: servidor_campaign_all_fecha.xlsx)
    """
    try:
        if fecha is None:
            fecha = datetime.now().strftime("%Y-%m-%d")
        
        mes = fecha[:7]
        ruta_raiz = os.path.join(BASE_DIR, mes, fecha)
        ruta_campanas = os.path.join(BASE_DIR, mes, fecha, "campanas")
        
        logger.info(f"📥 Procesando archivos para {fecha}")
        logger.info(f"📁 Buscando en raíz: {ruta_raiz}")
        logger.info(f"📁 Buscando en campanas: {ruta_campanas}")
        
        credentials, project = obtener_credenciales()
        
        # 1. Leer archivos de la RAIZ (CDR)
        df_final = leer_archivos_desde_ruta(ruta_raiz)
        
        # 2. Si no hay en raíz, intentar desde campanas (AMD)
        if df_final is None or len(df_final) == 0:
            logger.info(f"📁 No hay archivos en raíz, buscando en campanas...")
            df_final = leer_archivos_desde_ruta(ruta_campanas)
        
        if df_final is None or len(df_final) == 0:
            logger.warning(f"⚠️ No se encontraron archivos para {fecha}")
            return False
        
        logger.info(f"✅ {len(df_final)} registros procesados")
        
        # 3. Crear DataFrame para BigQuery
        df_resultado = crear_dataframe_bigquery(df_final, fecha)
        
        # 4. Subir a BigQuery
        if df_resultado is not None and len(df_resultado) > 0:
            resultado = subir_a_bigquery(df_resultado, credentials)
            if resultado:
                logger.info(f"✅ Datos subidos a {FULL_TABLE} para {fecha}")
                return True
            else:
                logger.warning(f"⚠️ No se pudieron subir datos")
                return False
        else:
            logger.warning(f"⚠️ No hay datos para subir")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def crear_dataframe_bigquery(df_final: pd.DataFrame, fecha: str):
    """Crea un DataFrame con el formato esperado para BigQuery."""
    try:
        df_resultado = pd.DataFrame()
        df_resultado['Fecha_dia'] = fecha
        df_resultado['Fecha_Procesamiento'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df_resultado['Fecha_Reporte'] = fecha
        
        # Buscar columnas importantes
        columnas_importantes = [
            'CUSTOMER_ID', 'customer_id', 'ID_CLIENTE', 'id_cliente',
            'CAMPAIGN_ID', 'campaign_id', 'DATE', 'date',
            'Hora', 'hora', 'RESULT', 'result', 'TELEPHONE', 'telephone'
        ]
        
        for col in columnas_importantes:
            if col in df_final.columns:
                df_resultado[col] = df_final[col].astype(str)
            else:
                found = False
                for col_final in df_final.columns:
                    if col_final.lower() == col.lower():
                        df_resultado[col] = df_final[col_final].astype(str)
                        found = True
                        break
                if not found:
                    df_resultado[col] = ''
        
        # Columna servidor
        if 'servidor' in df_final.columns:
            df_resultado['servidor'] = df_final['servidor'].astype(str)
        else:
            df_resultado['servidor'] = 'desconocido'
        
        # Métricas
        df_resultado['Cantidad_Llamados'] = len(df_final)
        df_resultado['Cantidad_Robot'] = 0
        df_resultado['Cantidad_Humano'] = 0
        df_resultado['Cantidad_Venta'] = 0
        
        if 'RESULT' in df_final.columns:
            df_resultado['Cantidad_Robot'] = (df_final['RESULT'].str.lower() == 'machine').sum()
            df_resultado['Cantidad_Humano'] = (df_final['RESULT'].str.lower() == 'human').sum()
        
        logger.info(f"✅ DataFrame creado con {len(df_resultado)} registros")
        return df_resultado
        
    except Exception as e:
        logger.error(f"❌ Error creando DataFrame: {e}")
        import traceback
        traceback.print_exc()
        return None

def subir_a_bigquery(df: pd.DataFrame, credentials) -> bool:
    """Sube el DataFrame a BigQuery."""
    try:
        client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
        
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            autodetect=True,
        )
        
        load_job = client.load_table_from_dataframe(df, FULL_TABLE, job_config=job_config)
        load_job.result()
        
        logger.info(f"✅ {len(df)} registros subidos a {FULL_TABLE}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo a BigQuery: {e}")
        return False

def procesar_rango_fechas(fecha_inicio: str, fecha_fin: str) -> dict:
    """Procesa un rango de fechas."""
    resultados = {'total': 0, 'exitosos': 0, 'fallidos': 0, 'fechas': []}
    
    fecha_actual = datetime.strptime(fecha_inicio, "%Y-%m-%d")
    fecha_fin_dt = datetime.strptime(fecha_fin, "%Y-%m-%d")
    
    while fecha_actual <= fecha_fin_dt:
        fecha_str = fecha_actual.strftime("%Y-%m-%d")
        logger.info(f"\n📅 Procesando: {fecha_str}")
        
        try:
            resultado = procesar_y_subir_a_bigquery(fecha_str)
            if resultado:
                resultados['exitosos'] += 1
                resultados['fechas'].append({'fecha': fecha_str, 'estado': 'exitoso'})
            else:
                resultados['fallidos'] += 1
                resultados['fechas'].append({'fecha': fecha_str, 'estado': 'fallido'})
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            resultados['fallidos'] += 1
            resultados['fechas'].append({'fecha': fecha_str, 'estado': 'error'})
        
        resultados['total'] += 1
        fecha_actual += timedelta(days=1)
    
    return resultados

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fecha_prueba = datetime.now().strftime("%Y-%m-%d")
    print(f"🧪 Probando subida a BigQuery para {fecha_prueba}")
    print("="*50)
    procesar_y_subir_a_bigquery(fecha_prueba)