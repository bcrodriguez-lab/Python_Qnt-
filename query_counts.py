from google.cloud import bigquery
from google.oauth2 import service_account
import os
creds_path = os.path.join(os.path.dirname('.'), 'config', 'google_key.json')
credentials = service_account.Credentials.from_service_account_file(
    creds_path,
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
client = bigquery.Client(project='capable-arbor-209819', credentials=credentials)
queries = [
    ('SmsLog', 'SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Temporal.SmsLog`'),
    ('ProgramacionSms', 'SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Temporal.ProgramacionSms`'),
    ('Telefonos_Tutela', 'SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Tablas_Reporteria.Telefonos_Tutela`')
]
for name, q in queries:
    try:
        res = client.query(q).result()
        for row in res:
            print(f'{name}: {row.cnt}')
    except Exception as e:
        print(f'{name}: Error - {e}')
