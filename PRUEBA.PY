from google.cloud import bigquery
from google.oauth2 import service_account

# Ruta al archivo de credenciales
credentials = service_account.Credentials.from_service_account_file(
    "config/google_key.json"
)

# Crear cliente
client = bigquery.Client(
    credentials=credentials,
    project=credentials.project_id
)

query = """
DELETE FROM `capable-arbor-209819.Tablas_Reporteria.Telefonos_Tutela`
WHERE Telefono = "3194319978"
"""

query_job = client.query(query)
query_job.result()

print("✅ Registro eliminado correctamente.")