
from google.cloud import bigquery
from google.oauth2 import service_account
import os
creds_path = os.path.join(os.path.dirname('.'), 'config', 'google_key.json')
credentials = service_account.Credentials.from_service_account_file(
    creds_path,
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
client = bigquery.Client(project='capable-arbor-209819', credentials=credentials)
query = '''
SELECT column_name
FROM capable-arbor-209819.Tablas_Reporteria.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'Telefonos_Tutela'
'''
try:
    res = client.query(query).result()
    for row in res:
        print(row.column_name)
except Exception as e:
    print(f'Error: {e}')

