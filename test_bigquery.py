"""Script de prueba para verificar conexión y tabla"""
from conexion_bigquery import get_bigquery_client

# Probar conexión
client = get_bigquery_client()
print("✅ Cliente BigQuery OK")

# Probar que la tabla existe y tiene datos
query = "SELECT COUNT(*) as cnt FROM Temporal.Robots_Temporal"
result = client.query(query).result()
for row in result:
    print(f"✅ Registros en Temporal.Robots_Temporal: {row.cnt}")

# Ver últimas fechas cargadas
query_fechas = """
    SELECT Fecha_dia, COUNT(*) as cnt 
    FROM Temporal.Robots_Temporal 
    GROUP BY Fecha_dia 
    ORDER BY Fecha_dia DESC 
    LIMIT 5
"""
result = client.query(query_fechas).result()
print("\nÚltimas fechas:")
for row in result:
    print(f"  {row.Fecha_dia}: {row.cnt} registros")