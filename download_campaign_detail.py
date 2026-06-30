#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script para descargar reportes AMD (campaign_7) con nombres de campaña.
"""

import os
import json
import requests
import io
import re
import time
import schedule
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Configuración de logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("download_campaign_detail.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== CONFIGURACIÓN ==========
BASE_DIR = Path(__file__).resolve().parent

try:
    from config import (
        BASE_DIR as CONFIG_BASE_DIR,
        HORARIOS_EJECUCION,
        DESCARGAR_AMD,
        MODO_DESCARGA,
        obtener_fechas_descarga,
        obtener_servidores,
        CONFIG_JSON
    )
    if CONFIG_BASE_DIR:
        BASE_DIR = Path(CONFIG_BASE_DIR)
    logger.info("✅ Configuración cargada desde config.py")
except ImportError as e:
    logger.warning(f"⚠️ No se encontró config.py: {e}")
    CONFIG_JSON = {}
    HORARIOS_EJECUCION = ["08:00", "12:00", "18:00"]
    DESCARGAR_AMD = True
    MODO_DESCARGA = "hoy"
    
    def obtener_fechas_descarga():
        return [datetime.now().strftime("%Y-%m-%d")]
    
    def obtener_servidores():
        return []

SERVIDORES = [
    "operacion-interna",
    "qnt_juridico_blaster",
    "qnt_cobro_blaster",
    "Qnt_RBK_blaster",
    "Qnt_recaudo_blaster",
    "qnt_digital"
]

_scheduler_running_amd = False
_scheduler_thread_amd = None
_descarga_lock_amd = threading.Lock()

# ========== FUNCIONES ==========

def get_server(name):
    for s in CONFIG_JSON.get('servers', []):
        if s.get('name') == name:
            url = s.get('url', '').strip().rstrip('/')
            token = s.get('token') or CONFIG_JSON.get('wolkvox-token')
            if url and token:
                return {'name': name, 'url': url, 'token': token}
    return None

def obtener_nombre_campana_especifica(servidor, campaign_id):
    try:
        camp_id_clean = re.sub(r'[^0-9]', '', str(campaign_id))
        if not camp_id_clean:
            return None

        url = f"{servidor['url']}/api/v2/real_time.php?api=campaigns&campaign_id={campaign_id}"
        headers = {"wolkvox-token": servidor['token']}

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            campaigns = data.get('data', [])
            for camp in campaigns:
                camp_str = camp.get('campaign', '')
                if camp_str and camp_str.startswith(camp_id_clean):
                    parts = camp_str.split(' - ', 1)
                    nombre = parts[1] if len(parts) > 1 else camp_str
                    nombre_limpio = re.sub(r'\s*-\s*Strategy\s*\([^)]*\)\s*$', '', nombre)
                    nombre_limpio = re.sub(r'\s*-\s*[a-zA-Z0-9_]+\s*\([^)]*\)\s*$', '', nombre_limpio)
                    return nombre_limpio if nombre_limpio else nombre
        return None
    except Exception as e:
        logger.debug(f"Error: {e}")
        return None

def to_ts(fecha, is_end=False):
    try:
        d = datetime.strptime(fecha, '%Y-%m-%d')
        dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
        return dt.strftime('%Y%m%d%H%M%S')
    except:
        return fecha

def descargar_reporte(servidor, fecha):
    url = f"{servidor['url']}/api/v2/reports_manager.php?api=campaign_7&campaign_id=ALL&date_ini={to_ts(fecha)}&date_end={to_ts(fecha, True)}"
    try:
        resp = requests.get(url, headers={"wolkvox-token": servidor['token']}, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('data'):
                return resp.content
        return None
    except Exception as e:
        logger.error(f"Error descargando: {e}")
        return None

def guardar_excel_simple(contenido, servidor, fecha):
    """Guarda el contenido como Excel simple usando pandas"""
    try:
        import pandas as pd
        data = json.loads(contenido)
        rows = data.get('data', [])
        
        if not rows:
            return False
        
        df = pd.DataFrame(rows)
        
        mes = fecha[:7]
        dir_path = os.path.join(str(BASE_DIR), mes, fecha, "campanas")
        os.makedirs(dir_path, exist_ok=True)
        ruta = os.path.join(dir_path, f"{servidor}_campaign_ALL_{fecha}.xlsx")
        
        df.to_excel(ruta, index=False)
        logger.info(f"   💾 Guardado: {os.path.basename(ruta)}")
        return True
    except Exception as e:
        logger.error(f"   ❌ Error guardando: {e}")
        return False

def descargar_todos_los_reportes_amd(fecha: str = None):
    with _descarga_lock_amd:
        if fecha is None:
            fecha = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"\n{'='*60}")
        logger.info(f"📥 INICIANDO DESCARGA DE REPORTES AMD")
        logger.info(f"📅 Fecha: {fecha}")
        logger.info(f"{'='*60}")

        if not os.path.exists(str(BASE_DIR)):
            logger.error(f"❌ La ruta base {BASE_DIR} no existe")
            return

        total_exitosos = 0
        total_fallidos = 0

        for nombre in SERVIDORES:
            servidor = get_server(nombre)
            if not servidor:
                logger.warning(f"⚠️ Servidor {nombre} no configurado")
                continue

            logger.info(f"\n📡 Servidor: {nombre}")
            contenido = descargar_reporte(servidor, fecha)
            if contenido is None:
                logger.info(f"   ⏭️ Sin datos para {nombre}")
                total_fallidos += 1
                continue

            if guardar_excel_simple(contenido, nombre, fecha):
                total_exitosos += 1
            else:
                total_fallidos += 1

            time.sleep(1)

        logger.info(f"\n📊 RESUMEN AMD - Fecha {fecha}")
        logger.info(f"  ✅ Éxitos: {total_exitosos}")
        logger.info(f"  ❌ Fallos: {total_fallidos}")
        
        # ===== EJECUTAR BIGQUERY PROCESSOR =====
        if total_exitosos > 0:
            try:
                from bigquery_processor import procesar_y_actualizar_bigquery
                logger.info(f"\n🔄 Procesando BigQuery para {fecha}...")
                procesar_y_actualizar_bigquery(fecha=fecha)
            except Exception as e:
                logger.error(f"❌ Error en BigQuery: {e}")

# ========== FUNCIONES PARA BACKEND ==========

def iniciar_scheduler_amd():  # ← Este es el nombre que espera backend.py
    global _scheduler_running_amd, _scheduler_thread_amd
    if _scheduler_running_amd:
        logger.info("⚠️ El scheduler AMD ya está en ejecución")
        return True

    try:
        def ejecutar_descarga():
            logger.info(f"\n⏰ Ejecución programada AMD a las {datetime.now().strftime('%H:%M')}")
            try:
                descargar_segun_configuracion_amd()
            except Exception as e:
                logger.error(f"❌ Error en descarga AMD: {e}")
        
        for horario in HORARIOS_EJECUCION:
            schedule.every().day.at(horario).do(ejecutar_descarga)
            logger.info(f"   ✅ AMD programada a las {horario}")
        
        _scheduler_running_amd = True
        
        def run_scheduler_amd():
            while _scheduler_running_amd:
                schedule.run_pending()
                time.sleep(30)
        
        _scheduler_thread_amd = threading.Thread(target=run_scheduler_amd, daemon=True)
        _scheduler_thread_amd.start()
        
        logger.info("✅ Scheduler AMD iniciado")
        return True
            
    except Exception as e:
        logger.error(f"❌ Error iniciando scheduler AMD: {e}")
        return False

def detener_scheduler_amd():  # ← Este es el nombre que espera backend.py
    global _scheduler_running_amd
    _scheduler_running_amd = False
    logger.info("⏹️ Scheduler AMD detenido")
    return True

def estado_scheduler_amd():  # ← Este es el nombre que espera backend.py
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
            'running': _scheduler_running_amd,
            'horarios': HORARIOS_EJECUCION,
            'proximos': proximos,
            'modo_descarga': MODO_DESCARGA,
            'base_dir': str(BASE_DIR),
            'fechas': obtener_fechas_descarga(),
            'servidores': SERVIDORES
        }
    except Exception as e:
        return {
            'running': False,
            'error': str(e),
            'horarios': HORARIOS_EJECUCION,
            'modo_descarga': MODO_DESCARGA
        }

def init_auto_download_amd():  # ← Este es el nombre que espera backend.py
    return iniciar_scheduler_amd()

def descargar_segun_configuracion_amd():
    if not DESCARGAR_AMD:
        logger.info("⏭️ Descargas AMD desactivadas")
        return
    fechas = obtener_fechas_descarga()
    for fecha in fechas:
        descargar_todos_los_reportes_amd(fecha)

# ========== EJECUCIÓN DIRECTA ==========

if __name__ == "__main__":
    init_auto_download_amd()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        detener_scheduler_amd()
        logger.info("Script AMD finalizado")