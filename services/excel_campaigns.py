"""
Servicio para procesar Excel y preparar los registros para Wolkvox.

NO hace validaciones (lista negra, duplicados, etc.).
Solo:
  1. Lee el Excel.
  2. Extrae Contacto__c.
  3. Consulta BQ para enriquecer.
  4. Devuelve los registros en el formato que espera validar_consulta_wolkvox.

Las validaciones las hace el flujo existente con rows_override.
"""

import re
import logging
import pandas as pd

logger = logging.getLogger(__name__)

TABLA_INTRADIA = "capable-arbor-209819.Campanas.Campanas_intradia"
COLUMNA_EXCEL = "Contacto__c"


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _aplicar_prefijo(telefono, prefijo: str) -> str:
    """Aplica prefijo al teléfono si no lo tiene."""
    if pd.isna(telefono) or str(telefono).strip() == "":
        return ""
    t = re.sub(r'[^0-9]', '', str(telefono))
    if not t:
        return ""
    if t.startswith(prefijo):
        return t
    return f"{prefijo}{t}"


# ══════════════════════════════════════════════════════════════════════
# LECTURA DEL EXCEL
# ══════════════════════════════════════════════════════════════════════

def leer_excel(file) -> pd.DataFrame:
    """Lee el Excel y valida que tenga la columna Contacto__c."""
    logger.info("[EXCEL-1] Leyendo archivo...")

    try:
        df = pd.read_excel(file)
    except Exception as exc:
        logger.error(f"[EXCEL-1] Error leyendo Excel: {exc}")
        raise ValueError(f"No se pudo leer el Excel: {exc}")

    logger.info(f"[EXCEL-1] Leído: {len(df)} filas")
    logger.info(f"[EXCEL-2] Columnas: {list(df.columns)}")

    if COLUMNA_EXCEL not in df.columns:
        raise ValueError(
            f"El Excel debe tener una columna '{COLUMNA_EXCEL}'. "
            f"Columnas encontradas: {list(df.columns)}"
        )

    df[COLUMNA_EXCEL] = df[COLUMNA_EXCEL].astype(str).str.strip()
    filas_antes = len(df)
    df = df[df[COLUMNA_EXCEL].notna() & ~df[COLUMNA_EXCEL].isin(["", "nan", "None"])]

    logger.info(
        f"[EXCEL-2] Filas con Contacto__c válido: {len(df)} "
        f"(descartadas: {filas_antes - len(df)})"
    )

    return df


def extraer_contactos(df_excel: pd.DataFrame) -> list[str]:
    """Extrae Contacto__c únicos."""
    contactos = df_excel[COLUMNA_EXCEL].dropna().unique().tolist()
    contactos = [c for c in contactos if c and c not in ("nan", "None", "")]
    logger.info(f"[EXCEL-3] Contactos únicos: {len(contactos)}")
    return contactos


# ══════════════════════════════════════════════════════════════════════
# ENRIQUECIMIENTO DESDE BQ
# ══════════════════════════════════════════════════════════════════════

def enriquecer_desde_bigquery(contactos: list[str], bq_client) -> pd.DataFrame:
    """Consulta BQ con los IDs del Excel."""
    if not contactos:
        logger.warning("[EXCEL-4] Sin contactos para consultar")
        return pd.DataFrame()

    contactos_sql = ",".join([f"'{c}'" for c in contactos])

    query = f"""
        SELECT DISTINCT
            Name,
            Contacto__c,
            Telefono_1,
            email_1
        FROM `{TABLA_INTRADIA}`
        WHERE Contacto__c IN ({contactos_sql})
    """

    logger.info(f"[EXCEL-4] Consultando BQ para {len(contactos)} contactos...")

    try:
        df = bq_client.query(query).to_dataframe()
    except Exception as exc:
        logger.error(f"[EXCEL-4] Error BQ: {exc}")
        raise ValueError(f"Error consultando BigQuery: {exc}")

    logger.info(f"[EXCEL-5] BQ devolvió {len(df)} registros")
    return df


# ══════════════════════════════════════════════════════════════════════
# ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════

def excel_a_registros(file, prefijo: str, bq_client) -> list[dict]:
    """
    Lee el Excel, enriquece con BQ, y devuelve la lista de registros
    en el formato que espera `validar_consulta_wolkvox(rows_override=...)`.

    NO hace validaciones. Solo prepara los datos.
    """
    logger.info("═" * 60)
    logger.info(f"[EXCEL-0] Procesando Excel (prefijo={prefijo})")
    logger.info("═" * 60)

    # 1. Leer Excel
    df_excel = leer_excel(file)
    contactos = extraer_contactos(df_excel)

    if not contactos:
        raise ValueError("El Excel no tiene contactos válidos en 'Contacto__c'.")

    # 2. Enriquecer
    df_bq = enriquecer_desde_bigquery(contactos, bq_client)

    if df_bq.empty:
        raise ValueError(
            f"Ninguno de los {len(contactos)} contactos del Excel está en BigQuery."
        )

    # 3. Armar registros en el formato que espera el validador
    registros = []
    for row in df_bq.to_dict("records"):
        registros.append({
            # Campos para el validador
            "tel1": _aplicar_prefijo(row.get("Telefono_1"), prefijo),
            "customer_id": str(row.get("Contacto__c", "")).strip(),
            "customer_name": str(row.get("Name", "")).strip() or "Sin Nombre",
            # Campos para Cargue_Wolkvox
            "Name": str(row.get("Name", "")).strip() or "Sin Nombre",
            "Contaco__c": str(row.get("Contacto__c", "")).strip(),
            "MailPreferente": str(row.get("email_1", "")).strip(),
            "email": str(row.get("email_1", "")).strip(),
            # Referencia al prefijo aplicado
            "prefijo": prefijo,
        })

    logger.info(f"[EXCEL-6] {len(registros)} registros listos para validar")
    logger.info("═" * 60)

    return registros