#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Módulo para descarga automática de reportes de detalle de campañas (AMD)
"""

import os
import sys
import json
import requests
import pandas as pd
import logging
from datetime import datetime, timedelta
import time
from config import (
    BASE_DIR, obtener_fechas_descarga, obtener_servidores,
    obtener_token, obtener_url_base, HORARIOS_EJECUCION
)
from excel_report_builder import build_wolkvox_excel

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('download_campaign_detail.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def obtener_nombres_campanas(servidor: dict) -> dict:
    """
    Obtiene el mapeo de campaign_id a campaign_name para un servidor
    """
    try:
        nombre = servidor.get('name', '')
        url_base = obtener_url_base(nombre)
        token = obtener_token(nombre)
        
        url = f"{url_base}/api/v1/real_time.php?api=campaigns"
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.warning(f"   ⚠️ Error obteniendo campañas: {response.status_code}")
            return {}
        
        try:
            data = response.json()
            campaigns = {}
            if isinstance(data, list):
                for camp in data:
                    if 'id' in camp and 'name' in camp:
                        campaigns[str(camp['id'])] = camp['name']
            return campaigns
        except json.JSONDecodeError:
            logger.warning(f"   ⚠️ Respuesta no es JSON válido")
            return {}
            
    except Exception as e:
        logger.warning(f"   ⚠️ Error obteniendo nombres de campañas: {e}")
        return {}


def descargar_reporte_amd(servidor: dict, fecha: str, campaign_id: str = "ALL") -> dict:
    """
    Descarga el reporte AMD para un servidor y fecha específica
    """
    try:
        nombre = servidor.get('name', '')
        url_base = obtener_url_base(nombre)
        token = obtener_token(nombre)
        
        url = f"{url_base}/api/v1/report/campaign_details"
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'date': fecha,
            'campaign_id': campaign_id,
            'type': 'json'
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=300)
        
        if response.status_code != 200:
            logger.error(f"   ❌ Error {response.status_code}: {response.text[:200]}")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        try:
            data_json = response.json()
        except json.JSONDecodeError:
            logger.error(f"   ❌ Respuesta no es JSON válido")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        return {
            'success': True,
            'data': data_json,
            'servidor': nombre
        }
        
    except Exception as e:
        logger.error(f"   ❌ Error descargando AMD: {e}")
        return {'success': False, 'data': None, 'servidor': servidor.get('name', '')}


def guardar_reporte_amd(data: dict, servidor: str, fecha: str, campaign_name: str = "ALL"):
    """
    Guarda el reporte AMD en formato Excel
    """
    try:
        # Crear estructura de carpetas (subcarpeta 'campanas')
        mes = fecha[:7]
        carpeta_dia = os.path.join(BASE_DIR, mes, fecha, "campanas")
        os.makedirs(carpeta_dia, exist_ok=True)
        
        # Nombre del archivo
        nombre_archivo = f"{servidor}_campaign_{campaign_name}_{fecha}.xlsx"
        ruta_archivo = os.path.join(carpeta_dia, nombre_archivo)
        
        # Extraer datos
        if isinstance(data, dict) and 'data' in data:
            rows = data['data']
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        
        if not rows:
            logger.warning(f"   ⚠️ No hay datos para {servidor} - {fecha}")
            return False
        
        # Convertir a DataFrame
        df = pd.DataFrame(rows)
        
        # Agregar columna de servidor
        df['servidor'] = servidor
        
        # Guardar como Excel
        build_wolkvox_excel(df, ruta_archivo)
        
        logger.info(f"      ✅ Guardado: {nombre_archivo}")
        return True
        
    except Exception as e:
        logger.error(f"   ❌ Error guardando reporte AMD: {e}")
        return False


def descargar_todos_los_reportes_amd(fecha: str = None):
    """
    Descarga reportes AMD para todos los servidores y una fecha específica
    """
    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")
    
    logger.info("\n" + "="*60)
    logger.info("📥 INICIANDO DESCARGA DE REPORTES (AMD)")
    logger.info(f"📅 Fecha: {fecha}")
    logger.info("="*60)
    
    servidores = obtener_servidores()
    
    if not servidores:
        logger.error("❌ No hay servidores configurados en config.json")
        return False
    
    descargados = 0
    fallidos = 0
    
    for servidor in servidores:
        nombre = servidor.get('name', '')
        if not nombre:
            continue
            
        logger.info(f"\n📂 Procesando servidor: {nombre}")
        
        # Obtener nombres de campañas
        campanas = obtener_nombres_campanas(servidor)
        
        if campanas:
            logger.info(f"   📋 {len(campanas)} campañas encontradas")
        
        # Descargar reporte general (ALL)
        logger.info(f"   📡 Descargando ALL para {nombre}")
        resultado = descargar_reporte_amd(servidor, fecha, "ALL")
        
        if resultado['success']:
            if guardar_reporte_amd(resultado['data'], nombre, fecha, "ALL"):
                descargados += 1
        else:
            fallidos += 1
        
        time.sleep(2)
    
    logger.info("\n" + "="*60)
    logger.info(f"📊 RESUMEN AMD: {descargados} descargados, {fallidos} fallidos")
    logger.info("="*60)
    
    return descargados > 0


def descargar_segun_configuracion_amd():
    """
    Descarga AMD según la configuración
    """
    fechas = obtener_fechas_descarga()
    logger.info(f"📋 Fechas a descargar (AMD): {fechas}")
    
    for fecha in fechas:
        descargar_todos_los_reportes_amd(fecha)


def iniciar_scheduler_amd():
    """
    Inicia el scheduler para ejecutar descargas automáticas de AMD
    """
    try:
        import schedule
        import threading
        from datetime import datetime
        
        logger.info("="*60)
        logger.info("⏰ INICIANDO SCHEDULER DE AMD")
        logger.info(f"📋 Horarios configurados: {HORARIOS_EJECUCION}")
        logger.info("="*60)
        
        def ejecutar_descarga():
            logger.info(f"\n⏰ Ejecución programada AMD a las {datetime.now().strftime('%H:%M')}")
            try:
                descargar_segun_configuracion_amd()
            except Exception as e:
                logger.error(f"❌ Error en descarga programada AMD: {e}")
        
        for horario in HORARIOS_EJECUCION:
            schedule.every().day.at(horario).do(ejecutar_descarga)
            logger.info(f"   ✅ Programada AMD a las {horario}")
        
        logger.info("\n🚀 Ejecutando descarga inicial AMD...")
        ejecutar_descarga()
        
        logger.info("\n🔄 Scheduler de AMD en ejecución...")
        while True:
            schedule.run_pending()
            time.sleep(30)
            
    except ImportError:
        logger.error("❌ Error: schedule no está instalado. Ejecuta: pip install schedule")
        return
    except KeyboardInterrupt:
        logger.info("\n⏹️ Scheduler de AMD detenido manualmente")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--scheduler":
            iniciar_scheduler_amd()
        else:
            descargar_todos_los_reportes_amd(sys.argv[1])
    else:
        descargar_segun_configuracion_amd()