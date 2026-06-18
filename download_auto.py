#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Módulo de descarga automática de reportes
Guarda los archivos XLSX en la carpeta de la fecha (sin subcarpetas de corte)
"""

import os
import time
import logging
import requests
import json
import schedule
import threading
import io  # <--- ESTA LÍNEA FALTABA
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from backend import app, logger, get_authorization_headers

# ========== CONFIGURACIÓN ==========
def cargar_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error cargando config.json: {e}")
        return {}

CONFIG = cargar_config()
BASE_DIR = CONFIG.get('base_dir', r"G:\Unidades compartidas\Analitica\Embudo de Conversión\Proyecto Robot Omnicanal\Producción")

# Mapeo: número de corte -> nombre del servidor (solo para identificación)
CORTE_A_SERVIDOR = {
    1: "operacion-interna",
    2: "qnt_juridico_blaster",
    3: "qnt_cobro_blaster",
    4: "Qnt_RBK_blaster",
    5: "Qnt_recaudo_blaster",
    6: "qnt_digital",
}

HORARIOS_EJECUCION = CONFIG.get('horarios_descarga', ["10:00", "12:00","12:29", "14:00", "16:00", "18:00"])

_scheduler_running = False
_scheduler_thread = None
_descarga_lock = threading.Lock()  # Evita ejecuciones simultáneas

# ========== VALIDACIÓN INICIAL ==========
def validar_ruta_base():
    if not os.path.exists(BASE_DIR):
        logger.warning(f"⚠️ La ruta base {BASE_DIR} no existe")
        return False
    else:
        logger.info(f"✅ Ruta base verificada: {BASE_DIR}")
        return True

validar_ruta_base()

# ========== FUNCIONES ==========

def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
    if not s:
        return ''
    try:
        if 'T' in s or len(s) > 10:
            dt = datetime.fromisoformat(s)
        else:
            d = datetime.strptime(s, '%Y-%m-%d')
            dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
        return dt.strftime('%Y%m%d%H%M%S')
    except Exception:
        return s

def obtener_servidores_activos() -> List[Dict]:
    """Lee servidores desde config.json"""
    servidores = CONFIG.get('servers', [])
    if not servidores:
        logger.warning("⚠️ No hay servidores en config.json")
        return []
    
    servidores_filtrados = []
    nombres_servidores = list(CORTE_A_SERVIDOR.values())
    for s in servidores:
        if s.get('name') in nombres_servidores:
            servidores_filtrados.append(s)
    return servidores_filtrados

def descargar_reporte(servidor_nombre: str, fecha: str) -> Optional[Dict]:
    """
    Descarga el reporte de un servidor usando la URL y token desde config.json
    """
    # Obtener el servidor desde config.json
    servidores = CONFIG.get('servers', [])
    srv = None
    for s in servidores:
        if s.get('name') == servidor_nombre:
            srv = s
            break
    
    if not srv:
        logger.error(f"❌ Servidor {servidor_nombre} no encontrado en config.json")
        return None

    # Construir URL
    url_base = srv.get('url', '').strip().rstrip('/')
    if url_base.lower().startswith('http'):
        base_url = url_base
    else:
        base_url = f"https://wv{url_base}.wolkvox.com"
    
    # Token: usar el específico o el general
    token = srv.get('token') or CONFIG.get('wolkvox-token')
    if not token:
        logger.error(f"❌ No hay token para {servidor_nombre}")
        return None

    date_ini_ts = _to_wolkvox_ts(fecha, is_end=False)
    date_end_ts = _to_wolkvox_ts(fecha, is_end=True)

    url = (
        f"{base_url}/api/v2/reports_manager.php"
        f"?api=cdr_1"
        f"&date_ini={date_ini_ts}"
        f"&date_end={date_end_ts}"
    )

    headers = {"wolkvox-token": token}
    
    logger.info(f"    📡 Descargando {servidor_nombre} para {fecha}")
    response = requests.get(url, headers=headers, timeout=60)

    if response.status_code != 200:
        logger.error(f"    ❌ Error {response.status_code}: {response.text[:200]}")
        return None

    # Parsear JSON
    try:
        data = response.json()
        if isinstance(data, dict):
            if data.get('error'):
                logger.error(f"    ❌ Error en API: {data.get('error')}")
                return None
            if 'data' in data and isinstance(data['data'], list):
                return data
            return data
        elif isinstance(data, list):
            return {'data': data}
        else:
            logger.error(f"    ❌ Formato de respuesta no reconocido")
            return None
    except json.JSONDecodeError as e:
        logger.error(f"    ❌ Error parseando JSON: {e}")
        return None

def convertir_json_a_xlsx(data: Dict, fecha: str, servidor_nombre: str) -> Optional[bytes]:
    """
    Convierte la respuesta JSON a formato XLSX con estilo
    """
    try:
        # Extraer los datos
        rows = []
        if isinstance(data, dict) and 'data' in data:
            rows = data['data']
        elif isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = [data]
        
        if not rows:
            logger.warning(f"    ⚠️ Sin datos para {servidor_nombre}")
            return None
        
        # Obtener todas las columnas posibles
        all_keys = set()
        for row in rows:
            if isinstance(row, dict):
                all_keys.update(row.keys())
        
        if not all_keys and rows and isinstance(rows[0], dict):
            all_keys = set(rows[0].keys())
        
        if not all_keys:
            all_keys = ['raw']
        
        columnas = sorted(list(all_keys))
        
        # Crear workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Reporte"
        
        # Estilos
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left = Alignment(horizontal="left", vertical="top", wrap_text=True)
        
        # Escribir cabecera
        ws.append(columnas)
        for col_idx in range(1, len(columnas) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center
        
        # Escribir datos
        for row in rows:
            if isinstance(row, dict):
                fila = [str(row.get(col, '')) for col in columnas]
            else:
                fila = [str(row)]
            ws.append(fila)
        
        # Ajustar anchos
        for col_idx in range(1, len(columnas) + 1):
            letter = get_column_letter(col_idx)
            max_len = 0
            for r in range(1, ws.max_row + 1):
                v = ws.cell(row=r, column=col_idx).value
                if v is None:
                    continue
                max_len = max(max_len, len(str(v)))
            width = min(45, max(10, max_len + 2))
            ws.column_dimensions[letter].width = width
        
        # Alinear datos a la izquierda
        for row_idx in range(2, ws.max_row + 1):
            for col_idx in range(1, len(columnas) + 1):
                ws.cell(row=row_idx, column=col_idx).alignment = left
        
        # Guardar en bytes
        output = io.BytesIO()  # <--- AHORA io está definido
        wb.save(output)
        output.seek(0)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"    ❌ Error convirtiendo a XLSX: {e}")
        return None

def guardar_reporte_xlsx(contenido_xlsx: bytes, servidor_nombre: str, fecha: str) -> bool:
    """
    Guarda el archivo XLSX en la carpeta de la fecha (sin subcarpetas de corte)
    """
    try:
        mes = fecha[:7]
        fecha_dir = os.path.join(BASE_DIR, mes, fecha)
        os.makedirs(fecha_dir, exist_ok=True)
        
        nombre_archivo = f"{servidor_nombre}_{fecha}.xlsx"
        ruta_completa = os.path.join(fecha_dir, nombre_archivo)
        
        with open(ruta_completa, "wb") as f:
            f.write(contenido_xlsx)
        
        logger.info(f"      ✅ Guardado: {nombre_archivo}")
        return True
        
    except Exception as e:
        logger.error(f"      ❌ Error guardando archivo: {e}")
        return False

def descargar_todos_los_reportes(fecha: str = None):
    """
    Descarga los reportes de todos los servidores para una fecha.
    Guarda todos los archivos XLSX en la misma carpeta.
    """
    # Usar lock para evitar ejecuciones simultáneas
    with _descarga_lock:
        if fecha is None:
            fecha = datetime.now().strftime("%Y-%m-%d")

        mes = fecha[:7]
        fecha_dir = os.path.join(BASE_DIR, mes, fecha)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 INICIANDO DESCARGA DE REPORTES")
        logger.info(f"📅 Fecha: {fecha}")
        logger.info(f"📁 Ruta: {fecha_dir}")
        logger.info(f"{'='*60}")

        if not os.path.exists(BASE_DIR):
            logger.error(f"❌ La ruta base {BASE_DIR} no existe")
            return

        servidores = obtener_servidores_activos()
        if not servidores:
            logger.warning("⚠️ No hay servidores activos configurados en config.json")
            return

        total_exitosos = 0
        total_fallidos = 0

        # Procesar cada servidor con delay para evitar 409
        for idx, servidor in enumerate(servidores):
            servidor_nombre = servidor.get('name')
            
            logger.info(f"\n📂 Procesando servidor: {servidor_nombre}")
            
            # 1. Descargar datos JSON
            data = descargar_reporte(servidor_nombre, fecha)
            
            if data is None:
                logger.warning(f"  ⚠️ No se pudo descargar {servidor_nombre}")
                total_fallidos += 1
                # Esperar igualmente antes del siguiente
                if idx < len(servidores) - 1:
                    time.sleep(3)
                continue
            
            # 2. Convertir a XLSX
            xlsx_content = convertir_json_a_xlsx(data, fecha, servidor_nombre)
            
            if xlsx_content is None:
                logger.warning(f"  ⚠️ No se pudo convertir a XLSX {servidor_nombre}")
                total_fallidos += 1
                if idx < len(servidores) - 1:
                    time.sleep(3)
                continue
            
            # 3. Guardar XLSX
            if guardar_reporte_xlsx(xlsx_content, servidor_nombre, fecha):
                total_exitosos += 1
            else:
                total_fallidos += 1
            
            # Esperar 3 segundos antes del siguiente servidor
            if idx < len(servidores) - 1:
                logger.info(f"    ⏳ Esperando 3 segundos antes del próximo servidor...")
                time.sleep(3)

        logger.info(f"\n{'='*60}")
        logger.info(f"📊 RESUMEN DE DESCARGA")
        logger.info(f"  ✅ Éxitos: {total_exitosos}")
        logger.info(f"  ❌ Fallos: {total_fallidos}")
        logger.info(f"  📂 Carpeta: {fecha_dir}")
        logger.info(f"{'='*60}\n")

def descargar_y_sobrescribir():
    try:
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"🕐 EJECUTANDO DESCARGA PROGRAMADA - {datetime.now().strftime('%H:%M:%S')}")
        descargar_todos_los_reportes(fecha_hoy)
    except Exception as e:
        logger.error(f"❌ Error en ejecución programada: {e}")

# ========== FUNCIONES EXPORTADAS ==========

def iniciar_scheduler():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        logger.info("⚠️ El scheduler ya está en ejecución")
        return False

    if not os.path.exists(BASE_DIR):
        logger.error(f"❌ La ruta base {BASE_DIR} no existe")
        return False

    servidores = obtener_servidores_activos()
    if not servidores:
        logger.warning("⚠️ No hay servidores activos configurados en config.json")
        return False

    def run_scheduler():
        global _scheduler_running
        _scheduler_running = True

        logger.info("="*60)
        logger.info("🚀 INICIANDO SISTEMA DE DESCARGAS PROGRAMADAS")
        logger.info("="*60)
        logger.info(f"📁 Ruta base: {BASE_DIR}")
        logger.info(f"📋 Servidores configurados: {len(servidores)}")
        for s in servidores:
            logger.info(f"   - {s.get('name')}")

        for horario in HORARIOS_EJECUCION:
            schedule.every().day.at(horario).do(descargar_y_sobrescribir)
            logger.info(f"  📅 Descarga programada a las {horario}")

        logger.info(f"\n🔄 Scheduler iniciado. Próximas ejecuciones:")
        for horario in HORARIOS_EJECUCION:
            logger.info(f"  ⏰ {horario}")

        while _scheduler_running:
            schedule.run_pending()
            time.sleep(30)

        logger.info("🛑 Scheduler detenido")

    _scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    _scheduler_thread.start()
    logger.info("✅ Scheduler iniciado en segundo plano")
    return True

def detener_scheduler():
    global _scheduler_running
    if _scheduler_running:
        _scheduler_running = False
        logger.info("🛑 Deteniendo scheduler...")
        return True
    return False

def estado_scheduler() -> Dict:
    servidores = obtener_servidores_activos()
    return {
        "running": _scheduler_running,
        "horarios": HORARIOS_EJECUCION,
        "servidores": len(servidores),
        "servidores_lista": [s.get('name') for s in servidores],
        "base_dir": BASE_DIR,
        "corte_mapeo": CORTE_A_SERVIDOR
    }

def init_auto_download():
    return iniciar_scheduler()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_auto_download()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        detener_scheduler()
        logger.info("Script finalizado")