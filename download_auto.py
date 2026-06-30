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
import time
import schedule
import threading
from datetime import datetime, timedelta
from pathlib import Path

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

# ========== CONFIGURACIÓN ==========
BASE_DIR = Path(__file__).resolve().parent

# Intentar importar config
try:
    from config import (
        BASE_DIR as CONFIG_BASE_DIR,
        obtener_fechas_descarga, 
        obtener_servidores,
        obtener_token, 
        HORARIOS_EJECUCION,
        MODO_DESCARGA,
        DESCARGAR_CDR
    )
    if CONFIG_BASE_DIR:
        BASE_DIR = Path(CONFIG_BASE_DIR)
    logger.info("✅ Configuración cargada desde config.py")
except ImportError as e:
    logger.warning(f"⚠️ No se encontró config.py: {e}")
    CONFIG_JSON = {}
    
    def obtener_fechas_descarga():
        return [datetime.now().strftime("%Y-%m-%d")]
    
    def obtener_servidores():
        return []
    
    def obtener_token(nombre=None):
        return ""
    
    HORARIOS_EJECUCION = ["08:00", "12:00", "18:00"]
    MODO_DESCARGA = "hoy"
    DESCARGAR_CDR = True

# Mapeo de servidores
CORTE_A_SERVIDOR = {
    1: "operacion-interna",
    2: "qnt_juridico_blaster",
    3: "qnt_cobro_blaster",
    4: "Qnt_RBK_blaster",
    5: "Qnt_recaudo_blaster",
    6: "qnt_digital"
}

_scheduler_running = False
_scheduler_thread = None

# ========== FUNCIONES DE UTILIDAD ==========

def _to_wolkvox_ts(fecha: str, is_end: bool = False) -> str:
    """Convierte fecha YYYY-MM-DD a formato Wolkvox YYYYMMDDHHMMSS"""
    try:
        d = datetime.strptime(fecha, '%Y-%m-%d')
        dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
        return dt.strftime('%Y%m%d%H%M%S')
    except:
        return fecha

def _safe_filename(text: str) -> str:
    """Sanitiza nombres de archivo"""
    import re
    text = (text or "").strip() or "server"
    return re.sub(r"[^0-9A-Za-z._-]", "_", text)[:120]

# ========== FUNCIONES DE DESCARGA ==========

def descargar_reporte_cdr(servidor: dict, fecha: str) -> dict:
    """Descarga el reporte CDR para un servidor y fecha específica"""
    try:
        nombre = servidor.get('name', '')
        url_base = servidor.get('url', '').strip().rstrip('/')
        token = servidor.get('token') or obtener_token(nombre)
        
        if not url_base:
            logger.error(f"❌ URL base vacía para {nombre}")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        if not url_base.lower().startswith('http'):
            base_url = f"https://wv{url_base}.wolkvox.com"
        else:
            base_url = url_base
        
        date_ini_ts = _to_wolkvox_ts(fecha, is_end=False)
        date_end_ts = _to_wolkvox_ts(fecha, is_end=True)
        
        url = (
            f"{base_url}/api/v2/reports_manager.php"
            f"?api=cdr_1"
            f"&date_ini={date_ini_ts}"
            f"&date_end={date_end_ts}"
        )
        
        headers = {"wolkvox-token": token} if token else {}
        
        logger.info(f"   📡 Descargando {nombre} para {fecha}")
        
        response = requests.get(url, headers=headers, timeout=60)
        
        if response.status_code != 200:
            logger.error(f"   ❌ Error {response.status_code}")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        try:
            data_json = response.json()
        except json.JSONDecodeError:
            logger.error(f"   ❌ Respuesta no es JSON válido")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        rows = []
        if isinstance(data_json, dict):
            if data_json.get('error'):
                logger.error(f"   ❌ Error en API: {data_json.get('error')}")
                return {'success': False, 'data': None, 'servidor': nombre}
            if 'data' in data_json and isinstance(data_json['data'], list):
                rows = data_json['data']
            else:
                rows = [data_json]
        elif isinstance(data_json, list):
            rows = data_json
        
        if not rows:
            logger.warning(f"   ⚠️ Sin datos para {nombre} - {fecha}")
            return {'success': False, 'data': None, 'servidor': nombre}
        
        return {
            'success': True,
            'data': rows,
            'servidor': nombre
        }
        
    except Exception as e:
        logger.error(f"   ❌ Error descargando {servidor.get('name', '')}: {e}")
        return {'success': False, 'data': None, 'servidor': servidor.get('name', '')}

def guardar_reporte_cdr(data: list, servidor: str, fecha: str):
    """Guarda el reporte CDR en formato Excel"""
    try:
        mes = fecha[:7]
        carpeta_dia = os.path.join(str(BASE_DIR), mes, fecha)
        os.makedirs(carpeta_dia, exist_ok=True)
        
        nombre_archivo = f"{servidor}_{fecha}.xlsx"
        ruta_archivo = os.path.join(carpeta_dia, nombre_archivo)
        
        if not data:
            logger.warning(f"   ⚠️ No hay datos para {servidor} - {fecha}")
            return False
        
        df = pd.DataFrame(data)
        df['servidor'] = servidor
        
        # Guardar como Excel
        df.to_excel(ruta_archivo, index=False)
        logger.info(f"      ✅ Guardado: {nombre_archivo}")
        return True
        
    except Exception as e:
        logger.error(f"   ❌ Error guardando reporte: {e}")
        return False

# ========== FUNCIONES PRINCIPALES ==========

def descargar_todos_los_reportes(fecha: str = None):
    """Descarga reportes CDR para todos los servidores"""
    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")
    
    logger.info("\n" + "="*60)
    logger.info("📥 INICIANDO DESCARGA DE REPORTES (CDR)")
    logger.info(f"📅 Fecha: {fecha}")
    logger.info("="*60)
    
    servidores = obtener_servidores()
    
    if not servidores:
        logger.error("❌ No hay servidores configurados")
        return False
    
    descargados = 0
    fallidos = 0
    
    for servidor in servidores:
        nombre = servidor.get('name', '')
        if not nombre:
            continue
        
        if nombre not in CORTE_A_SERVIDOR.values():
            logger.info(f"⏭️ Servidor {nombre} no está en el mapeo de cortes")
            continue
            
        logger.info(f"\n📂 Procesando servidor: {nombre}")
        resultado = descargar_reporte_cdr(servidor, fecha)
        
        if not resultado['success']:
            fallidos += 1
            continue
        
        if guardar_reporte_cdr(resultado['data'], nombre, fecha):
            descargados += 1
        
        time.sleep(2)
    
    logger.info("\n" + "="*60)
    logger.info(f"📊 RESUMEN CDR: {descargados} descargados, {fallidos} fallidos")
    logger.info("="*60)
    
    # ===== EJECUTAR BIGQUERY PROCESSOR =====
    if descargados > 0:
        try:
            from bigquery_processor import procesar_y_actualizar_bigquery
            logger.info(f"\n🔄 Procesando BigQuery para {fecha}...")
            procesar_y_actualizar_bigquery(fecha=fecha)
        except Exception as e:
            logger.error(f"❌ Error en BigQuery: {e}")
    
    return descargados > 0

def descargar_segun_configuracion():
    """Descarga según la configuración"""
    fechas = obtener_fechas_descarga()
    for fecha in fechas:
        descargar_todos_los_reportes(fecha)

# ========== FUNCIONES PARA BACKEND ==========
# NOTA: Los nombres de las funciones DEBEN coincidir con lo que espera backend.py

def iniciar_scheduler():  # ← Este es el nombre que espera backend.py
    """Inicia el scheduler para ejecutar descargas automáticas"""
    global _scheduler_running, _scheduler_thread
    
    if _scheduler_running:
        logger.info("⚠️ El scheduler CDR ya está en ejecución")
        return True
    
    try:
        def ejecutar_descarga():
            logger.info(f"\n⏰ Ejecución programada CDR a las {datetime.now().strftime('%H:%M')}")
            try:
                descargar_segun_configuracion()
            except Exception as e:
                logger.error(f"❌ Error en descarga programada: {e}")
        
        for horario in HORARIOS_EJECUCION:
            schedule.every().day.at(horario).do(ejecutar_descarga)
            logger.info(f"   ✅ CDR programada a las {horario}")
        
        _scheduler_running = True
        
        def run_scheduler():
            while _scheduler_running:
                schedule.run_pending()
                time.sleep(30)
        
        _scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        _scheduler_thread.start()
        
        logger.info("✅ Scheduler CDR iniciado")
        return True
            
    except Exception as e:
        logger.error(f"❌ Error iniciando scheduler CDR: {e}")
        return False

def detener_scheduler():  # ← Este es el nombre que espera backend.py
    """Detiene el scheduler de CDR"""
    global _scheduler_running
    _scheduler_running = False
    logger.info("⏹️ Scheduler CDR detenido")
    return True

def estado_scheduler():  # ← Este es el nombre que espera backend.py
    """Retorna el estado actual del scheduler"""
    try:
        trabajos = schedule.get_jobs()
        proximos = []
        for job in trabajos:
            if hasattr(job, 'next_run') and job.next_run:
                proximos.append({
                    'horario': job.next_run.strftime('%H:%M'),
                    'fecha': job.next_run.strftime('%Y-%m-%d %H:%M:%S')
                })
        
        return {
            'running': _scheduler_running,
            'horarios': HORARIOS_EJECUCION,
            'proximos': proximos,
            'modo': MODO_DESCARGA,
            'base_dir': str(BASE_DIR),
            'fechas': obtener_fechas_descarga(),
            'servidores': [s.get('name') for s in obtener_servidores()]
        }
    except Exception as e:
        return {
            'running': False,
            'error': str(e),
            'horarios': HORARIOS_EJECUCION,
            'modo': MODO_DESCARGA
        }

def init_auto_download():  # ← Este es el nombre que espera backend.py
    """Inicializa el sistema de descargas automáticas"""
    return iniciar_scheduler()

# ========== EJECUCIÓN DIRECTA ==========

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--scheduler":
            iniciar_scheduler()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                detener_scheduler()
        else:
            descargar_todos_los_reportes(sys.argv[1])
    else:
        descargar_segun_configuracion()