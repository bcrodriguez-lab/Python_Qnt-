import re
import pandas as pd
from google.cloud import bigquery
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Campos que se escribirán en BigQuery (basados en el encabezado del CSV)
CAMPOS_CAMPANA = [
    'agent_id',
    'agent_name',
    'campaign_id',
    'cod_act',
    'cod_act_2',
    'comment',
    'conn_id',
    'cost',
    'customer_id',
    'date',
    'description_cod_act',
    'description_cod_act_2',
    'destiny',
    'hang_up',
    'skill_id',
    'skill_name',
    'telephone',
    'time_min',
    'time_seg',
    'type_interaction'
]

# Esquema de la tabla para BigQuery
SCHEMA_CAMPANA = [
    bigquery.SchemaField('agent_id', 'INTEGER'),
    bigquery.SchemaField('agent_name', 'STRING'),
    bigquery.SchemaField('campaign_id', 'INTEGER'),
    bigquery.SchemaField('cod_act', 'STRING'),
    bigquery.SchemaField('cod_act_2', 'STRING'),
    bigquery.SchemaField('comment', 'STRING'),
    bigquery.SchemaField('conn_id', 'INTEGER'),
    bigquery.SchemaField('cost', 'NUMERIC'),
    bigquery.SchemaField('customer_id', 'STRING'),
    bigquery.SchemaField('date', 'TIMESTAMP'),
    bigquery.SchemaField('description_cod_act', 'STRING'),
    bigquery.SchemaField('description_cod_act_2', 'STRING'),
    bigquery.SchemaField('destiny', 'STRING'),
    bigquery.SchemaField('hang_up', 'STRING'),
    bigquery.SchemaField('skill_id', 'INTEGER'),
    bigquery.SchemaField('skill_name', 'STRING'),
    bigquery.SchemaField('telephone', 'STRING'),
    bigquery.SchemaField('time_min', 'FLOAT'),
    bigquery.SchemaField('time_seg', 'INTEGER'),
    bigquery.SchemaField('type_interaction', 'STRING'),
]

# Tabla destino en BigQuery
TABLE_ID = "capable-arbor-209819.volkvox2.resultado_campana_llamada"
TABLE_ID_CAMPAIGNS = "capable-arbor-209819.Operacion_Analitica.parametrizacion_campanas"


def escribir_resultados_campana(client, registros):
    """
    Escribe los registros de una campaña de llamadas en BigQuery.
    
    Args:
        client: Cliente de BigQuery autenticado
        registros: Lista de diccionarios con los datos a escribir
    
    Returns:
        dict: Resultado de la operación con información de éxito/error
    """
    from backend import log_task

    try:
        if not registros:
            return {
                "success": False,
                "message": "No hay registros para escribir",
                "rows_written": 0
            }
        
        # Convertir lista de diccionarios a DataFrame
        df = pd.DataFrame(registros)
        
        logger.debug(f"Sample data: {registros[:1] if registros else 'no data'}")
        
        # Convertir tipos de datos
        integer_fields = ['agent_id', 'campaign_id', 'conn_id', 'skill_id', 'time_seg']
        for field in integer_fields:
            if field in df.columns:
                df[field] = pd.to_numeric(df[field], errors='coerce').fillna(0).astype('int64')
        
        float_fields = ['time_min', 'cost']
        for field in float_fields:
            if field in df.columns:
                df[field] = pd.to_numeric(df[field], errors='coerce')
        
        if 'date' in df.columns:
            # 1. Limpieza agresiva de strings: 
            # Quitamos puntos de a.m./p.m. y nos aseguramos de que sea texto
            df['date'] = (df['date'].astype(str)
                        .str.replace('.', '', regex=False)  # Quita los puntos de "a.m."
                        .str.strip())                       # Quita espacios al inicio/final

            # 2. Conversión flexible:
            # Quitamos el 'format=' para que Pandas use su motor de inferencia (dayfirst=True es clave)
            df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')

            # 3. Validación (Opcional para debug):
            # Si sigue dando NaT, podrías imprimir df['date'].iloc[0] para ver qué falló
        
        logger.debug(f"DataFrame dtypes: {df.dtypes}")
        logger.debug(f"Sample df row: {df.head(1).to_dict('records') if not df.empty else 'empty'}")
        # campos_disponibles = [col for col in CAMPOS_CAMPANA if col in df.columns]
        campos_disponibles = ['agent_id', 'campaign_id', 'customer_id', 'cod_act', 'date', 'description_cod_act', 'hang_up', 'telephone', 'time_min', 'time_seg']  # Prueba con estos campos
        if not campos_disponibles:
            return {
                "success": False,
                "message": "No hay campos compatibles en los datos para escribir en BigQuery",
                "rows_written": 0
            }
        df_filtered = df[campos_disponibles]
        
        log_task(f"Escribiendo {len(df_filtered)} registros en {TABLE_ID}")
        logger.debug(f"Campos a escribir: {campos_disponibles}")
        
        # 3. Construir la consulta de eliminación para el día actual
        delete_query = f"""
            DELETE FROM {TABLE_ID}
            WHERE DATE(date) = CURRENT_DATE()
        """

        # 4. Ejecutar el borrado
        query_job = client.query(delete_query)
        query_job.result()  # Espera a que termine el borrado
        logger.debug("Registros borrados exitosamente.")

        # Configurar el job de carga
        job_config = bigquery.LoadJobConfig(
            schema=[field for field in SCHEMA_CAMPANA if field.name in campos_disponibles],
            write_disposition="WRITE_APPEND",  # Añadir registros
            autodetect=False
        )
        
        # Escribir en BigQuery
        job = client.load_table_from_dataframe(
            df_filtered,
            TABLE_ID,
            job_config=job_config
        )
        
        job.result()  # Esperar a que complete
        
        result = {
            "success": True,
            "message": f"Se escribieron {len(df_filtered)} registros exitosamente",
            "rows_written": len(df_filtered),
            "timestamp": datetime.now().isoformat(),
            "table": TABLE_ID
        }
        
        log_task(result["message"])
        return result
        
    except Exception as e:
        error_msg = f"Error escribiendo en BigQuery: {str(e)}"
        log_task(error_msg, level="ERROR")
        return {
            "success": False,
            "message": error_msg,
            "rows_written": 0,
            "timestamp": datetime.now().isoformat()
        }


def fetch_select_query_rows(client, query: str, *, max_rows: int = 50000) -> dict:
    """
    Ejecuta un SELECT en BigQuery y devuelve filas como list[dict] (para cargue Wolkvox).
    """
    try:
        if not query or not isinstance(query, str):
            return {"success": False, "message": "La consulta es obligatoria.", "rows": []}

        query_text = query.strip().rstrip(";")
        if not re.match(r"^SELECT\b", query_text, re.IGNORECASE):
            return {
                "success": False,
                "message": "Solo se permiten consultas SELECT para obtener clientes.",
                "rows": [],
            }

        logger.debug(f"Ejecutando SELECT BigQuery para cargue ({len(query_text)} chars)")
        job = client.query(query_text)
        result = job.result(max_results=max_rows)
        rows: list[dict] = []
        for row in result:
            rows.append(dict(row))
            if len(rows) >= max_rows:
                break

        return {
            "success": True,
            "rows": rows,
            "total": len(rows),
            "message": f"Se obtuvieron {len(rows)} fila(s) desde BigQuery.",
        }
    except Exception as e:
        logger.error(f"Error fetch_select_query_rows: {e}")
        return {"success": False, "message": str(e), "rows": []}


def count_query_results(client, query: str) -> dict:
    """Ejecuta un query de conteo en BigQuery y devuelve el total de filas."""
    try:
        if not query or not isinstance(query, str):
            return {"success": False, "message": "La consulta es obligatoria."}

        query_text = query.strip().rstrip(";")
        if not re.match(r"^SELECT\b", query_text, re.IGNORECASE):
            return {"success": False, "message": "Solo se permiten consultas SELECT para pruebas de conteo."}

        if not re.search(r"SELECT\s+COUNT\s*\(", query_text, re.IGNORECASE):
            query_text = f"SELECT COUNT(*) AS total FROM ({query_text}) AS subquery"

        logger.debug(f"Ejecutando conteo BigQuery: {query_text}")
        job = client.query(query_text)
        result = job.result()
        rows = list(result)
        if not rows:
            return {"success": True, "total": 0, "message": "Consulta ejecutada correctamente, no se devolvieron filas."}

        total = getattr(rows[0], "total", None)
        if total is None:
            total = rows[0][0] if len(rows[0]) > 0 else 0

        return {
            "success": True,
            "total": int(total),
            "message": f"Consulta ejecutada correctamente. Total: {int(total)}"
        }
    except Exception as e:
        logger.error(f"Error count_query_results: {e}")
        return {"success": False, "message": str(e)}


def _format_fecha_inicio_bq(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _campaign_row_for_bigquery(campaign: dict) -> dict:
    return {
        "id": int(campaign["id"]),
        "nombre": campaign.get("nombre"),
        "descripcion": campaign.get("descripcion") or "",
        "operacion": campaign.get("operacion"),
        "tipo": campaign.get("tipo"),
        "fecha_inicio": _format_fecha_inicio_bq(campaign.get("fecha_inicio")),
        "consulta": campaign.get("consulta"),
        "usuario": campaign.get("usuario"),
        "servidor": campaign.get("servidor") or "",
    }


def _truncate_campaigns_table(client) -> None:
    """Vacía la tabla de campañas en BigQuery."""
    try:
        client.query(f"TRUNCATE TABLE `{TABLE_ID_CAMPAIGNS}`").result()
        logger.debug("[SYNC] Tabla de campañas truncada en BigQuery")
    except Exception as truncate_error:
        logger.debug(f"[SYNC] TRUNCATE falló, usando DELETE: {truncate_error}")
        client.query(f"DELETE FROM `{TABLE_ID_CAMPAIGNS}` WHERE TRUE").result()
        logger.debug("[SYNC] Tabla de campañas vaciada con DELETE en BigQuery")


def sync_campaigns_to_bigquery(client, campaigns: list) -> dict:
    """
    Sincroniza SQLite -> BigQuery: borra todo en BigQuery y reescribe
    con el contenido actual de SQLite.
    """
    try:
        _truncate_campaigns_table(client)

        if not campaigns:
            return {
                "success": True,
                "message": "Sincronización completada. BigQuery quedó sin campañas (SQLite vacío).",
                "rows_written": 0,
            }

        rows = [_campaign_row_for_bigquery(c) for c in campaigns]
        errors = client.insert_rows_json(TABLE_ID_CAMPAIGNS, rows)
        if errors:
            logger.error(f"[SYNC] Errores insertando campañas en BigQuery: {errors}")
            return {
                "success": False,
                "message": f"Error insertando en BigQuery: {errors}",
            }

        return {
            "success": True,
            "message": f"Sincronización exitosa. {len(rows)} campaña(s) enviada(s) a BigQuery.",
            "rows_written": len(rows),
        }
    except Exception as e:
        logger.error(f"Error sync_campaigns_to_bigquery: {e}")
        return {"success": False, "message": str(e)}
