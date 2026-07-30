from google.cloud import bigquery
from google.oauth2 import service_account
import os
from datetime import datetime
creds_path = os.path.join(os.path.dirname('.'), 'config', 'google_key.json')
credentials = service_account.Credentials.from_service_account_file(
    creds_path,
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
client = bigquery.Client(project='capable-arbor-209819', credentials=credentials)
today_str = datetime.now().strftime('%Y-%m-%d')
# total messages sent today
q_total_today = f"""
SELECT COUNT(*) AS cnt
FROM `capable-arbor-209819.Temporal.SmsLog`
WHERE DATE(fecha_envio) = DATE('{today_str}')
"""
# distinct numbers sent today
q_distinct_today = f"""
SELECT COUNT(DISTINCT telefono) AS cnt
FROM `capable-arbor-209819.Temporal.SmsLog`
WHERE DATE(fecha_envio) = DATE('{today_str}')
"""
# numbers sent more than once today (duplicates count: total - distinct)
q_duplicates = f"""
SELECT (COUNT(*) - COUNT(DISTINCT telefono)) AS cnt
FROM `capable-arbor-209819.Temporal.SmsLog`
WHERE DATE(fecha_envio) = DATE('{today_str}')
"""
queries = [
    ('TotalMessagesToday', q_total_today),
    ('DistinctNumbersToday', q_distinct_today),
    ('DuplicateMessagesToday', q_duplicates)
]
for name, q in queries:
    try:
        res = client.query(q).result()
        for row in res:
            print(f'{name}: {row.cnt}')
    except Exception as e:
        print(f'{name}: Error - {e}')
