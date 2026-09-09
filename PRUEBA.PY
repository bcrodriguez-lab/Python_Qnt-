#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PRUEBA: Enviar múltiples mensajes para obtener bulkId
"""

import json
import re
import time
import unicodedata
import requests
import pandas as pd
from datetime import datetime

print("\n" + "="*60)
print("🧪 PRUEBA: ENVÍO DE MÚLTIPLES MENSAJES + BULKID")
print("="*60)

# ==================================================
# ⚙️ CONFIGURACIÓN
# ==================================================

API_KEY = "04ca14df6c5a47e6713cdb13e8272ab3-aa42acbb-c69f-4a29-a3f5-3ec8b2fae16d"
BASE_URL = "https://2knxjz.api.infobip.com"
SENDER_ID = "599982"
MAX_REINTENTOS = 2

# ==================================================
# 📡 CLIENTE INFOBIP V2
# ==================================================

class InfobipSenderV2:
    def __init__(self, api_key, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"App {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def enviar_lote(self, lista_mensajes, reintentos=MAX_REINTENTOS):
        url = f"{self.base_url}/sms/2/text/advanced"
        payload = {
            "messages": lista_mensajes,
            "urlOptions": {
                "shortenUrl": True,
                "trackClicks": True,
                "removeProtocol": False
            }
        }
        
        print(f"\n📤 URL: {url}")
        print(f"📦 Enviando {len(lista_mensajes)} mensajes...")
        
        for intento in range(1, reintentos + 1):
            try:
                resp = self.session.post(url, json=payload)
                print(f"📡 Código: {resp.status_code}")
                
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    return data
                else:
                    print(f"⚠️ Intento {intento}: Error {resp.status_code}")
                    if intento < reintentos:
                        time.sleep(2 * intento)
            except Exception as e:
                print(f"⚠️ Intento {intento}: {e}")
                if intento < reintentos:
                    time.sleep(2 * intento)
        return None

# ==================================================
# 🧠 FUNCIONES
# ==================================================

def remover_tildes(texto):
    nfkd_form = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def construir_mensaje(row, plantilla):
    msg = plantilla
    for key, value in row.items():
        if key not in ["Celular_limpio", "telefono"]:
            msg = msg.replace(f"{{{{{key}}}}}", str(value) if value else "")
    msg = remover_tildes(msg)
    return msg.strip()

# ==================================================
# 📤 ENVIAR MÚLTIPLES MENSAJES
# ==================================================

def enviar_multiples():
    """Envía 3 mensajes de prueba para obtener bulkId"""
    
    plantilla = "{{Nombre}}, conoce GRATIS tu situacion financiera. Registrate en VALORO: {{Link}}"
    
    # 3 destinatarios de prueba (usa números reales si los tienes)
    destinatarios = [
        {"Nombre": "John", "Link": "https://valoro.com", "telefono": "573144051619"},
        {"Nombre": "Maria", "Link": "https://valoro.com", "telefono": "573001234567"},
        {"Nombre": "Carlos", "Link": "https://valoro.com", "telefono": "573223189873"}
    ]
    
    mensajes = []
    for datos in destinatarios:
        texto = construir_mensaje(datos, plantilla)
        mensajes.append({
            "from": SENDER_ID,
            "destinations": [{"to": datos["telefono"]}],
            "text": texto,
            "callbackData": json.dumps(datos, ensure_ascii=False)
        })
        print(f"\n📱 {datos['telefono']}: {texto[:60]}...")
    
    # Enviar
    print(f"\n📤 ENVIANDO {len(mensajes)} MENSAJES...")
    cliente = InfobipSenderV2(API_KEY, BASE_URL)
    respuesta = cliente.enviar_lote(mensajes)
    
    if respuesta:
        bulk_id = respuesta.get('bulkId')
        print(f"\n📦 Respuesta completa:")
        print(json.dumps(respuesta, indent=2, ensure_ascii=False))
        
        if bulk_id:
            print(f"\n✅ ¡BULK ID OBTENIDO!")
            print(f"   📦 Bulk ID: {bulk_id}")
            
            # Guardar
            with open("bulk_id_guardado.txt", "w") as f:
                f.write(f"Bulk ID: {bulk_id}\n")
                f.write(f"Fecha: {datetime.now().isoformat()}\n")
                f.write(f"Mensajes: {len(mensajes)}\n")
            print(f"   💾 Guardado en: bulk_id_guardado.txt")
            
            return {
                "success": True,
                "bulk_id": bulk_id,
                "respuesta": respuesta
            }
        else:
            print(f"\n⚠️ No se recibió bulkId")
            print("   Esto puede pasar si solo envías 1 mensaje.")
            print("   Para obtener bulkId necesitas enviar varios mensajes.")
            return {
                "success": False,
                "error": "No se recibió bulkId"
            }
    else:
        print(f"\n❌ Error: No se recibió respuesta")
        return {"success": False, "error": "Sin respuesta"}

# ==================================================
# 📊 CONSULTAR REPORTE
# ==================================================

def consultar_reporte(bulk_id):
    print(f"\n📊 CONSULTANDO REPORTE: {bulk_id}")
    
    url = f"{BASE_URL}/sms/3/reports?bulkId={bulk_id}"
    headers = {"Authorization": f"App {API_KEY}", "Accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        print(f"📡 Código: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            resultados = data.get("results", [])
            print(f"📋 Resultados: {len(resultados)} mensajes")
            return {"success": True, "data": data, "resultados": resultados}
        else:
            return {"success": False, "error": response.text}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==================================================
# 📊 MOSTRAR REPORTE
# ==================================================

def mostrar_reporte(resultados):
    if not resultados:
        print("⚠️ No hay resultados")
        return
    
    print("\n" + "="*60)
    print("📊 REPORTE DETALLADO")
    print("="*60)
    
    entregados = fallidos = pendientes = 0
    
    for i, item in enumerate(resultados):
        status = item.get("status", {})
        group_name = status.get("groupName", "UNKNOWN")
        
        if group_name == "DELIVERED":
            entregados += 1
        elif group_name in ["PENDING", "ACCEPTED"]:
            pendientes += 1
        else:
            fallidos += 1
        
        print(f"\n--- Mensaje {i+1} ---")
        print(f"   📱 Teléfono: {item.get('to', '?')}")
        print(f"   📊 Estado: {group_name} - {status.get('name', '?')}")
        print(f"   📝 {status.get('description', '?')}")
        print(f"   🕐 Enviado: {item.get('sentAt', '?')}")
        print(f"   🕐 Entregado: {item.get('doneAt', '?')}")
        print(f"   💰 Precio: {item.get('price', {}).get('pricePerMessage', '?')} {item.get('price', {}).get('currency', '')}")
    
    print("\n" + "="*60)
    print("📈 RESUMEN")
    print("="*60)
    print(f"   📦 Total: {len(resultados)}")
    print(f"   ✅ Entregados: {entregados}")
    print(f"   ❌ Fallidos: {fallidos}")
    print(f"   ⏳ Pendientes: {pendientes}")
    
    if len(resultados) > 0:
        print(f"   📈 Tasa de entrega: {(entregados/len(resultados))*100:.2f}%")

# ==================================================
# 🚀 MAIN
# ==================================================

def main():
    # PASO 1: Enviar múltiples mensajes
    print("\n📤 PASO 1: Enviando múltiples mensajes")
    envio = enviar_multiples()
    
    if not envio.get("success"):
        print(f"\n❌ Error: {envio.get('error')}")
        return
    
    bulk_id = envio.get("bulk_id")
    
    # PASO 2: Esperar
    print(f"\n⏳ Esperando 30 segundos para procesamiento...")
    for i in range(30, 0, -1):
        print(f"   {i} segundos restantes...", end="\r")
        time.sleep(1)
    print("   ✅ Espera completada")
    
    # PASO 3: Consultar reporte
    print(f"\n📊 PASO 3: Consultando reporte")
    reporte = consultar_reporte(bulk_id)
    
    if not reporte.get("success"):
        print(f"❌ Error: {reporte.get('error')}")
        return
    
    # PASO 4: Mostrar resultados
    mostrar_reporte(reporte.get("resultados", []))
    
    print("\n" + "="*60)
    print("✅ PRUEBA COMPLETADA")
    print("="*60)

if __name__ == "__main__":
    if API_KEY == "xx":
        print("⚠️ Configura tu API_KEY")
        exit(1)
    main()