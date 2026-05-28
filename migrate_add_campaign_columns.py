import sqlite3

DB='app.db'
con=sqlite3.connect(DB)
cur=con.cursor()

table='big_query_campaign'
cur.execute(f"PRAGMA table_info('{table}')")
cols=[r[1] for r in cur.fetchall()]
print('Existing columns:',cols)
needed=[('nombre','TEXT'),('operacion','TEXT'),('tipo','TEXT'),('fecha_inicio','DATETIME'),('consulta','TEXT'),('usuario','TEXT')]
for name,typ in needed:
    if name not in cols:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
            print('Added column',name)
        except Exception as e:
            print('Failed to add',name,e)
con.commit()
con.close()
print('Done')
