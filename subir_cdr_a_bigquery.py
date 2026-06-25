#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bigquery_processor.py  (v8.4 — USA CAMPAIGN_ID DESDE EXCEL DE CAMPAÑAS)
========================================================================
El campaign_id correcto viene de los Excel de campañas (carpeta campanas/),
no del CDR. Este script hace el match usando Contacto__c = customer_id.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
import warnings
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

# ─────────────────────────────────────────────────────────────────────────────
# SUPRIMIR WARNING
# ─────────────────────────────────────────────────────────────────────────────
warnings.filterwarnings(
    "ignore",
    message=".*BigQuery Storage module not found.*",
    category=UserWarning,
)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ID        = "capable-arbor-209819"
TABLE_CONSOLIDADO = f"{PROJECT_ID}.Temporal.Embudo_Consolidado"
TABLE_POSITIVOS   = f"{PROJECT_ID}.Temporal.Embudo_Positivo_Robot_TEST"

BASE_DIR_CRED     = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH  = os.path.join(BASE_DIR_CRED, "config", "google_key.json")
BASE_DIR_DRIVE    = r"G:\Unidades compartidas\Analitica\Embudo de Conversión\Proyecto Robot Omnicanal\Producción\2026-06\Resultados"

TIPIFICACIONES_ROBOT: list = [
    "CV_CONT_RECP",
    "CV_CONT_FILTRO",
    "102",
    "103",
    "CV_RESPONDE_POSITIVO",
    "CV_CUELGA",
]

MAX_RETRIES    = 3
RETRY_BASE_SEC = 5


# ─────────────────────────────────────────────────────────────────────────────
# CREDENCIALES Y HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def obtener_credenciales():
    if os.path.exists(CREDENTIALS_PATH):
        try:
            creds = service_account.Credentials.from_service_account_file(
                CREDENTIALS_PATH,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            logger.info(f"✅ Credenciales cargadas desde: {CREDENTIALS_PATH}")
            return creds
        except Exception as exc:
            logger.warning(f"⚠️ Error cargando credenciales JSON: {exc}")
    try:
        from google.auth import default as gauth_default
        creds, _ = gauth_default()
        logger.info("✅ Credenciales desde ADC (gcloud).")
        return creds
    except Exception as exc:
        logger.error(f"❌ No se pudieron obtener credenciales: {exc}")
        return None


def _bq_client(credentials):
    return bigquery.Client(project=PROJECT_ID, credentials=credentials)


def _retry_bq_operation(operation, description: str, *args, **kwargs):
    last_exc = None
    for intento in range(1, MAX_RETRIES + 1):
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = RETRY_BASE_SEC * (2 ** (intento - 1))
            logger.warning(
                f"Intento {intento}/{MAX_RETRIES} falló en '{description}': {exc}. "
                f"Reintentando en {wait}s..."
            )
            time.sleep(wait)
    logger.error(f"Se agotaron {MAX_RETRIES} reintentos para '{description}'.")
    raise last_exc


def _run_query(client, sql: str) -> pd.DataFrame:
    job = client.query(sql)
    return job.result().to_dataframe()


def _run_dml(client, sql: str, description: str = "DML") -> int:
    def _exec(s, d):
        job = client.query(s)
        job.result()
        return job.num_dml_affected_rows
    rows = _retry_bq_operation(_exec, description, sql, description)
    logger.info(f"   {description}: {rows:,} filas afectadas.")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE ARCHIVOS EXCEL (CAMPAÑAS)
# ─────────────────────────────────────────────────────────────────────────────

def leer_campanas_excel(fecha: str) -> pd.DataFrame:
    """
    Lee los archivos de campañas y extrae campaign_id y customer_id.
    """
    mes = fecha[:7]
    carpeta_campanas = os.path.join(BASE_DIR_DRIVE, mes, fecha, "campanas")
    
    if not os.path.exists(carpeta_campanas):
        logger.warning(f"⚠️ Carpeta de campañas no existe: {carpeta_campanas}")
        return pd.DataFrame()
    
    archivos = [f for f in os.listdir(carpeta_campanas) 
                if f.endswith('.xlsx') and not f.startswith('~')]
    
    if not archivos:
        logger.warning(f"⚠️ No hay archivos de campañas en: {carpeta_campanas}")
        return pd.DataFrame()
    
    logger.info(f"   📁 Encontrados {len(archivos)} archivos de campañas")
    
    lista_dfs = []
    for archivo in archivos:
        try:
            ruta = os.path.join(carpeta_campanas, archivo)
            df = pd.read_excel(ruta)
            lista_dfs.append(df)
            logger.info(f"      ✅ {archivo}: {len(df)} registros")
        except Exception as e:
            logger.warning(f"      ⚠️ Error leyendo {archivo}: {e}")
    
    if not lista_dfs:
        return pd.DataFrame()
    
    df_campanas = pd.concat(lista_dfs, ignore_index=True)
    
    # Normalizar nombres de columnas a mayúsculas
    df_campanas.columns = [col.upper() for col in df_campanas.columns]
    
    logger.info(f"   📋 Columnas disponibles en campañas: {list(df_campanas.columns)}")
    
    # Buscar columnas
    col_id = None
    col_customer = None
    
    for col in df_campanas.columns:
        if 'CAMPAIGN' in col and ('ID' in col or 'ID_CAMPAÑA' in col):
            col_id = col
        if 'CUSTOMER' in col and 'ID' in col:
            col_customer = col
    
    # Si no se encontraron, buscar por nombres comunes
    if col_id is None:
        for col in ['CAMPAIGN_ID', 'CAMPAING_ID', 'ID_CAMPAÑA', 'ID_CAMPAIGN']:
            if col in df_campanas.columns:
                col_id = col
                break
    
    # Si no se encontró campaign_id, usar la primera columna
    if col_id is None and len(df_campanas.columns) >= 1:
        col_id = df_campanas.columns[0]
        logger.info(f"   Usando '{col_id}' como campaign_id (por defecto)")
    
    # Si no se encontró customer_id, usar la segunda columna
    if col_customer is None and len(df_campanas.columns) >= 2:
        col_customer = df_campanas.columns[1]
        logger.info(f"   Usando '{col_customer}' como customer_id (por defecto)")
    
    if col_id is None or col_customer is None:
        logger.warning(f"⚠️ No se encontraron columnas necesarias. Columnas: {list(df_campanas.columns)}")
        return pd.DataFrame()
    
    # Extraer columnas
    df_result = df_campanas[[col_id, col_customer]].copy()
    df_result.columns = ['campaign_id', 'customer_id']
    
    # Limpiar campaign_id
    df_result['campaign_id'] = df_result['campaign_id'].astype(str).str.strip()
    df_result['campaign_id'] = df_result['campaign_id'].str.rstrip('-')
    df_result['campaign_id'] = df_result['campaign_id'].str.rstrip()
    df_result['campaign_id'] = df_result['campaign_id'].str.replace('-', '')
    
    # Limpiar customer_id
    df_result['customer_id'] = df_result['customer_id'].astype(str).str.strip()
    
    # Eliminar duplicados y valores vacíos
    df_result = df_result.drop_duplicates(subset=['customer_id'])
    df_result = df_result[df_result['customer_id'] != '']
    df_result = df_result[df_result['customer_id'] != 'nan']
    
    # Crear campaign_name como NULL
    df_result['campaign_name'] = None
    
    logger.info(f"   📋 Campañas únicas por customer: {len(df_result)}")
    logger.info(f"   📋 Ejemplo: {df_result.head(3).to_dict('records')}")
    
    return df_result


def consultar_cdr(fecha: str, client) -> pd.DataFrame:
    """
    Lee CDR crudos de Temporal.Embudo_Consolidado.
    NOTA: El campaign_id de aquí no es confiable, lo reemplazaremos.
    """
    sql = f"""
        SELECT
            Fecha_dia,
            DATE,
            TELEPHONE,
            COD_ACT,
            Operado_Por__c,
            CONN_ID,
            Contacto__c,
            servidor
        FROM `{TABLE_CONSOLIDADO}`
        WHERE Fecha_dia = '{fecha}'
          AND tipo_reporte = 'CDR'
    """
    try:
        df = _run_query(client, sql)
        logger.info(f"📊 CDR leídos: {len(df):,} registros para {fecha}")
        return df
    except Exception as exc:
        logger.error(f"❌ Error leyendo CDR: {exc}")
        return pd.DataFrame()


def consultar_auxiliares(mes_inicio: str, client) -> dict:
    """
    Consulta las tablas auxiliares.
    """
    consultas = {
        "operador": f"""
            SELECT DISTINCT 
                Contacto__c, 
                Operado_Por__c
            FROM `{PROJECT_ID}.Campanas.Campanas_intradia`
            WHERE Contacto__c IS NOT NULL 
              AND Operado_Por__c IS NOT NULL
        """,
        "contacto": f"""
            SELECT DISTINCT Contacto__c, Fecha_dia, 1 AS Contacto_Identificado_Humano
            FROM `{PROJECT_ID}.Operacion_Analitica.GestionesTitular`
            WHERE Fecha_dia >= '{mes_inicio}'
        """,
        "gestion": f"""
            SELECT DISTINCT
                Contacto__c,
                SUBSTR(CAST(Fecha_Gestion__c AS STRING), 1, 10) AS Fecha_dia,
                1 AS Gestion_Humano
            FROM `{PROJECT_ID}.SalesForce.Gestion_Oportunidad__c`
            WHERE Fecha_Gestion__c >= '{mes_inicio}'
        """,
        "venta": f"""
            SELECT
                Contacto__c,
                CAST(Fecha_Acuerdo_de_Pago__c AS STRING) AS Fecha_dia,
                1 AS Venta_Humano
            FROM `{PROJECT_ID}.Tablas_Reporteria.reporteMes`
            WHERE Fecha_Acuerdo_de_Pago__c >= '{mes_inicio}'
              AND entidades_habilitadas = 'Habilitado'
              AND Estado_Base__c = 'Habilitado'
              AND Contacto__c IS NOT NULL
            GROUP BY Contacto__c, Fecha_dia
        """,
    }

    resultados = {}
    for nombre, sql in consultas.items():
        try:
            df = _run_query(client, sql)
            if "Fecha_dia" in df.columns:
                df["Fecha_dia"] = df["Fecha_dia"].astype(str).str[:10]
            logger.info(f"   📋 {nombre}: {len(df):,} registros")
            resultados[nombre] = df
        except Exception as exc:
            logger.error(f"❌ Error consultando auxiliar '{nombre}': {exc}")
            resultados[nombre] = pd.DataFrame()
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# CALCULO DE GRUPO OPERADOR
# ─────────────────────────────────────────────────────────────────────────────

def calcular_grupo_operador(operado_por) -> str:
    if pd.isna(operado_por) or str(operado_por).strip() == "":
        return "OTRO"
    
    op = str(operado_por).strip().upper()
    
    # 🔥 LISTA COMPLETA DE GRUPOS
    # DIGITAL
    if op in ["DIGITAL_2", "DIGITAL_MONTOS_BAJOS", "DIGITAL_1", "DIGITAL_ESPECIAL"]:
        return "DIGITAL"
    
    # RBK
    if op in ["QNT_RBK_2", "QNT_RBK_1.2", "QNT_RBK_1.1"]:
        return "RBK"
    
    # MONTOS_ALTOS
    if op in ["QNT_RECAUDO", "QNT_PERSONA_JURIDICA", "QNT_JUD"]:
        return "MONTOS_ALTOS"
    
    # SATELITES
    if op in ["GENNIALS_BPO_2", "GENNIALS_BPO", "HELLO_BPO", 
              "MAPNOVA", "ESTA_BIEN_GROUP", "QNT_COBRO"]:
        return "SATELITES"
    
    # MANTENIMIENTO (cualquier valor que contenga "MANT")
    if "MANT" in op:
        return "MANTENIMIENTO"
    
    # Si no coincide con nada
    return "OTRO"

# ─────────────────────────────────────────────────────────────────────────────
# PROCESAMIENTO DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

def procesar_datos(
    df_cdr: pd.DataFrame,
    fecha: str,
    auxiliares: dict,
    df_campanas: pd.DataFrame,
) -> pd.DataFrame:
    if df_cdr.empty:
        return pd.DataFrame()

    df = df_cdr.copy()

    # ── Normalizar tipos ──────────────────────────────────────────────────
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    if "Contacto__c" in df.columns:
        df["Contacto__c"] = df["Contacto__c"].astype(str).str.strip()
    if "COD_ACT" in df.columns:
        df["COD_ACT"] = df["COD_ACT"].astype(str).str.strip()
    
    # ── OBTENER CAMPAIGN_ID DESDE LOS EXCEL DE CAMPAÑAS ──────────────────
    # Usamos Contacto__c (customer_id) para hacer el match
    df["campaign_id"] = None
    df["campaign_name"] = None
    
    if not df_campanas.empty:
        # Crear diccionario para mapeo rápido
        campanas_dict = df_campanas.set_index('customer_id').to_dict('index')
        
        # Asignar campaign_id a cada registro
        def get_campaign_id(contacto):
            contacto_str = str(contacto).strip()
            if contacto_str in campanas_dict:
                return campanas_dict[contacto_str]['campaign_id']
            return None
        
        def get_campaign_name(contacto):
            contacto_str = str(contacto).strip()
            if contacto_str in campanas_dict:
                return campanas_dict[contacto_str]['campaign_name']
            return None
        
        df["campaign_id"] = df["Contacto__c"].apply(get_campaign_id)
        df["campaign_name"] = df["Contacto__c"].apply(get_campaign_name)
        
        # Limpiar campaign_id
        df["campaign_id"] = df["campaign_id"].astype(str).str.strip()
        df["campaign_id"] = df["campaign_id"].str.rstrip('-')
        df["campaign_id"] = df["campaign_id"].str.rstrip()
        df["campaign_id"] = df["campaign_id"].str.replace('-', '')
        df["campaign_id"] = df["campaign_id"].replace('None', None)
        df["campaign_id"] = df["campaign_id"].replace('nan', None)
        
        logger.info(
            f"   📋 Campaign IDs asignados: {df['campaign_id'].notna().sum():,} / {len(df):,}"
        )
        if df['campaign_id'].notna().sum() > 0:
            logger.info(f"   📋 Ejemplo campaign_id: {df[df['campaign_id'].notna()]['campaign_id'].head(5).tolist()}")

    # ── INICIALIZAR COLUMNAS DE MÉTRICAS ──────────────────────────────
    df["Contacto_Identificado_Humano"] = 0
    df["Gestion_Humano"] = 0
    df["Venta_Humano"] = 0

    # ── Merge con operador ──────────────────────────────────────────────
    df_op = auxiliares.get("operador", pd.DataFrame())
    if not df_op.empty and "Contacto__c" in df_op.columns:
        df_op["Contacto__c"] = df_op["Contacto__c"].astype(str).str.strip()
        df_op_uniq = df_op.drop_duplicates(subset=["Contacto__c"])
        df = df.merge(
            df_op_uniq[["Contacto__c", "Operado_Por__c"]],
            on="Contacto__c",
            how="left",
            suffixes=("", "_op"),
        )
        if "Operado_Por__c_op" in df.columns:
            df["Operado_Por__c"] = df["Operado_Por__c"].fillna(df["Operado_Por__c_op"])
            df.drop(columns=["Operado_Por__c_op"], inplace=True)
        logger.info(
            f"   Merge operador: {df['Operado_Por__c'].notna().sum():,} / {len(df):,}"
        )

    # ── Fecha_dia ────────────────────────────────────────────────────────
    if "Fecha_dia" in df.columns:
        df["Fecha_dia"] = df["Fecha_dia"].astype(str).str[:10]
    else:
        df["Fecha_dia"] = fecha

    # ── Merge con contacto humano ──────────────────────────────────────
    df_contacto = auxiliares.get("contacto", pd.DataFrame())
    if not df_contacto.empty:
        df_contacto["Contacto__c"] = df_contacto["Contacto__c"].astype(str).str.strip()
        df = df.merge(
            df_contacto[["Contacto__c", "Fecha_dia", "Contacto_Identificado_Humano"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        if "Contacto_Identificado_Humano_y" in df.columns:
            df["Contacto_Identificado_Humano"] = df["Contacto_Identificado_Humano_y"].fillna(0)
            df.drop(columns=["Contacto_Identificado_Humano_y"], inplace=True)

    # ── Merge con gestion humana ──────────────────────────────────────
    df_gestion = auxiliares.get("gestion", pd.DataFrame())
    if not df_gestion.empty:
        df_gestion["Contacto__c"] = df_gestion["Contacto__c"].astype(str).str.strip()
        df = df.merge(
            df_gestion[["Contacto__c", "Fecha_dia", "Gestion_Humano"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        if "Gestion_Humano_y" in df.columns:
            df["Gestion_Humano"] = df["Gestion_Humano_y"].fillna(0)
            df.drop(columns=["Gestion_Humano_y"], inplace=True)

    # ── Merge con ventas ──────────────────────────────────────────────
    df_venta = auxiliares.get("venta", pd.DataFrame())
    if not df_venta.empty:
        df_venta["Contacto__c"] = df_venta["Contacto__c"].astype(str).str.strip()
        df = df.merge(
            df_venta[["Contacto__c", "Fecha_dia", "Venta_Humano"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        if "Venta_Humano_y" in df.columns:
            df["Venta_Humano"] = df["Venta_Humano_y"].fillna(0)
            df.drop(columns=["Venta_Humano_y"], inplace=True)

    # ── Asegurar tipos correctos ──────────────────────────────────────
    df["Contacto_Identificado_Humano"] = pd.to_numeric(df["Contacto_Identificado_Humano"], errors="coerce").fillna(0).astype("int64")
    df["Gestion_Humano"] = pd.to_numeric(df["Gestion_Humano"], errors="coerce").fillna(0).astype("int64")
    df["Venta_Humano"] = pd.to_numeric(df["Venta_Humano"], errors="coerce").fillna(0).astype("int64")

    # ── Calcular flags ──────────────────────────────────────────────────
    df["Contacto_Identificado_Robot"] = np.where(
        df["COD_ACT"].isin(TIPIFICACIONES_ROBOT), 1, 0
    ).astype("int64")

    df["Gestion_Humano"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1) & (df["Gestion_Humano"] == 1),
        1, 0
    ).astype("int64")

    df["Contacto_Identificado_Humano"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1) & (df["Contacto_Identificado_Humano"] == 1),
        1, 0
    ).astype("int64")

    df["Venta_Humano_Identificado"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1)
        & (df["Contacto_Identificado_Humano"] == 1)
        & (df["Venta_Humano"] == 1),
        1, 0
    ).astype("int64")

    # ── Grupo_Operador ──────────────────────────────────────────────────
    df["Grupo_Operador"] = df["Operado_Por__c"].apply(calcular_grupo_operador)

    logger.info(
        f"   Procesamiento: {len(df):,} registros | "
        f"Robot: {int(df['Contacto_Identificado_Robot'].sum()):,} | "
        f"Gestion: {int(df['Gestion_Humano'].sum()):,} | "
        f"Contacto: {int(df['Contacto_Identificado_Humano'].sum()):,} | "
        f"Venta: {int(df['Venta_Humano_Identificado'].sum()):,}"
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SUBIR SOLO ROBOTS A LA TABLA DE PRUEBA
# ─────────────────────────────────────────────────────────────────────────────

def subir_a_embudo_positivo(
    df_procesado: pd.DataFrame,
    fecha: str,
    client,
) -> bool:
    if df_procesado.empty:
        return False

    # FILTRO: SOLO REGISTROS CON Contacto_Identificado_Robot = 1
    df_robots = df_procesado.copy()
    
    if df_robots.empty:
        logger.info(f"⚠️ No hay robots para {fecha}. No se sube nada.")
        return True

    logger.info(f"   🤖 Subiendo solo {len(df_robots):,} registros con Robot=1")

    # Columnas a subir
    columnas_subir = [
        "Fecha_dia", "DATE", "TELEPHONE", "COD_ACT",
        "Grupo_Operador", "Operado_Por__c", "CONN_ID", "Contacto__c",
        "Contacto_Identificado_Robot",
        "Gestion_Humano",
        "Contacto_Identificado_Humano",
        "Venta_Humano_Identificado",
        "campaign_id",
        "campaign_name",
    ]

    cols_disponibles = [c for c in columnas_subir if c in df_robots.columns]
    df_subir = df_robots[cols_disponibles].copy()

    # Limpieza de tipos
    for col in ["Contacto_Identificado_Robot", "Gestion_Humano",
                "Contacto_Identificado_Humano", "Venta_Humano_Identificado"]:
        if col in df_subir.columns:
            df_subir[col] = pd.to_numeric(df_subir[col], errors="coerce").fillna(0).astype("int64")

    # campaign_id a string
    if "campaign_id" in df_subir.columns:
        df_subir["campaign_id"] = df_subir["campaign_id"].astype(str).replace('None', None).replace('nan', None)

    for col in df_subir.columns:
        if df_subir[col].dtype == object:
            df_subir[col] = df_subir[col].where(df_subir[col].notna(), None)

    try:
        # DELETE de la fecha
        sql_delete = f"""
            DELETE FROM `{TABLE_POSITIVOS}`
            WHERE Fecha_dia = '{fecha}'
        """
        logger.info(f"🗑️  Eliminando registros de {fecha} en {TABLE_POSITIVOS}...")
        _run_dml(client, sql_delete, "DELETE TEST")

        # INSERT
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        logger.info(f"📤 Subiendo {len(df_subir):,} registros a {TABLE_POSITIVOS}...")

        def _load(df, table, config):
            job = client.load_table_from_dataframe(df, table, job_config=config)
            job.result()
            return job

        job = _retry_bq_operation(
            _load, "LOAD TEST", df_subir, TABLE_POSITIVOS, job_config
        )
        logger.info(f"   ✅ Upload OK. Job ID: {job.job_id}")
        return True

    except Exception as exc:
        logger.error(f"❌ Error subiendo a {TABLE_POSITIVOS}: {exc}")
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# FUNCION PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def procesar_y_actualizar_bigquery(
    base_dir: Optional[str] = None,
    fecha: Optional[str] = None,
) -> bool:
    try:
        if fecha is None:
            fecha = datetime.now().strftime("%Y-%m-%d")
        mes_inicio = fecha[:7] + "-01"

        sep = "=" * 65
        logger.info(sep)
        logger.info(f"🚀 INICIO — PROCESAMIENTO CDR (CAMPAIGN_ID DESDE EXCEL) | fecha={fecha}")
        logger.info(sep)

        credentials = obtener_credenciales()
        if credentials is None:
            logger.error("❌ No se pudieron obtener credenciales.")
            return False
        client = _bq_client(credentials)

        # 1. Leer campañas desde Excel
        logger.info("─ Paso 1/4: Leer campañas desde Excel...")
        df_campanas = leer_campanas_excel(fecha)
        if not df_campanas.empty:
            logger.info(f"   ✅ {len(df_campanas)} campañas únicas cargadas")

        # 2. Leer CDR (sin campaign_id)
        logger.info("─ Paso 2/4: Leer CDR crudos...")
        df_cdr = consultar_cdr(fecha, client)
        if df_cdr.empty:
            logger.warning(f"⚠️ Sin CDR para {fecha}.")
            return False

        # 3. Auxiliares
        logger.info("─ Paso 3/4: Consultar tablas auxiliares...")
        auxiliares = consultar_auxiliares(mes_inicio, client)

        # 4. Procesar y subir
        logger.info("─ Paso 4/4: Calcular métricas y subir SOLO ROBOTS...")
        df_procesado = procesar_datos(df_cdr, fecha, auxiliares, df_campanas)
        if df_procesado.empty:
            logger.error("❌ Procesamiento no generó datos.")
            return False

        if not subir_a_embudo_positivo(df_procesado, fecha, client):
            logger.error("❌ Falló la subida a la tabla de prueba.")
            return False

        logger.info(sep)
        logger.info(f"🏁 FIN — ✅ ÉXITO  |  fecha={fecha}")
        logger.info(sep)
        return True

    except Exception as exc:
        logger.error(f"❌ Error fatal: {exc}")
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fecha_arg = sys.argv[1] if len(sys.argv) > 1 else None
    exito = procesar_y_actualizar_bigquery(fecha=fecha_arg)
    sys.exit(0 if exito else 1)