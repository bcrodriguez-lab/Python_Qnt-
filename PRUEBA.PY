python -c "
import sqlite3
con = sqlite3.connect('app.db')

# 1. Eliminar tabla existente
con.execute('DROP TABLE IF EXISTS programaciones_campanas')

# 2. Crear tabla COMPLETA
con.execute('''
CREATE TABLE programaciones_campanas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre VARCHAR(255) NOT NULL,
    campana_id INTEGER NOT NULL,
    bigquery_query TEXT NOT NULL,
    wolkvox_campaign_id VARCHAR(100) NOT NULL,
    server_name VARCHAR(255) NOT NULL,
    tipo_programacion VARCHAR(20) DEFAULT 'simple',
    fecha_programada DATETIME,
    hora_inicio VARCHAR(5) DEFAULT '08:00',
    fecha_fin VARCHAR(10),
    estado VARCHAR(20) DEFAULT 'pendiente',
    fecha_ejecucion DATETIME,
    total_destinatarios INTEGER DEFAULT 0,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

con.commit()
print('✅ Tabla recreada con todas las columnas')
con.close()
"