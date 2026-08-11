from google.cloud import bigquery

client = bq_client
table_id = "capable-arbor-209819.Temporal.ProgramacionSms"

# Verificar si la tabla es accesible
try:
    table = client.get_table(table_id)
    print(f"✅ Tabla encontrada: {table.table_id}")
    print(f"   Proyecto: {table.project}")
    print(f"   Dataset: {table.dataset_id}")
except Exception as e:
    print(f"❌ Error: {e}")