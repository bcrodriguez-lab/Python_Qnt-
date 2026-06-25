#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bigquery_processor.py  (v10.10 — DEFINITIVO CON CAMPANAS_DESAROLLO)
===============================================================================
PROCESA TODOS LOS CDR CON TODOS LOS DATOS
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

SCHEMA_POSITIVOS = {
    "Fecha_dia":                    "STRING",
    "DATE":                         "DATETIME",
    "Rango_Horario":                "STRING",
    "Hora":                         "INT64",
    "Dia_Semana":                   "STRING",
    "Numero_Dia_Semana":            "INT64",
    "TELEPHONE":                    "INT64",
    "COD_ACT":                      "STRING",
    "Grupo_Operador":               "STRING",
    "Operado_Por__c":               "STRING",
    "CONN_ID":                      "STRING",
    "Contacto__c":                  "STRING",
    "campaign_id":                  "STRING",
    "campaign_name":                "STRING",
    "servidor":                     "STRING",
    "Gestion_Marcador":             "INT64",
    "Entidad_principal":            "STRING",
    "localizado_historico":         "STRING",
    "Contacto_Identificado_Robot":  "INT64",
    "Gestion_Humano":               "INT64",
    "Contacto_Identificado_Humano": "INT64",
    "Venta_Humano_Identificado":    "INT64",
}

COLUMNAS_A_ELIMINAR = [
    'AGENT_NAME', 'COMMENT', 'COST',
    'DESCRIPTION_COD_ACT', 'DESCRIPTION_COD_ACT_2', 'DESTINY',
    'SKILL_NAME', 'SKILL_ID', 'TIME_MIN', 'TIME_SEG',
    'TYPE_INTERACTION', 'HANG_UP', 'COD_ACT_2',
]


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


def _run_ddl(client, sql: str, description: str = "DDL") -> None:
    def _exec(s):
        job = client.query(s)
        job.result()
        return job
    _retry_bq_operation(_exec, description, sql)
    logger.info(f"   {description}: OK")


def _asegurar_columnas_positivos(client):
    try:
        table = _retry_bq_operation(
            client.get_table, f"GET SCHEMA {TABLE_POSITIVOS}", TABLE_POSITIVOS
        )
        columnas_existentes = {field.name for field in table.schema}
    except Exception:
        logger.info(f"   Tabla {TABLE_POSITIVOS} no existe aun, se creara en el primer upload.")
        return

    columnas_faltantes = {
        col: tipo for col, tipo in SCHEMA_POSITIVOS.items()
        if col not in columnas_existentes
    }

    if not columnas_faltantes:
        logger.info(f"   Schema Positivos OK ({len(columnas_existentes)} columnas)")
        return

    logger.info(f"   Agregando {len(columnas_faltantes)} columnas faltantes a Positivos...")
    for col, tipo in columnas_faltantes.items():
        sql = f"ALTER TABLE `{TABLE_POSITIVOS}` ADD COLUMN IF NOT EXISTS {col} {tipo}"
        _run_ddl(client, sql, f"ADD COLUMN {col} {tipo}")


# ─────────────────────────────────────────────────────────────────────────────
# FUNCION PARA LIMPIAR NOMBRES DE CAMPAÑA
# ─────────────────────────────────────────────────────────────────────────────

def limpiar_nombre_campana(valor):
    """Quita el guion al inicio de un nombre de campaña."""
    if pd.isna(valor) or str(valor).strip() == "":
        return valor
    texto = str(valor).strip()
    if texto.startswith("- "):
        return texto[2:].strip()
    if texto.startswith("-"):
        return texto[1:].strip()
    return texto


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE CDR DESDE EXCEL
# ─────────────────────────────────────────────────────────────────────────────

def leer_cdr_excel(fecha: str) -> pd.DataFrame:
    mes = fecha[:7]
    carpeta_cdr = os.path.join(BASE_DIR_DRIVE, mes, fecha)

    if not os.path.exists(carpeta_cdr):
        logger.warning(f"❌ Carpeta de CDR no existe: {carpeta_cdr}")
        return pd.DataFrame()

    archivos = [
        f for f in os.listdir(carpeta_cdr)
        if f.endswith('.xlsx') and not f.startswith('~') and 'campaign' not in f.lower()
    ]

    if not archivos:
        logger.warning(f"❌ No hay archivos CDR en: {carpeta_cdr}")
        return pd.DataFrame()

    logger.info(f"   📁 Encontrados {len(archivos)} archivos CDR")

    lista_dfs = []
    for archivo in archivos:
        try:
            ruta = os.path.join(carpeta_cdr, archivo)
            df = pd.read_excel(ruta)
            df.columns = [col.upper() for col in df.columns]
            df['servidor'] = archivo.replace(f'_{fecha}.xlsx', '').replace('.xlsx', '')
            lista_dfs.append(df)
            logger.info(f"      ✅ {archivo}: {len(df)} registros")
        except Exception as e:
            logger.warning(f"      ⚠️ Error leyendo {archivo}: {e}")

    if not lista_dfs:
        return pd.DataFrame()

    df_cdr = pd.concat(lista_dfs, ignore_index=True)

    mapeo = {
        'CUSTOMER_ID': 'Contacto__c',
        'CAMPAIGN_ID': 'campaign_id',
        'DATE': 'DATE',
        'TELEPHONE': 'TELEPHONE',
        'COD_ACT': 'COD_ACT',
        'CONN_ID': 'CONN_ID',
    }
    for old, new in mapeo.items():
        if old in df_cdr.columns:
            df_cdr.rename(columns={old: new}, inplace=True)

    if 'Fecha_dia' not in df_cdr.columns:
        df_cdr['Fecha_dia'] = fecha

    for col in COLUMNAS_A_ELIMINAR:
        if col in df_cdr.columns:
            df_cdr.drop(columns=[col], inplace=True)

    logger.info(f"   📊 CDR consolidado: {len(df_cdr):,} registros")
    logger.info(f"   📋 Columnas CDR: {list(df_cdr.columns)}")
    
    if 'servidor' in df_cdr.columns:
        logger.info(f"   ✅ servidor: {df_cdr['servidor'].nunique()} únicos")
    
    return df_cdr


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE CAMPANAS DESDE EXCEL
# ─────────────────────────────────────────────────────────────────────────────

def leer_campanas_excel(fecha: str) -> pd.DataFrame:
    mes = fecha[:7]
    carpeta_campanas = os.path.join(BASE_DIR_DRIVE, mes, fecha, "campanas")

    if not os.path.exists(carpeta_campanas):
        logger.warning(f"❌ Carpeta de campañas no existe: {carpeta_campanas}")
        return pd.DataFrame()

    archivos = [
        f for f in os.listdir(carpeta_campanas)
        if f.endswith('.xlsx') and not f.startswith('~')
    ]

    if not archivos:
        logger.warning(f"❌ No hay archivos de campañas en: {carpeta_campanas}")
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
    df_campanas.columns = [col.upper() for col in df_campanas.columns]
    logger.info(f"   📋 Columnas en campañas: {list(df_campanas.columns)}")

    col_id = None
    col_name = None
    col_customer = None

    for col in df_campanas.columns:
        cl = col.upper()
        if 'CAMPAIGN' in cl and ('ID' in cl or 'ID_CAMPAÑA' in cl):
            col_id = col
        if 'CAMPAIGN' in cl and ('NAME' in cl or 'NOMBRE' in cl or 'CAMPA' in cl):
            col_name = col
        if 'CUSTOMER' in cl and 'ID' in cl:
            col_customer = col

    if col_id is None:
        for c in ['CAMPAIGN_ID', 'CAMPAING_ID', 'ID_CAMPAÑA', 'ID_CAMPAIGN']:
            if c in df_campanas.columns:
                col_id = c
                break

    if col_name is None:
        for c in ['CAMPAIGN_NAME', 'NOMBRE_CAMPAÑA', 'NAME', 'CAMPAÑA', 'CAMPAIGN']:
            if c in df_campanas.columns:
                col_name = c
                break

    if col_customer is None:
        for c in ['CUSTOMER_ID', 'ID_CLIENTE']:
            if c in df_campanas.columns:
                col_customer = c
                break

    if col_id is None and len(df_campanas.columns) >= 1:
        col_id = df_campanas.columns[0]
        logger.info(f"   Usando '{col_id}' como campaign_id (por defecto)")
    if col_customer is None and len(df_campanas.columns) >= 2:
        col_customer = df_campanas.columns[1]
        logger.info(f"   Usando '{col_customer}' como customer_id (por defecto)")

    if col_id is None or col_customer is None:
        logger.warning(f"⚠️ No se encontraron columnas necesarias. Disponibles: {list(df_campanas.columns)}")
        return pd.DataFrame()

    columnas_a_extraer = [col_id, col_customer]
    if col_name:
        columnas_a_extraer.append(col_name)

    df_result = df_campanas[columnas_a_extraer].copy()
    if col_name:
        df_result.columns = ['campaign_id_raw', 'customer_id', 'campaign_name_raw']
    else:
        df_result.columns = ['campaign_id_raw', 'customer_id']
        df_result['campaign_name_raw'] = None

    ids_limpios = []
    nombres_limpios = []

    for _, row in df_result.iterrows():
        id_raw = row['campaign_id_raw']
        name_raw = row.get('campaign_name_raw')

        id_final = None
        name_final = None

        id_str = str(id_raw).strip() if pd.notna(id_raw) else ''

        if id_str and id_str not in ('nan', ''):
            id_final = id_str.rstrip('-').rstrip()
            if ' ' in id_final:
                partes = id_final.split(' ', 1)
                id_final = partes[0].strip()
                if len(partes) > 1 and partes[1].strip():
                    name_final = partes[1].strip().rstrip('-').rstrip()
        else:
            if pd.notna(name_raw) and str(name_raw).strip() not in ('', 'nan'):
                name_str = str(name_raw).strip()
                if name_str.isdigit():
                    id_final = name_str
                elif ' ' in name_str:
                    partes = name_str.split(' ', 1)
                    if partes[0].isdigit():
                        id_final = partes[0].strip()
                        name_final = partes[1].strip().rstrip('-').rstrip()
                    else:
                        name_final = name_str.rstrip('-').rstrip()
                else:
                    name_final = name_str.rstrip('-').rstrip()

        ids_limpios.append(id_final)
        nombres_limpios.append(name_final)

    df_result['campaign_id'] = ids_limpios
    df_result['campaign_name'] = nombres_limpios
    df_result['Nombre_Campana'] = nombres_limpios

    df_result['campaign_id'] = (
        df_result['campaign_id'].astype(str).str.strip()
        .str.rstrip('-').str.rstrip().str.replace('-', '')
        .replace('None', None).replace('nan', None)
    )
    df_result['campaign_name'] = (
        df_result['campaign_name'].astype(str).str.strip()
        .str.rstrip('-').str.rstrip('_').str.rstrip()
        .replace('None', None).replace('nan', None).replace('', None)
    )
    df_result['Nombre_Campana'] = (
        df_result['Nombre_Campana'].astype(str).str.strip()
        .str.rstrip('-').str.rstrip('_').str.rstrip()
        .replace('None', None).replace('nan', None).replace('', None)
    )
    df_result['customer_id'] = (
        df_result['customer_id'].astype(str).str.strip()
        .replace('None', None).replace('nan', None)
    )

    df_result = df_result.drop_duplicates(subset=['customer_id'])
    df_result = df_result[df_result['customer_id'].notna() & (df_result['customer_id'] != '')]

    df_final = df_result[['campaign_id', 'customer_id', 'campaign_name', 'Nombre_Campana']].copy()
    logger.info(f"   📋 Campañas únicas por customer: {len(df_final)}")
    if not df_final.empty:
        logger.info(f"   📋 Ejemplo: {df_final.head(3).to_dict('records')}")
    return df_final


# ─────────────────────────────────────────────────────────────────────────────
# CONSULTA DE AUXILIARES DESDE BIGQUERY
# ─────────────────────────────────────────────────────────────────────────────

def consultar_auxiliares(fecha: str, mes_inicio: str, client) -> dict[str, pd.DataFrame]:
    consultas = {
        "intradia": f"""
            SELECT DISTINCT
                Contacto__c,
                Operado_Por__c,
                Entidad_principal,
                Name AS campaign_name_bq
            FROM `{PROJECT_ID}.Campanas.Campanas_intradia`
            WHERE Contacto__c IS NOT NULL
              AND Operado_Por__c IS NOT NULL
              AND Operado_Por__c != ''
        """,
        "campanas_desarollo": f"""
            SELECT DISTINCT
                TRIM(CAST(campaign_id AS STRING)) AS campaign_id,
                campaign_name AS campaign_name_desarollo
            FROM `{PROJECT_ID}.Operacion_Analitica.campanas_desarollo`
            WHERE campaign_id IS NOT NULL
              AND campaign_name IS NOT NULL
              AND campaign_name != ''
        """,
        "contacto": f"""
            SELECT DISTINCT
                Contacto__c,
                CAST(Fecha_dia AS STRING) AS Fecha_dia,
                1 AS Contacto_Identificado_Humano_aux
            FROM `{PROJECT_ID}.Operacion_Analitica.GestionesTitular`
            WHERE Fecha_dia >= '{mes_inicio}'
              AND Contacto__c IS NOT NULL
        """,
        "gestion": f"""
            SELECT DISTINCT
                Contacto__c,
                SUBSTR(CAST(Fecha_Gestion__c AS STRING), 1, 10) AS Fecha_dia,
                1 AS Gestion_Humano_aux
            FROM `{PROJECT_ID}.SalesForce.Gestion_Oportunidad__c`
            WHERE Fecha_Gestion__c >= '{mes_inicio}'
              AND Contacto__c IS NOT NULL
        """,
        "venta": f"""
            SELECT
                Contacto__c,
                CAST(Fecha_Acuerdo_de_Pago__c AS STRING) AS Fecha_dia,
                1 AS Venta_Humano_aux
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
            if "Contacto__c" in df.columns:
                df["Contacto__c"] = df["Contacto__c"].astype(str).str.strip()
            if "Fecha_dia" in df.columns:
                df["Fecha_dia"] = df["Fecha_dia"].astype(str).str[:10]
            if "campaign_id" in df.columns:
                df["campaign_id"] = df["campaign_id"].astype(str).str.strip()
            logger.info(f"   📋 Auxiliar {nombre}: {len(df):,} registros")
            resultados[nombre] = df
        except Exception as exc:
            logger.warning(f"⚠️ No se pudo consultar auxiliar '{nombre}': {exc}")
            resultados[nombre] = pd.DataFrame()

    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# CALCULO DE GRUPO OPERADOR
# ─────────────────────────────────────────────────────────────────────────────

def calcular_grupo_operador(operado_por) -> str:
    if pd.isna(operado_por) or str(operado_por).strip() == "":
        return "OTRO"
    op = str(operado_por).strip()
    if op in ["DIGITAL_2", "DIGITAL_MONTOS_BAJOS", "DIGITAL_1", "DIGITAL_ESPECIAL"]:
        return "DIGITAL"
    if op in ["QNT_RBK_2", "QNT_RBK_1.2", "QNT_RBK_1.1"]:
        return "RBK"
    if op in ["QNT_RECAUDO", "QNT_PERSONA_JURIDICA", "QNT_JUD"]:
        return "MONTOS_ALTOS"
    if op in ["GENNIALS_BPO_2", "GENNIALS_BPO", "HELLO_BPO",
              "MAPNOVA", "ESTA_BIEN_GROUP", "QNT_COBRO"]:
        return "SATELITES"
    if "MANT" in op.upper():
        return "MANTENIMIENTO"
    return "OTRO"


# ─────────────────────────────────────────────────────────────────────────────
# PROCESAMIENTO DE DATOS
# ─────────────────────────────────────────────────────────────────────────────
def procesar_datos(
    df_cdr: pd.DataFrame,
    fecha: str,
    df_campanas: pd.DataFrame,
    auxiliares: Optional[dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    if df_cdr.empty:
        return pd.DataFrame()

    df = df_cdr.copy()
    auxiliares = auxiliares or {}

    # Normalizar tipos
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    if "Contacto__c" in df.columns:
        df["Contacto__c"] = df["Contacto__c"].astype(str).str.strip()
    if "COD_ACT" in df.columns:
        df["COD_ACT"] = df["COD_ACT"].astype(str).str.strip()
    
    #Mapeo de fechas horas dias de la seman y rangos horarios
    if "DATE" in df.columns:
        # Hora: número entero (0-23) para cálculos
        df["Hora"] = df["DATE"].dt.hour
        
        # Rango Horario (basado en número)
        def get_rango_horario(hora):
            if 8 <= hora < 10:
                return "8-10 AM"
            elif 10 <= hora < 12:
                return "10-12 AM"
            elif 12 <= hora < 14:
                return "12-2 PM"
            elif 14 <= hora < 16:
                return "2-4 PM"
            elif 16 <= hora < 18:
                return "4-6 PM"
            else:
                return "6-8 PM"
        
        df["Rango_Horario"] = df["Hora"].apply(get_rango_horario)
        
        # Día de la semana (en inglés, puedes cambiar a español)
        df["Dia_Semana"] = df["DATE"].dt.day_name(locale='es_ES')
        df["Dia_Semana"] = df["Dia_Semana"].str.capitalize()  
        
        df["Numero_Dia_Semana"] = df["DATE"].dt.weekday 
        
        logger.info(f"   📋 Hora calculada: {df['Hora'].min()} - {df['Hora'].max()}")
        logger.info(f"   📋 Rangos: {df['Rango_Horario'].unique().tolist()}")

    df["Fecha_dia"] = fecha

    # Inicializar columnas de métricas
    df["Contacto_Identificado_Humano"] = 0
    df["Gestion_Humano"] = 0
    df["Venta_Humano"] = 0
    df["campaign_id"] = None
    df["campaign_name"] = None
    df["Nombre_Campana"] = None
    df["Operado_Por__c"] = None
    df["Entidad_principal"] = None
    df["Gestion_Marcador"] = 0
    df["localizado_historico"] = "NO_LOCALIZADO"

    # ── 1. Enriquecer con campañas desde Excel ────────────────────────────
    if not df_campanas.empty and "customer_id" in df_campanas.columns:
        campanas_dict = {}
        for _, row in df_campanas.iterrows():
            cust_id = str(row['customer_id']).strip()
            if cust_id and cust_id not in ('nan', 'None', ''):
                camp_id = row.get('campaign_id')
                camp_name = row.get('campaign_name')
                nom_campana = row.get('Nombre_Campana')

                if pd.notna(camp_id) and str(camp_id).strip() not in ('nan', 'None', ''):
                    camp_id = str(camp_id).strip().rstrip('-').rstrip()
                else:
                    camp_id = None

                if pd.notna(camp_name) and str(camp_name).strip() not in ('nan', 'None', ''):
                    camp_name = str(camp_name).strip().rstrip('-').rstrip()
                else:
                    camp_name = None

                if pd.notna(nom_campana) and str(nom_campana).strip() not in ('nan', 'None', ''):
                    nom_campana = str(nom_campana).strip().rstrip('-').rstrip()
                else:
                    nom_campana = None

                campanas_dict[cust_id] = {
                    'campaign_id': camp_id,
                    'campaign_name': camp_name,
                    'Nombre_Campana': nom_campana
                }

        logger.info(f"   📋 Diccionario de campañas: {len(campanas_dict)} customer_ids mapeados")

        def _get_camp_id(contacto):
            c = str(contacto).strip()
            return campanas_dict.get(c, {}).get('campaign_id')

        def _get_camp_name(contacto):
            c = str(contacto).strip()
            return campanas_dict.get(c, {}).get('campaign_name')

        def _get_nom_campana(contacto):
            c = str(contacto).strip()
            return campanas_dict.get(c, {}).get('Nombre_Campana')

        df["campaign_id"] = df["Contacto__c"].apply(_get_camp_id)
        df["campaign_name"] = df["Contacto__c"].apply(_get_camp_name)
        df["Nombre_Campana"] = df["Contacto__c"].apply(_get_nom_campana)

        for col in ["campaign_id", "campaign_name", "Nombre_Campana"]:
            df[col] = (
                df[col].astype(str).str.strip()
                .str.rstrip('-').str.rstrip()
                .replace('None', None).replace('nan', None).replace('', None)
            )

        logger.info(f"   📋 campaign_id (Excel): {df['campaign_id'].notna().sum():,} con dato")
        logger.info(f"   📋 campaign_name (Excel): {df['campaign_name'].notna().sum():,} con dato")

    # ── 2. Normalizar campaign_id en CDR ──────────────────────────────────
    if "campaign_id" in df.columns:
        df["campaign_id"] = df["campaign_id"].astype(str).str.strip()
        df["campaign_id"] = df["campaign_id"].str.rstrip('-')
        df["campaign_id"] = df["campaign_id"].str.rstrip()
        df["campaign_id"] = df["campaign_id"].str.replace('-', '')
        df["campaign_id"] = df["campaign_id"].replace('None', None).replace('nan', None)
        logger.info(f"   📋 CDR campaign_id normalizados: {df['campaign_id'].notna().sum():,} con dato")
        
        # Mostrar valores únicos para depuración
        if df['campaign_id'].notna().sum() > 0:
            logger.info(f"   📋 CDR campaign_id únicos (muestra): {df['campaign_id'].dropna().unique().tolist()[:10]}")

    # ── 3. Enriquecer con campanas_desarollo ──────────────────────────────
    df_campanas_desarollo = auxiliares.get("campanas_desarollo", pd.DataFrame())
    if not df_campanas_desarollo.empty and "campaign_id" in df_campanas_desarollo.columns:
        # Normalizar campaign_id en campanas_desarollo
        df_campanas_desarollo["campaign_id"] = df_campanas_desarollo["campaign_id"].astype(str).str.strip()
        df_campanas_desarollo["campaign_id"] = df_campanas_desarollo["campaign_id"].str.rstrip('-')
        df_campanas_desarollo["campaign_id"] = df_campanas_desarollo["campaign_id"].str.rstrip()
        df_campanas_desarollo["campaign_id"] = df_campanas_desarollo["campaign_id"].str.replace('-', '')
        df_campanas_desarollo = df_campanas_desarollo.drop_duplicates(subset=["campaign_id"]).copy()
        
        logger.info(f"   📋 Campañas en campanas_desarollo: {len(df_campanas_desarollo)} IDs")
        logger.info(f"   📋 Ejemplo campanas_desarollo: {df_campanas_desarollo.head(5).to_dict('records')}")
        
        # Hacer merge
        df = df.merge(
            df_campanas_desarollo[["campaign_id", "campaign_name_desarollo"]],
            on="campaign_id",
            how="left",
            suffixes=("", "_desarollo")
        )
        
        if "campaign_name_desarollo" in df.columns:
            # Solo sobrescribir si NO está vacío
            mask = df["campaign_name_desarollo"].notna() & (df["campaign_name_desarollo"] != "")
            df["campaign_name"] = df["campaign_name"].where(
                ~mask,
                df["campaign_name_desarollo"]
            )
            df["Nombre_Campana"] = df["Nombre_Campana"].where(
                ~mask,
                df["campaign_name_desarollo"]
            )
            df.drop(columns=["campaign_name_desarollo"], inplace=True)
            
            logger.info(f"   📋 campaign_name enriquecido desde campanas_desarollo: {mask.sum():,} con dato")

    # ── 4. Enriquecer con Intradia (Operado_Por__c y Entidad_principal) ──
    df_intradia = auxiliares.get("intradia", pd.DataFrame())
    if not df_intradia.empty and "Contacto__c" in df_intradia.columns:
        df_intradia = df_intradia.drop_duplicates(subset=["Contacto__c"]).copy()
        df = df.merge(
            df_intradia[["Contacto__c", "Operado_Por__c", "Entidad_principal"]],
            on="Contacto__c",
            how="left",
            suffixes=("", "_bq")
        )

        if "Operado_Por__c_bq" in df.columns:
            df["Operado_Por__c"] = df["Operado_Por__c_bq"]
            df.drop(columns=["Operado_Por__c_bq"], inplace=True)

        if "Entidad_principal_bq" in df.columns:
            df["Entidad_principal"] = df["Entidad_principal_bq"]
            df.drop(columns=["Entidad_principal_bq"], inplace=True)

        logger.info(f"   📋 Operado_Por__c desde Intradia: {df['Operado_Por__c'].notna().sum():,} con dato")
        logger.info(f"   📋 Entidad_principal desde Intradia: {df['Entidad_principal'].notna().sum():,} con dato")

    # ── 5. Limpiar nombres de campaña (quitar guiones al inicio) ──
    for col in ["campaign_name", "Nombre_Campana"]:
        if col in df.columns:
            df[col] = df[col].apply(limpiar_nombre_campana)
            df[col] = df[col].replace('None', None).replace('nan', None).replace('', None)
            logger.info(f"   📋 {col} limpiado (guiones eliminados)")

    # ── 6. Grupo_Operador ──────────────────────────────────────────────────
    df["Grupo_Operador"] = df["Operado_Por__c"].apply(calcular_grupo_operador)
    logger.info(f"   📋 Grupo_Operador: {df['Grupo_Operador'].value_counts().to_dict()}")

    # ── 7. Merge con contacto humano ──────────────────────────────────────
    df_contacto = auxiliares.get("contacto", pd.DataFrame())
    if not df_contacto.empty:
        df = df.merge(
            df_contacto[["Contacto__c", "Fecha_dia", "Contacto_Identificado_Humano_aux"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        df["Contacto_Identificado_Humano"] = pd.to_numeric(
            df["Contacto_Identificado_Humano_aux"], errors="coerce"
        ).fillna(0).astype("int64")
        df.drop(columns=["Contacto_Identificado_Humano_aux"], inplace=True)

    # ── 8. Merge con gestion humana ──────────────────────────────────────
    df_gestion = auxiliares.get("gestion", pd.DataFrame())
    if not df_gestion.empty:
        df = df.merge(
            df_gestion[["Contacto__c", "Fecha_dia", "Gestion_Humano_aux"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        df["Gestion_Humano"] = pd.to_numeric(
            df["Gestion_Humano_aux"], errors="coerce"
        ).fillna(0).astype("int64")
        df.drop(columns=["Gestion_Humano_aux"], inplace=True)

    # ── 9. Merge con ventas ──────────────────────────────────────────────
    df_venta = auxiliares.get("venta", pd.DataFrame())
    if not df_venta.empty:
        df = df.merge(
            df_venta[["Contacto__c", "Fecha_dia", "Venta_Humano_aux"]],
            on=["Contacto__c", "Fecha_dia"],
            how="left",
        )
        df["Venta_Humano"] = pd.to_numeric(
            df["Venta_Humano_aux"], errors="coerce"
        ).fillna(0).astype("int64")
        df.drop(columns=["Venta_Humano_aux"], inplace=True)

    logger.info(
        f"   📋 Auxiliares aplicados | "
        f"Contacto: {int(df['Contacto_Identificado_Humano'].sum()):,} | "
        f"Gestion: {int(df['Gestion_Humano'].sum()):,} | "
        f"Venta: {int(df['Venta_Humano'].sum()):,}"
    )

    # ── 10. Calcular flags ──────────────────────────────────────────────────
    df["Contacto_Identificado_Robot"] = np.where(
        df["COD_ACT"].isin(TIPIFICACIONES_ROBOT), 1, 0
    ).astype("int64")

    df["Gestion_Humano"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1) & (df["Gestion_Humano"].fillna(0) == 1),
        1, 0
    ).astype("int64")

    df["Contacto_Identificado_Humano"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1) & (df["Contacto_Identificado_Humano"].fillna(0) == 1),
        1, 0
    ).astype("int64")

    df["Venta_Humano_Identificado"] = np.where(
        (df["Contacto_Identificado_Robot"] == 1)
        & (df["Contacto_Identificado_Humano"].fillna(0) == 1)
        & (df["Venta_Humano"].fillna(0) == 1),
        1, 0
    ).astype("int64")

    if "Venta_Humano" in df.columns:
        df.drop(columns=["Venta_Humano"], inplace=True)

    logger.info(
        f"   📊 Procesamiento: {len(df):,} registros | "
        f"Robot: {int(df['Contacto_Identificado_Robot'].sum()):,} | "
        f"Gestion: {int(df['Gestion_Humano'].sum()):,} | "
        f"Contacto: {int(df['Contacto_Identificado_Humano'].sum()):,} | "
        f"Venta: {int(df['Venta_Humano_Identificado'].sum()):,}"
    )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# ENRIQUECIMIENTO CON BIGQUERY (SQL)
# ─────────────────────────────────────────────────────────────────────────────

def enriquecer_columnas_bq(df_procesado: pd.DataFrame, fecha: str, client) -> pd.DataFrame:
    if df_procesado.empty:
        return df_procesado

    df = df_procesado.copy()
    df["Contacto__c"] = df["Contacto__c"].astype(str).str.strip()

    # 1. Ultimo_Contacto (GestionesTitular)
    try:
        sql = f"""
            SELECT Contacto__c, Fecha_Gestion__c AS Ultimo_Contacto
            FROM `{PROJECT_ID}.Operacion_Analitica.GestionesTitular`
            WHERE DATE(Fecha_Gestion__c) < DATE('{fecha}')
              AND Contacto__c IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY Contacto__c ORDER BY Fecha_Gestion__c DESC
            ) = 1
        """
        df_ultimo = _run_query(client, sql)
        if not df_ultimo.empty:
            df_ultimo["Contacto__c"] = df_ultimo["Contacto__c"].astype(str).str.strip()
            df = df.merge(df_ultimo, on="Contacto__c", how="left", suffixes=("", "_bq"))
            if "Ultimo_Contacto_bq" in df.columns:
                df["Ultimo_Contacto"] = df["Ultimo_Contacto_bq"].combine_first(df.get("Ultimo_Contacto"))
                df.drop(columns=["Ultimo_Contacto_bq"], inplace=True)
            logger.info(f"   📋 Ultimo_Contacto: {df['Ultimo_Contacto'].notna().sum():,}")
    except Exception as exc:
        logger.warning(f"   ⚠️ No se pudo enriquecer Ultimo_Contacto: {exc}")

    # 2. Gestion_Marcador (Campanas_Marcador)
    try:
        sql = f"""
            SELECT DISTINCT Info1 AS Contacto__c, 1 AS Gestion_Marcador_bq
            FROM `{PROJECT_ID}.SalesForce.Campanas_Marcador`
            WHERE SUBSTR(CAST(FechaMod AS STRING), 1, 10) = '{fecha}'
              AND Info1 IS NOT NULL
        """
        df_marcador = _run_query(client, sql)
        if not df_marcador.empty:
            df_marcador["Contacto__c"] = df_marcador["Contacto__c"].astype(str).str.strip()
            df_marcador = df_marcador.drop_duplicates(subset=["Contacto__c"])
            df = df.merge(df_marcador, on="Contacto__c", how="left")
            df["Gestion_Marcador"] = np.where(
                (df["Gestion_Marcador_bq"].fillna(0).astype("int64") == 1)
                & (df["Contacto_Identificado_Robot"] == 1),
                1, 0,
            ).astype("int64")
            df.drop(columns=["Gestion_Marcador_bq"], inplace=True)
        else:
            df["Gestion_Marcador"] = 0
        logger.info(f"   📋 Gestion_Marcador: {int(df['Gestion_Marcador'].sum()):,}")
    except Exception as exc:
        logger.warning(f"   ⚠️ No se pudo enriquecer Gestion_Marcador: {exc}")
        if "Gestion_Marcador" not in df.columns:
            df["Gestion_Marcador"] = 0

    # 3. localizado_historico (calculado)
    if "DATE" in df.columns:
        fecha_dt = pd.to_datetime(df["DATE"], errors="coerce")
    else:
        fecha_dt = pd.Series(pd.to_datetime(fecha), index=df.index)

    if "Ultimo_Contacto" in df.columns:
        ultimo_dt = pd.to_datetime(df["Ultimo_Contacto"], errors="coerce")
    else:
        ultimo_dt = pd.Series(pd.NaT, index=df.index)

    meses_diff = (
        (fecha_dt.dt.year - ultimo_dt.dt.year) * 12
        + (fecha_dt.dt.month - ultimo_dt.dt.month)
    )
    df["localizado_historico"] = np.where(
        ultimo_dt.notna() & (meses_diff <= 6), "LOCALIZADO",
        np.where(ultimo_dt.notna(), "PERDIDO", "NO_LOCALIZADO"),
    )
    logger.info(f"   📋 localizado_historico: LOCALIZADO={int((df['localizado_historico']=='LOCALIZADO').sum()):,} | "
                f"PERDIDO={int((df['localizado_historico']=='PERDIDO').sum()):,} | "
                f"NO_LOCALIZADO={int((df['localizado_historico']=='NO_LOCALIZADO').sum()):,}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSION DE TIPOS PARA BIGQUERY
# ─────────────────────────────────────────────────────────────────────────────

def _convertir_tipos_bq(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # STRING
    for col in ["Operado_Por__c", "CONN_ID", "Contacto__c", "COD_ACT",
                "Grupo_Operador", "campaign_id", "campaign_name", "Nombre_Campana",
                "Fecha_dia", "servidor", "Ultimo_Contacto", "Entidad_principal", "localizado_historico","Rango_Horario", "Dia_Semana", ]:
        if col in df.columns:
            df[col] = df[col].astype(str).replace({'nan': None, 'None': None, '': None})

    # INT64 metricas
    for col in ["Contacto_Identificado_Robot", "Gestion_Humano",
                "Contacto_Identificado_Humano", "Venta_Humano_Identificado",
                "Gestion_Marcador","Hora","Numero_Dia_Semana" ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")

    # TELEPHONE a INT64
    if "TELEPHONE" in df.columns:
        df["TELEPHONE"] = pd.to_numeric(df["TELEPHONE"], errors="coerce").fillna(0).astype("int64")

    # DATE a DATETIME
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")

    # Resto object: NaN -> None
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].where(df[col].notna(), None)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# SUBIR A EMBUDO_CONSOLIDADO (RESPALDO)
# ─────────────────────────────────────────────────────────────────────────────
def subir_a_embudo_consolidado(df_cdr: pd.DataFrame, fecha: str, client) -> bool:
    if df_cdr.empty:
        return False

    df_subir = df_cdr.copy()

    # 🔥 ELIMINAR COLUMNAS QUE NO EXISTEN EN LA TABLA
    columnas_a_eliminar = ['AGENT_ID', 'AGENT_NAME', 'COMMENT', 'COST', 
                           'DESCRIPTION_COD_ACT', 'DESCRIPTION_COD_ACT_2', 
                           'DESTINY', 'SKILL_NAME', 'SKILL_ID', 'TIME_MIN', 'TIME_SEG',
                           'TYPE_INTERACTION', 'HANG_UP', 'COD_ACT_2']
    
    for col in columnas_a_eliminar:
        if col in df_subir.columns:
            df_subir.drop(columns=[col], inplace=True)
            logger.info(f"   Eliminada columna: {col}")

    # Eliminar servidor (ya existe en la tabla Consolidado)
    if 'servidor' in df_subir.columns:
        df_subir.drop(columns=['servidor'], inplace=True)
        logger.info(f"   Eliminada columna: servidor (ya existe en la tabla)")

    df_subir["Fecha_dia"] = fecha

    if "tipo_reporte" not in df_subir.columns:
        df_subir["tipo_reporte"] = "CDR"
    # Eliminar AGENT_ID antes de subir
    if 'AGENT_ID' in df_subir.columns:
        df_subir.drop(columns=['AGENT_ID'], inplace=True)
        
    if "DATE" in df_subir.columns:
        df_subir["DATE"] = pd.to_datetime(df_subir["DATE"], errors="coerce")


    #COLUMNAS QUE SE VAN A SUBIR

    for col in ["Operado_Por__c","Hora","Dia_Semana", "Rango_Horario","Numero_Dia_Semana","CONN_ID", "Contacto__c", "COD_ACT",
                "tipo_reporte", "Fecha_dia", "campaign_id"]:
        if col in df_subir.columns:
            df_subir[col] = df_subir[col].astype(str).replace({'nan': None, 'None': None, '': None})

    if "TELEPHONE" in df_subir.columns:
        df_subir["TELEPHONE"] = pd.to_numeric(df_subir["TELEPHONE"], errors="coerce").fillna(0).astype("int64")

    for col in df_subir.columns:
        if df_subir[col].dtype == object:
            df_subir[col] = df_subir[col].where(df_subir[col].notna(), None)

    try:
        sql_delete = f"""
            DELETE FROM `{TABLE_CONSOLIDADO}`
            WHERE Fecha_dia = '{fecha}' AND tipo_reporte = 'CDR'
        """
        logger.info(f"   🗑️ Eliminando CDR de {fecha} en Embudo_Consolidado...")
        _run_dml(client, sql_delete, "DELETE Consolidado")

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        logger.info(f"   📤 Subiendo {len(df_subir):,} registros a Embudo_Consolidado...")

        def _load(df, table, config):
            job = client.load_table_from_dataframe(df, table, job_config=config)
            job.result()
            return job

        job = _retry_bq_operation(_load, "LOAD Consolidado", df_subir, TABLE_CONSOLIDADO, job_config)
        logger.info(f"   ✅ Upload Consolidado OK. Job ID: {job.job_id}")
        return True

    except Exception as exc:
        logger.error(f"❌ Error subiendo a Embudo_Consolidado: {exc}")
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SUBIR A POSITIVOS ROBOT TEST (TODOS LOS REGISTROS)
# ─────────────────────────────────────────────────────────────────────────────
def subir_a_embudo_positivo(
    df_procesado: pd.DataFrame,
    fecha: str,
    client,
) -> bool:
    if df_procesado.empty:
        return False

    # 🔥 TODOS LOS REGISTROS (NO SOLO ROBOTS)
    df_todos = df_procesado.copy()

    logger.info(f"   📤 Subiendo TODOS los {len(df_todos):,} registros")
    
    # VERIFICAR campaign_name ANTES DE SUBIR
    if 'campaign_name' in df_todos.columns:
        logger.info(f"   📋 campaign_name en df: {df_todos['campaign_name'].notna().sum():,} con dato")
        logger.info(f"   📋 Ejemplo campaign_name: {df_todos['campaign_name'].head(3).tolist()}")
    else:
        logger.warning("   ⚠️ campaign_name NO está en el DataFrame")
        # Crear campaign_name desde campaign_id
        df_todos['campaign_name'] = df_todos['campaign_id'].apply(
            lambda x: f"Campaña {x}" if pd.notna(x) else None
        )
        logger.info(f"   📋 campaign_name creado desde campaign_id")

    # TODAS las columnas del schema
    columnas_subir = list(SCHEMA_POSITIVOS.keys())

    cols_disponibles = [c for c in columnas_subir if c in df_todos.columns]
    df_subir = df_todos[cols_disponibles].copy()

    faltan = [c for c in columnas_subir if c not in df_todos.columns]
    if faltan:
        logger.warning(f"   ⚠️ Columnas del schema que NO están en el DataFrame: {faltan}")

    # CONVERTIR TIPOS
    df_subir = _convertir_tipos_bq(df_subir)

    try:
        _asegurar_columnas_positivos(client)

        sql_delete = f"""
            DELETE FROM `{TABLE_POSITIVOS}`
            WHERE Fecha_dia = '{fecha}'
        """
        logger.info(f"   🗑️ Eliminando registros de {fecha} en {TABLE_POSITIVOS}...")
        _run_dml(client, sql_delete, "DELETE Positivos TEST")

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        logger.info(f"   📤 Subiendo {len(df_subir):,} registros a {TABLE_POSITIVOS}...")

        def _load(df, table, config):
            job = client.load_table_from_dataframe(df, table, job_config=config)
            job.result()
            return job

        job = _retry_bq_operation(_load, "LOAD Positivos TEST", df_subir, TABLE_POSITIVOS, job_config)
        logger.info(f"   ✅ Upload Positivos OK. Job ID: {job.job_id}")
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
        logger.info(f"🚀 INICIO -- PROCESAMIENTO CDR (v10.10 - DEFINITIVO) | fecha={fecha}")
        logger.info(sep)

        credentials = obtener_credenciales()
        if credentials is None:
            logger.error("❌ No se pudieron obtener credenciales. Proceso abortado.")
            return False
        client = _bq_client(credentials)

        # 1. Leer CDR desde Excel
        logger.info("─ Paso 1/6: Leer CDR desde Excel...")
        df_cdr = leer_cdr_excel(fecha)
        if df_cdr.empty:
            logger.warning(f"⚠️ Sin CDR para {fecha}. Proceso finalizado.")
            return False

        # 2. Leer campañas desde Excel
        logger.info("─ Paso 2/6: Leer campañas desde Excel...")
        df_campanas = leer_campanas_excel(fecha)
        if not df_campanas.empty:
            logger.info(f"   ✅ {len(df_campanas)} campañas únicas cargadas")

        # 3. Consultar auxiliares BQ
        logger.info("─ Paso 3/6: Consultar auxiliares BigQuery...")
        auxiliares = consultar_auxiliares(fecha, mes_inicio, client)

        # 4. Procesar datos
        logger.info("─ Paso 4/6: Calcular métricas...")
        df_procesado = procesar_datos(df_cdr, fecha, df_campanas, auxiliares)
        if df_procesado.empty:
            logger.error("❌ Procesamiento no generó datos.")
            return False

        # 5. Subir CDR crudos a Consolidado (respaldo)
        logger.info("─ Paso 5/6: Subir a Embudo_Consolidado (respaldo)...")
        subir_a_embudo_consolidado(df_cdr, fecha, client)

        # 6. Enriquecer con BQ y subir a Positivos (TODOS los registros)
        logger.info("─ Paso 6/6: Enriquecer y subir a Positivos (TODOS)...")
        df_procesado = enriquecer_columnas_bq(df_procesado, fecha, client)
        if not subir_a_embudo_positivo(df_procesado, fecha, client):
            logger.error("❌ Falló la subida a la tabla de prueba.")
            return False

        logger.info(sep)
        logger.info(f"🏁 FIN -- ✅ EXITO  |  fecha={fecha}")
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