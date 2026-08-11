from google.cloud import bigquery

client = bigquery.Client()

query = """
    SELECT 
        id,
        fecha_programada,
        fecha_creacion,
        fecha_actualizacion,
        fecha_ejecucion
    FROM `capable-arbor-209819.Temporal.ProgramacionSMS`
    ORDER BY fecha_programada DESC
"""

df = client.query(query).to_dataframe()

# Ver cuáles son NULL
print("¿Hay NaT en fecha_creacion?:", df['fecha_creacion'].isna().any())
print("¿Hay NaT en fecha_actualizacion?:", df['fecha_actualizacion'].isna().any())
print("¿Hay NaT en fecha_ejecucion?:", df['fecha_ejecucion'].isna().any())

# Mostrar filas con NaT
print("\nFilas con NaT:")
print(df[df.isna().any(axis=1)])