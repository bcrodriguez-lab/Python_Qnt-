#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Módulo para descarga automática de reportes de llamadas (CDR)
Cada servidor se descarga por separado (no en paralelo)
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
        logging.FileHandler('download_auto.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def descargar_reporte_cdr(servidor: dict, fecha: str) -> dict:
    """
    Descarga el reporte CDR para un servidor y fecha específica
    """
    try:
        nombre = servidor.get('name', '')
        url_base = obtener_url_base(nombre)
        token = obtener_token(nombre)
        
        # Construir URL para CDR
        url = f"{url_base}/api/v1/report/call_details"
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        # Datos de la solicitud
        data = {
            'date': fecha,
            'type': 'json'
        }
        
        logger.info(f"   📡 Descargando {nombre} para {fecha}")
        
        response = requests.post(url, headers=headers, json=data, timeout=300)
        
        if response.status_code != 200:
            logger.error(f"   ❌ Error {response.status_code}: {response.text[:200]}")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        # Verificar que la respuesta es JSON válido
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
        logger.error(f"   ❌ Error descargando {servidor.get('name', '')}: {e}")
        return {'success': False, 'data': None, 'servidor': servidor.get('name', '')}


def guardar_reporte_cdr(data: dict, servidor: str, fecha: str):
    """
    Guarda el reporte CDR en formato Excel
    """
    try:
        # Crear estructura de carpetas
        mes = fecha[:7]  # YYYY-MM
        carpeta_dia = os.path.join(BASE_DIR, mes, fecha)
        os.makedirs(carpeta_dia, exist_ok=True)
        
        # Nombre del archivo
        nombre_archivo = f"{servidor}_{fecha}.xlsx"
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
        
        # Guardar como Excel con formato
        build_wolkvox_excel(df, ruta_archivo)
        
        logger.info(f"      ✅ Guardado: {nombre_archivo}")
        return True
        
    except Exception as e:
        logger.error(f"   ❌ Error guardando reporte: {e}")
        return False


def descargar_todos_los_reportes(fecha: str = None):
    """
    Descarga reportes CDR para todos los servidores y una fecha específica
    """
    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")
    
    logger.info("\n" + "="*60)
    logger.info("📥 INICIANDO DESCARGA DE REPORTES (CDR)")
    logger.info(f"📅 Fecha: {fecha}")
    logger.info(f"📁 Ruta: {os.path.join(BASE_DIR, fecha[:7], fecha)}")
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
        
        # Descargar
        resultado = descargar_reporte_cdr(servidor, fecha)
        
        if not resultado['success']:
            fallidos += 1
            continue
        
        # Guardar
        if guardar_reporte_cdr(resultado['data'], nombre, fecha):
            descargados += 1
        
        # Pequeña pausa entre servidores para evitar límites de rate
        time.sleep(2)
    
    logger.info("\n" + "="*60)
    logger.info(f"📊 RESUMEN CDR: {descargados} descargados, {fallidos} fallidos")
    logger.info("="*60)
    
    return descargados > 0


def descargar_segun_configuracion():
    """
    Descarga según la configuración (modo: hoy, rango, fecha_especifica)
    """
    fechas = obtener_fechas_descarga()
    logger.info(f"📋 Fechas a descargar: {fechas}")
    
    for fecha in fechas:
        descargar_todos_los_reportes(fecha)


def iniciar_scheduler():
    """
    Inicia el scheduler para ejecutar descargas automáticas
    """
    try:
        import schedule
        import threading
        from datetime import datetime
        
        logger.info("="*60)
        logger.info("⏰ INICIANDO SCHEDULER DE CDR")
        logger.info(f"📋 Horarios configurados: {HORARIOS_EJECUCION}")
        logger.info("="*60)
        
        def ejecutar_descarga():
            logger.info(f"\n⏰ Ejecución programada a las {datetime.now().strftime('%H:%M')}")
            try:
                descargar_segun_configuracion()
            except Exception as e:
                logger.error(f"❌ Error en descarga programada: {e}")
        
        # Programar las descargas
        for horario in HORARIOS_EJECUCION:
            schedule.every().day.at(horario).do(ejecutar_descarga)
            logger.info(f"   ✅ Programada a las {horario}")
        
        # Ejecutar una vez al inicio
        logger.info("\n🚀 Ejecutando descarga inicial...")
        ejecutar_descarga()
        
        # Bucle principal del scheduler
        logger.info("\n🔄 Scheduler de CDR en ejecución...")
        while True:
            schedule.run_pending()
            time.sleep(30)
            
    except ImportError:
        logger.error("❌ Error: schedule no está instalado. Ejecuta: pip install schedule")
        return
    except KeyboardInterrupt:
        logger.info("\n⏹️ Scheduler de CDR detenido manualmente")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--scheduler":
            iniciar_scheduler()
        else:
            descargar_todos_los_reportes(sys.argv[1])
    else:
        descargar_segun_configuracion()