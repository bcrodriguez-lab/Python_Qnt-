#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Archivo central de configuración para descargas automáticas.
Todas las configuraciones se manejan desde aquí.
"""
from  datetime  import datetime
import json

# ============================================================
# CONFIGURACIÓN DE DESCARGA 
# ============================================================

# === MODO DE DESCARGA ===
# Opciones: "hoy", "rango", "fecha_especifica"
MODO_DESCARGA = "rango"  

# === CONFIGURACIÓN PARA MODO "rango" ===
FECHA_INICIO_RANGO = "2026-06-19"  # Fecha inicio (YYYY-MM-DD)
FECHA_FIN_RANGO = datetime.now().strftime("%Y-%m-%d")  # Siempre hasta hoy

# === CONFIGURACIÓN PARA MODO "fecha_especifica" ===
#FECHA_ESPECIFICA = "2026-06-18"    # Fecha específica (YYYY-MM-DD)

# === SERVICIOS A EJECUTAR ===
# Activar/desactivar descargas individuales
DESCARGAR_CDR = True   # Reportes de llamadas (download_auto.py)
DESCARGAR_AMD = True   # Reportes de campañas (download_campaign_detail.py)

# === HORARIOS DE EJECUCIÓN ===
HORARIOS_EJECUCION = [
    "08:16", "08:34","08:51", "09:14","09:26", "10:00", "10:38",
    "11:00", "11:33", "12:00", "12:29", "14:00",
    "16:14", "16:25"
]

# ============================================================
# NO MODIFICAR DEBAJO DE ESTA LÍNEA (es para los scripts)
# ============================================================

def cargar_config_json():
    """Carga configuración desde config.json (tokens, servidores, etc.)"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error cargando config.json: {e}")
        return {}

CONFIG_JSON = cargar_config_json()
BASE_DIR = CONFIG_JSON.get('base_dir', r"G:\Unidades compartidas\Analitica\Embudo de Conversión\Proyecto Robot Omnicanal\Producción\2026-06\Resultados")

def obtener_fechas_descarga():
    """
    Retorna una lista de fechas a descargar según el modo configurado.
    """
    from datetime import datetime, timedelta
    
    fechas = []
    
    if MODO_DESCARGA == "hoy":
        fecha = datetime.now().strftime("%Y-%m-%d")
        fechas = [fecha]
        
    elif MODO_DESCARGA == "rango":
        start = datetime.strptime(FECHA_INICIO_RANGO, "%Y-%m-%d")
        end = datetime.strptime(FECHA_FIN_RANGO, "%Y-%m-%d")
        current = start
        while current <= end:
            fechas.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
            
    elif MODO_DESCARGA == "fecha_especifica":
        fechas = [FECHA_ESPECIFICA]
        
    else:
        # Por defecto, descargar hoy
        fechas = [datetime.now().strftime("%Y-%m-%d")]
    
    return fechas

def obtener_servidores():
    """Obtiene la lista de servidores desde config.json"""
    return CONFIG_JSON.get('servers', [])

def obtener_token(servidor_nombre=None):
    """Obtiene el token para un servidor específico o el general"""
    if servidor_nombre:
        for s in CONFIG_JSON.get('servers', []):
            if s.get('name') == servidor_nombre:
                return s.get('token')
    return CONFIG_JSON.get('wolkvox-token', '')

def obtener_url_base(servidor_nombre):
    """Obtiene la URL base para un servidor"""
    for s in CONFIG_JSON.get('servers', []):
        if s.get('name') == servidor_nombre:
            url = s.get('url', '').strip().rstrip('/')
            if url:
                return url
    return servidor_nombre