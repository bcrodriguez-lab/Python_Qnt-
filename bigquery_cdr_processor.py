"""
Procesador de CDR de Wolkvox a BigQuery.
Obtiene datos del CDR desde la API de Wolkvox, los transforma,
y los sube a Temporal.Robots_Temporal.
"""

import time
from datetime import datetime, date
from typing import Optional

import requests

from conexion_bigquery import get_bigquery_client
from database import AutoCampaign, AutoCampaignExecutionLog

# ===========================================================================
# CONFIGURACIÓN
# ===========================================================================

BQ_PROJECT_ID = "capable-arbor-209819"
BQ_DATASET_TEMP = "Temporal"
BQ_TABLE_TEMP = "Robots_Temporal"
TABLE_FULL_PATH = f"{BQ_PROJECT_ID}.{BQ_DATASET_TEMP}.{BQ_TABLE_TEMP}"

# Tipificaciones que identifican contacto positivo por robot
TIPIFICACIONES_ROBOT = [
    "CV_CONT_RECP", "CV_CONT_FILTRO", "102", "103",
    "CV_RESPONDE_POSITIVO", "CV_CUELGA", 102, 103
]

# Mapeo de grupos de operador
GRUPOS_OPERADOR = {
    "DIGITAL": ['DIGITAL_2', 'DIGITAL_MONTOS_BAJOS', 'DIGITAL_1', 'DIGITAL_ESPECIAL'],
    "RBK": ['QNT_RBK_2', 'QNT_RBK_1.2', 'QNT_RBK_1.1'],
    "MONTOS_ALTOS": ['QNT_RECAUDO', 'QNT_PERSONA_JURIDICA', 'QNT_JUD'],
    "SATELITES": ['GENNIALS_BPO_2', 'GENNIALS_BPO', 'HELLO_BPO', 'MAPNOVA', 'ESTA_BIEN_GROUP', 'QNT_COBRO'],
}


# ===========================================================================
# LOGGING
# ===========================================================================

def _log(message: str, level: str = "INFO") -> None:
    try:
        from backend import log_task
        log_task(f"[CDR-BIGQUERY] {message}", level=level)
    except Exception:
        pass


# ===========================================================================
# OBTENCIÓN DE CDR DESDE WOLKVOX
# ===========================================================================

def fetch_cdr_from_wolkvox(
    campaign: AutoCampaign,
    log: AutoCampaignExecutionLog,
) -> Optional[list[dict]]:
    """
    Obtiene los datos del CDR desde la API de Wolkvox.
    Prueba múltiples endpoints hasta encontrar uno que funcione.
    """
    from backend import get_authorization_headers, get_server

    server_name = (campaign.server_name or "").strip()
    end_dt = log.end_time or datetime.utcnow()

    # Construir fechas en formato string
    date_ini = end_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
    date_end = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d")

    def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
        if not s:
            return ""
        try:
            if "T" in s or len(s) > 10:
                dt = datetime.fromisoformat(s)
            else:
                d = date.fromisoformat(s)
                dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
            return dt.strftime("%Y%m%d%H%M%S")
        except Exception:
            return s

    date_ini_ts = _to_wolkvox_ts(date_ini, is_end=False)
    date_end_ts = _to_wolkvox_ts(date_end, is_end=True)

    # Obtener URL base del servidor
    try:
        srv = get_server(server_name)
    except Exception:
        srv = None

    if srv:
        prefix = (srv.get("url") or "").strip().rstrip("/")
        base_url = prefix if prefix.lower().startswith("http") else f"https://wv{prefix}.wolkvox.com"
    else:
        if server_name.lower().startswith("http"):
            base_url = server_name.rstrip("/")
        else:
            base_url = f"https://wv{server_name}.wolkvox.com"
        base_url = base_url.rstrip("/")

    # Endpoints a probar
    wolkvox_camp_id = str(campaign.wolkvox_campaign_id or "").strip()
    endpoints = []

    if wolkvox_camp_id:
        endpoints.append(
            f"{base_url}/api/v2/reports_manager.php?api=cdr_1&campaign_id={wolkvox_camp_id}&date_ini={date_ini_ts}&date_end={date_end_ts}"
        )
    endpoints.extend([
        f"{base_url}/api/v2/reports_manager.php?api=cdr_1&date_ini={date_ini_ts}&date_end={date_end_ts}",
        f"{base_url}/api/v2/reports_manager.php?api=campaign_1&date_ini={date_ini_ts}&date_end={date_end_ts}",
        f"{base_url}/api/reports_manager.php?api=cdr_1&date_ini={date_ini}&date_end={date_end}",
    ])

    headers = get_authorization_headers(server_name) or {}

    for url in endpoints:
        try:
            _log(f"Intentando CDR: {url}")
            resp = requests.get(url, headers=headers, timeout=60)

            if resp.status_code == 200:
                data_json = resp.json()
                rows = _extract_rows_from_response(data_json)

                if rows:
                    _log(f"CDR obtenido: {len(rows)} registros")
                    return rows
                else:
                    _log("Respuesta 200 pero sin datos extraíbles", level="WARN")
            else:
                _log(f"HTTP {resp.status_code}", level="WARN")

        except Exception as exc:
            _log(f"Error: {exc}", level="WARN")

    _log("No se pudo obtener CDR de ningún endpoint", level="ERROR")
    return None


def _extract_rows_from_response(data_json) -> Optional[list[dict]]:
    """Extrae filas de datos de la respuesta JSON del CDR."""
    if not data_json:
        return None

    if isinstance(data_json, list) and len(data_json) > 0:
        return data_json

    if isinstance(data_json, dict):
        for key in ["data", "files", "rows", "records", "results", "cdr"]:
            value = data_json.get(key)
            if isinstance(value, list) and len(value) > 0:
                return value

        for key, value in data_json.items():
            if isinstance(value, list) and len(value) > 0:
                _log(f"Usando clave '{key}' con {len(value)} elementos")
                return value

        if any(isinstance(v, (str, int, float)) for v in data_json.values()):
            return [data_json]

    return None


# ===========================================================================
# NORMALIZACIÓN DE COLUMNAS DEL CDR
# ===========================================================================

def normalize_cdr_columns(rows: list[dict], server_name: str) -> list[dict]:
    """
    Normaliza las columnas del CDR a un formato estándar.
    Cada servidor de Wolkvox puede devolver nombres de columnas diferentes.
    """
    if not rows:
        return []

    column_mapping = {
        "date": "DATE",
        "fecha": "DATE",
        "call_date": "DATE",
        "telephone": "TELEPHONE",
        "phone": "TELEPHONE",
        "customer_phone": "TELEPHONE",
        "telefono": "TELEPHONE",
        "cod_act": "COD_ACT",
        "code": "COD_ACT",
        "result": "COD_ACT",
        "resultado": "COD_ACT",
        "status": "COD_ACT",
        "conn_id": "CONN_ID",
        "call_id": "CONN_ID",
        "id_llamada": "CONN_ID",
        "uniqueid": "CONN_ID",
        "customer_id": "CUSTOMER_ID",
        "contacto__c": "CUSTOMER_ID",
        "client_id": "CUSTOMER_ID",
        "agent_name": "AGENT_NAME",
        "agent": "AGENT_NAME",
        "agente": "AGENT_NAME",
    }

    normalized = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        new_row = {"SERVIDOR": server_name}

        for key, value in row.items():
            key_lower = key.lower().strip()
            mapped = False

            for pattern, target in column_mapping.items():
                if pattern in key_lower:
                    new_row[target] = value
                    mapped = True
                    break

            if not mapped:
                new_row[key.upper()] = value

        # Asegurar columnas mínimas requeridas
        for col in ["DATE", "TELEPHONE", "COD_ACT", "CONN_ID", "CUSTOMER_ID"]:
            if col not in new_row:
                new_row[col] = None

        # Filtrar fila de TOTALES
        agent_name = str(new_row.get("AGENT_NAME", "")).upper().strip()
        if agent_name == "TOTAL":
            continue

        normalized.append(new_row)

    _log(f"Filas normalizadas: {len(normalized)}")
    if normalized:
        _log(f"Columnas: {list(normalized[0].keys())}")
    return normalized


# ===========================================================================
# CLASIFICACIÓN DE OPERADOR
# ===========================================================================

def classify_operador_grupo(operado_por: str) -> str:
    """Clasifica un operador en su grupo según reglas de negocio."""
    if not operado_por:
        return "OTRO"

    operado_por = str(operado_por).strip().upper()

    if "MANT" in operado_por:
        return "MANTENIMIENTO"

    for grupo, operadores in GRUPOS_OPERADOR.items():
        if operado_por in [op.upper() for op in operadores]:
            return grupo

    return "OTRO"


# ===========================================================================
# OBTENCIÓN DE DATOS DE REFERENCIA DESDE BIGQUERY
# ===========================================================================

def _fetch_reference_data(client, fecha_dia: str) -> dict:
    """
    Obtiene datos de referencia desde BigQuery necesarios para
    las validaciones y cruces del script original.
    """
    ref = {
        "operadores": {},
        "contactos_identificados": set(),
        "gestiones_humanas": {},
        "ventas": {},
    }

    # 1. Operadores desde Campanas.Campanas_intradia
    try:
        query = """
            SELECT DISTINCT Contacto__c, Operado_Por__c, Name 
            FROM Campanas.Campanas_intradia
        """
        for row in client.query(query).result():
            contacto = str(row.get("Contacto__c", "")).strip()
            if contacto:
                ref["operadores"][contacto] = {
                    "Operado_Por__c": str(row.get("Operado_Por__c", "")),
                    "Name": str(row.get("Name", "")),
                }
        _log(f"Operadores cargados: {len(ref['operadores'])}")
    except Exception as exc:
        _log(f"Error cargando operadores: {exc}", level="WARN")

    # 2. Contactos identificados (GestionesTitular)
    try:
        query = f"""
            SELECT DISTINCT Contacto__c
            FROM Operacion_Analitica.GestionesTitular
            WHERE Fecha_dia >= "{fecha_dia}"
        """
        for row in client.query(query).result():
            contacto = str(row.get("Contacto__c", "")).strip()
            if contacto:
                ref["contactos_identificados"].add(contacto)
        _log(f"Contactos identificados: {len(ref['contactos_identificados'])}")
    except Exception as exc:
        _log(f"Error cargando contactos: {exc}", level="WARN")

    # 3. Gestiones humanas (SalesForce)
    try:
        query = f"""
            SELECT DISTINCT Contacto__c, 
                   SUBSTR(CAST(Fecha_Gestion__c AS STRING), 1, 10) AS Fecha_dia
            FROM SalesForce.Gestion_Oportunidad__c
            WHERE Fecha_Gestion__c >= "{fecha_dia}"
        """
        for row in client.query(query).result():
            contacto = str(row.get("Contacto__c", "")).strip()
            fecha = str(row.get("Fecha_dia", "")).strip()
            if contacto:
                ref["gestiones_humanas"][contacto] = fecha
        _log(f"Gestiones humanas: {len(ref['gestiones_humanas'])}")
    except Exception as exc:
        _log(f"Error cargando gestiones: {exc}", level="WARN")

    # 4. Ventas (Tablas_Reporteria.reporteMes)
    try:
        query = f"""
            SELECT Contacto__c, 
                   Fecha_Acuerdo_de_Pago__c AS Fecha_dia
            FROM Tablas_Reporteria.reporteMes
            WHERE Fecha_Acuerdo_de_Pago__c >= "{fecha_dia}"
              AND entidades_habilitadas = "Habilitado"
              AND Estado_Base__c = "Habilitado"
        """
        for row in client.query(query).result():
            contacto = str(row.get("Contacto__c", "")).strip()
            fecha = str(row.get("Fecha_dia", "")).strip()
            if contacto:
                ref["ventas"][contacto] = fecha
        _log(f"Ventas cargadas: {len(ref['ventas'])}")
    except Exception as exc:
        _log(f"Error cargando ventas: {exc}", level="WARN")

    return ref


# ===========================================================================
# TRANSFORMACIÓN PRINCIPAL
# ===========================================================================

def transform_cdr_to_temp_table(
    cdr_rows: list[dict],
    campaign: AutoCampaign,
    log: AutoCampaignExecutionLog,
) -> list[dict]:
    """
    Transforma los datos del CDR al formato de Temporal.Robots_Temporal.
    Aplica todas las reglas de negocio del script de Colab original.
    """
    if not cdr_rows:
        _log("No hay filas CDR para transformar", level="WARN")
        return []

    server_name = (campaign.server_name or "").strip()
    end_dt = log.end_time or datetime.utcnow()
    fecha_dia = end_dt.strftime("%Y-%m-%d")

    # Paso 1: Normalizar columnas del CDR
    normalized_rows = normalize_cdr_columns(cdr_rows, server_name)
    if not normalized_rows:
        _log("No hay filas después de normalizar", level="WARN")
        return []

    # Paso 2: Obtener datos de referencia de BigQuery
    client = get_bigquery_client()
    if client is None:
        _log("Cliente BigQuery no disponible", level="ERROR")
        return []

    ref = _fetch_reference_data(client, fecha_dia)

    # Paso 3: Transformar cada fila
    transformed = []
    processed_customers = set()

    for row in normalized_rows:
        customer_id = str(row.get("CUSTOMER_ID", "")).strip()
        if not customer_id:
            continue

        cod_act_raw = row.get("COD_ACT", "")
        # Normalizar COD_ACT para comparación
        cod_act_str = str(cod_act_raw).strip()
        try:
            cod_act_int = int(cod_act_raw) if cod_act_raw not in (None, "") else None
        except (ValueError, TypeError):
            cod_act_int = None

        fecha_row = str(row.get("DATE", ""))[:10]

        # Datos del operador desde Campanas_intradia
        operador_info = ref["operadores"].get(customer_id, {})
        operado_por = operador_info.get("Operado_Por__c", "")
        grupo_operador = classify_operador_grupo(operado_por)

        # Determinar si es contacto identificado por robot
        es_contacto_robot = 1 if (
            cod_act_str in TIPIFICACIONES_ROBOT or
            cod_act_int in TIPIFICACIONES_ROBOT
        ) else 0

        # Determinar gestión humana (solo si fue identificado por robot)
        tiene_gestion_humana = customer_id in ref["gestiones_humanas"]
        gestion_humano = 1 if (es_contacto_robot == 1 and tiene_gestion_humana) else 0

        # Determinar contacto identificado humano
        tiene_contacto_humano = customer_id in ref["contactos_identificados"]
        contacto_identificado_humano = 1 if (es_contacto_robot == 1 and tiene_contacto_humano) else 0

        # Determinar venta humano identificado
        tiene_venta = customer_id in ref["ventas"]
        venta_humano_identificado = 1 if (
            es_contacto_robot == 1 and
            tiene_contacto_humano and
            tiene_venta
        ) else 0

        # Último contacto y localizado histórico
        ultimo_contacto = ""
        localizado_historico = "NO_LOCALIZADO"

        # Buscar último contacto en gestiones
        if tiene_gestion_humana:
            ultimo_contacto = ref["gestiones_humanas"].get(customer_id, "")
            if ultimo_contacto:
                try:
                    fecha_gestion = date.fromisoformat(ultimo_contacto[:10])
                    fecha_actual = date.fromisoformat(fecha_row) if fecha_row else end_dt.date()
                    meses_diff = (
                        (fecha_actual.year - fecha_gestion.year) * 12 +
                        (fecha_actual.month - fecha_gestion.month)
                    )
                    localizado_historico = "LOCALIZADO" if meses_diff <= 6 else "PERDIDO"
                except Exception:
                    localizado_historico = "PERDIDO"

        # Entidad principal
        entidad_principal = operador_info.get("Name", "")

        # Gestion Marcador (se inicializa en 0, se actualiza con query posterior)
        gestion_marcador = 0

        # Construir fila para Temporal.Robots_Temporal
        transformed_row = {
            "Fecha_dia": fecha_dia,
            "DATE": row.get("DATE", ""),
            "TELEPHONE": str(row.get("TELEPHONE", "")),
            "COD_ACT": cod_act_raw,
            "Grupo_Operador": grupo_operador,
            "Operado_Por__c": operado_por,
            "CONN_ID": str(row.get("CONN_ID", "")),
            "Contacto__c": customer_id,
            "Contacto_Identificado_Robot": es_contacto_robot,
            "Gestion_Humano": gestion_humano,
            "Contacto_Identificado_Humano": contacto_identificado_humano,
            "Venta_Humano_Identificado": venta_humano_identificado,
            "Ultimo_Contacto": ultimo_contacto,
            "Entidad_principal": entidad_principal,
            "localizado_historico": localizado_historico,
            "Gestion_Marcador": gestion_marcador,
            # Metadatos adicionales
            "servidor": server_name,
            "campaign_id": campaign.id,
            "execution_id": log.id,
        }

        transformed.append(transformed_row)

    _log(f"Filas transformadas: {len(transformed)}")
    if transformed:
        _log(f"Muestra primera fila: {dict(list(transformed[0].items())[:10])}")

    return transformed


# ===========================================================================
# CARGA A BIGQUERY
# ===========================================================================

def upload_to_temp_table(rows: list[dict]) -> dict:
    """
    Sube los datos transformados a Temporal.Robots_Temporal.
    Borra los datos del día actual antes de insertar (como hace el script original).
    """
    if not rows:
        return {
            "success": False,
            "message": "No hay datos para subir",
            "rows_uploaded": 0,
        }

    client = get_bigquery_client()
    if client is None:
        return {
            "success": False,
            "message": "Cliente BigQuery no disponible",
            "rows_uploaded": 0,
        }

    try:
        fecha_dia = rows[0].get("Fecha_dia", "")
        
        # 1. Borrar datos del día actual (igual que el script original)
        if fecha_dia:
            delete_query = f"""
                DELETE FROM {TABLE_FULL_PATH}
                WHERE Fecha_dia = "{fecha_dia}"
            """
            _log(f"Borrando datos del día {fecha_dia}...")
            job = client.query(delete_query)
            job.result()
            _log("Datos anteriores borrados")

        # 2. Insertar nuevos datos
        _log(f"Insertando {len(rows)} registros en {TABLE_FULL_PATH}...")

        # Usar pandas_gbq para la carga (igual que el script original)
        import pandas as pd
        import pandas_gbq

        df = pd.DataFrame(rows)

        # Seleccionar solo las columnas que van a la tabla
        columnas_tabla = [
            "Fecha_dia", "DATE", "TELEPHONE", "COD_ACT", "Grupo_Operador",
            "Operado_Por__c", "CONN_ID", "Contacto__c", "Contacto_Identificado_Robot",
            "Gestion_Humano", "Contacto_Identificado_Humano", "Venta_Humano_Identificado",
            "Ultimo_Contacto", "Entidad_principal", "localizado_historico", "Gestion_Marcador",
        ]

        # Asegurar que todas las columnas existan
        for col in columnas_tabla:
            if col not in df.columns:
                df[col] = ""

        df_to_upload = df[columnas_tabla]

        pandas_gbq.to_gbq(
            df_to_upload,
            TABLE_FULL_PATH,
            project_id=BQ_PROJECT_ID,
            if_exists="append",
            credentials=client._credentials,
        )

        _log(f"✅ {len(df_to_upload)} registros subidos a {TABLE_FULL_PATH}")

        return {
            "success": True,
            "message": f"{len(df_to_upload)} registros subidos",
            "rows_uploaded": len(df_to_upload),
        }

    except Exception as exc:
        _log(f"❌ Error subiendo a BigQuery: {exc}", level="ERROR")
        import traceback
        _log(traceback.format_exc(), level="ERROR")
        return {
            "success": False,
            "message": str(exc),
            "rows_uploaded": 0,
        }


# ===========================================================================
# FUNCIÓN PRINCIPAL - ORQUESTADOR
# ===========================================================================

def process_cdr_to_bigquery(
    campaign: AutoCampaign,
    log: AutoCampaignExecutionLog,
) -> dict:
    """
    Orquestador completo:
    1. Obtiene CDR desde API de Wolkvox
    2. Normaliza columnas
    3. Transforma datos con reglas de negocio
    4. Sube a Temporal.Robots_Temporal
    
    Returns:
        dict con success, message, rows_uploaded
    """
    _log("=" * 60)
    _log("INICIANDO PROCESAMIENTO CDR → BIGQUERY")
    _log(f"  Campaña: {campaign.name}")
    _log(f"  Servidor: {campaign.server_name}")
    _log(f"  Tabla destino: {TABLE_FULL_PATH}")
    _log("=" * 60)

    # Paso 1: Obtener CDR
    _log("Paso 1/4: Obteniendo CDR desde Wolkvox...")
    cdr_rows = fetch_cdr_from_wolkvox(campaign, log)

    if not cdr_rows:
        _log("❌ No se pudo obtener CDR", level="ERROR")
        return {
            "success": False,
            "message": "No se pudo obtener CDR de Wolkvox",
            "rows_uploaded": 0,
        }

    _log(f"  CDR obtenido: {len(cdr_rows)} registros")

    # Paso 2: Normalizar columnas
    _log("Paso 2/4: Normalizando columnas...")
    server_name = (campaign.server_name or "").strip()
    normalized_rows = normalize_cdr_columns(cdr_rows, server_name)
    _log(f"  Filas normalizadas: {len(normalized_rows)}")

    # Paso 3: Transformar datos
    _log("Paso 3/4: Transformando datos con reglas de negocio...")
    transformed_rows = transform_cdr_to_temp_table(normalized_rows, campaign, log)
    _log(f"  Filas transformadas: {len(transformed_rows)}")

    if not transformed_rows:
        _log("❌ No hay datos después de la transformación", level="WARN")
        return {
            "success": False,
            "message": "No hay datos después de la transformación",
            "rows_uploaded": 0,
        }

    # Paso 4: Subir a BigQuery
    _log("Paso 4/4: Subiendo a BigQuery...")
    result = upload_to_temp_table(transformed_rows)

    _log("=" * 60)
    if result.get("success"):
        _log(f"✅ PROCESAMIENTO COMPLETADO: {result.get('rows_uploaded')} registros en {TABLE_FULL_PATH}")
    else:
        _log(f"❌ ERROR: {result.get('message')}", level="ERROR")
    _log("=" * 60)

    return result