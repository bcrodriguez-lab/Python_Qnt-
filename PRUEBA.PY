import logging
import os

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "prueba.log"
)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True
)

logger = logging.getLogger(__name__)

print("Directorio actual:", os.getcwd())
print("Ruta del log:", LOG_FILE)

logger.info("🔥 PRUEBA DE LOG")
logger.info("El archivo debería haberse creado")

print("¿Existe el archivo?", os.path.exists(LOG_FILE))