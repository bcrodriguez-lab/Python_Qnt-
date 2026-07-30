from google.cloud import bigquery
from google.oauth2 import service_account
import os
from datetime import datetime, timedelta
creds_path = os.path.join(os.path.dirname('.'), 'config', 'google_key.json')
credentials = service_account.Credentials.from_service_account_file(
    creds_path,
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
client = bigquery.Client(project='capable-arbor-209819', credentials=credentials)
# Editable blacklist
q_blacklist = "SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Temporal.ListaNegraSms` WHERE activo = TRUE"
# Distinct numbers sent today (assuming fecha_hora is timestamp)
today_str = datetime.now().strftime('%Y-%m-%d')
q_sent_today = f"""
SELECT COUNT(DISTINCT telefono) AS cnt
FROM `capable-arbor-209819.Temporal.SmsLog`
WHERE DATE(fecha_envio) = DATE('{today_str}')
"""
# Total sent (all time)
q_total_sent = "SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Temporal.SmsLog`"
# Total scheduled
q_scheduled = "SELECT COUNT(*) AS cnt FROM `capable-arbor-209819.Temporal.ProgramacionSms`"
queries = [
    ('BlacklistEditable', q_blacklist),
    ('SentTodayDistinct', q_sent_today),
    ('TotalSentAllTime', q_total_sent),
    ('Scheduled', q_scheduled)
]
for name, q in queries:
    try:
        res = client.query(q).result()
        for row in res:
            print(f'{name}: {row.cnt}')
    except Exception as e:
        print(f'{name}: Error - {e}')
