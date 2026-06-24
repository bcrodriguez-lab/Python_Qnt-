#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ejecuta el pipeline completo de descarga y procesamiento.
Este script asegura que bigquery_processor.py se ejecute solo una vez
después de que ambas descargas (CDR y AMD) estén completas.
"""

import logging
import sys
import os
from datetime import datetime
import subprocess

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Importar módulos de descarga
from download_auto import descargar_todos_los_reportes
from download_campaign_detail import descargar_todos_los_reportes_amd
from config import DESCARGAR_CDR, DESCARGAR_AMD


def ejecutar_pipeline_completo(fecha: str = None):
    """
    Ejecuta el pipeline completo:
    1. Descarga CDR (si está activado)
    2. Descarga AMD (si está activado)
    3. Ejecuta bigquery_processor.py (una sola vez)
    """
    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")
    
    logger.info("="*60)
    logger.info(f"🚀 INICIANDO PIPELINE COMPLETO PARA {fecha}")
    logger.info("="*60)
    
    # 1. Descargar CDR
    if DESCARGAR_CDR:
        logger.info("\n📥 PASO 1/3: Descargando CDR...")
        try:
            descargar_todos_los_reportes(fecha)
            logger.info("✅ CDR descargado correctamente")
        except Exception as e:
            logger.error(f"❌ Error descargando CDR: {e}")
    else:
        logger.info("\n⏭️ CDR desactivado en configuración")
    
    # 2. Descargar AMD
    if DESCARGAR_AMD:
        logger.info("\n📥 PASO 2/3: Descargando AMD...")
        try:
            descargar_todos_los_reportes_amd(fecha)
            logger.info("✅ AMD descargado correctamente")
        except Exception as e:
            logger.error(f"❌ Error descargando AMD: {e}")
    else:
        logger.info("\n⏭️ AMD desactivado en configuración")
    
    # 3. Ejecutar bigquery_processor.py (una sola vez)
    logger.info("\n🔄 PASO 3/3: Procesando y subiendo a BigQuery...")
    try:
        # Verificar que el archivo existe
        if not os.path.exists("bigquery_processor.py"):
            logger.error("❌ No se encontró bigquery_processor.py")
            return False
        
        resultado = subprocess.run(
            [sys.executable, "bigquery_processor.py", fecha],
            capture_output=False,
            text=True,
            timeout=3600  # 1 hora máximo
        )
        
        if resultado.returncode == 0:
            logger.info(f"✅ bigquery_processor.py completado exitosamente para {fecha}")
        else:
            logger.error(f"❌ bigquery_processor.py falló con código {resultado.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"❌ bigquery_processor.py excedió el tiempo límite (1 hora)")
        return False
    except Exception as e:
        logger.error(f"❌ Error ejecutando bigquery_processor.py: {e}")
        return False
    
    logger.info("="*60)
    logger.info(f"🏁 PIPELINE COMPLETADO EXITOSAMENTE PARA {fecha}")
    logger.info("="*60)
    return True


def ejecutar_pipeline_hoy():
    """Ejecuta el pipeline para hoy (usado por el scheduler)"""
    fecha = datetime.now().strftime("%Y-%m-%d")
    return ejecutar_pipeline_completo(fecha)


if __name__ == "__main__":
    fecha = sys.argv[1] if len(sys.argv) > 1 else None
    exito = ejecutar_pipeline_completo(fecha)
    sys.exit(0 if exito else 1)