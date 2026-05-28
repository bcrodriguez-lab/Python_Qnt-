import sqlite3

DB = 'app.db'
con = sqlite3.connect(DB)
cur = con.cursor()
print('--- tables ---')
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(row)

candidates = ['big_query_campaign','big_query_campaigns','BigQueryCampaign','bigquerycampaign','big_query_campaign']
for t in candidates:
    try:
        print('\n--- PRAGMA table_info({}) ---'.format(t))
        cols = cur.execute(f"PRAGMA table_info('{t}')").fetchall()
        for c in cols:
            print(c)
        if not cols:
            print('(no columns or table not found)')
    except Exception as e:
        print('error for', t, e)

con.close()
