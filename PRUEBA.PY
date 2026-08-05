from services.email_client import EmailClient
import json

with open('config.json') as f:
    config = json.load(f)

api_key = config['email']['api_key']
client = EmailClient(api_key)

campos_faltantes = [
    "Valor a pagar",
    "Valor Oferta Esp",
    "Porcentaje",
    "Fecha de Pago"
]

for nombre in campos_faltantes:
    resultado = client.crear_campo_personalizado(nombre)
    print(f"{nombre}: {resultado}")