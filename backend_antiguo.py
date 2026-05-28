import csv
import json
import logging
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask
from database import db, init_db, ScheduledCSV, APIEndpoint, ScheduledQuery
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from bigquery import escribir_resultados_campana
from conexion_bigquery import client
# - ScheduledCSV: Modelo para tareas CSV programadas
# - APIEndpoint: Modelo para endpoints API configurables
# - upload_csv_to_api(): Carga CSV a API
# - consume_api(): Consume endpoints API
# - execute_scheduled_query(): Ejecuta queries programadas
# - execute_pending_tasks(): Ejecuta todas las tareas programadas
# - list_saved_csv_files(): Lista archivos CSV
# - save_csv_file(): Guarda archivos CSV
# - allowed_file(): Valida extensiones de archivos
# - read_csv_metadata(): Lee metadata de CSV
# - rotate_log_if_needed(): Rotación de logs

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
DOWNLOAD_FOLDER = BASE_DIR / "downloads"
LOG_FILE = BASE_DIR / "execution_log.txt"
CONFIG_FILE = BASE_DIR / "config.json"
ALLOWED_EXTENSIONS = {"csv"}

CONFIG = {}


def load_config():
    global CONFIG
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            CONFIG = json.load(f)
            logger.info(f"Configuración cargada desde {CONFIG_FILE}")
    except FileNotFoundError:
        CONFIG = {}
        logger.warning(f"No se encontró {CONFIG_FILE}. Usando configuración vacía.")
    except Exception as e:
        CONFIG = {}
        logger.error(f"Error cargando {CONFIG_FILE}: {e}")
    return CONFIG


def get_authorization_headers():
    """Obtener headers de autorización con el token"""
    headers = {}
    token = CONFIG.get("wolkvox-token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["wolkvox-token"] = token
    return headers


# Configurar logging
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-key"
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["DOWNLOAD_FOLDER"] = str(DOWNLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'app.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

init_db(app)
scheduler = BackgroundScheduler()
scheduler.start()

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
load_config()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def read_csv_metadata(filepath: Path) -> dict:
    metadata = {"rows": 0, "columns": 0}
    try:
        with filepath.open(newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            for index, row in enumerate(reader, start=1):
                metadata["rows"] = index
                if index == 1:
                    metadata["columns"] = len(row)
    except Exception:
        metadata = {"rows": 0, "columns": 0}
    return metadata


def save_csv_file(uploaded_file) -> dict:
    filename = secure_filename(uploaded_file.filename)
    if not filename or not allowed_file(filename):
        raise ValueError("Solo se permiten archivos CSV con nombre válido.")

    destination = UPLOAD_FOLDER / filename
    uploaded_file.save(destination)
    metadata = read_csv_metadata(destination)
    return {
        "filename": filename,
        "path": str(destination),
        "rows": metadata["rows"],
        "columns": metadata["columns"],
    }


def list_saved_csv_files() -> list:
    files = []
    for entry in sorted(UPLOAD_FOLDER.iterdir()):
        if entry.is_file() and allowed_file(entry.name):
            metadata = read_csv_metadata(entry)
            files.append({
                "name": entry.name,
                "rows": metadata["rows"],
                "columns": metadata["columns"],
                "download_url": url_for("download_file", filename=entry.name),
            })
    return files


def rotate_log_if_needed():
    if LOG_FILE.exists():
        with LOG_FILE.open("r") as f:
            lines = f.readlines()
        if len(lines) >= 3000:
            backup_file = BASE_DIR / f"execution_log_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            LOG_FILE.rename(backup_file)
            logger.info(f"Log rotated to {backup_file}")


def upload_csv_to_api(filename: str, api_url: str):
    filepath = UPLOAD_FOLDER / filename
    if not filepath.exists():
        logger.error(f"Archivo {filename} no encontrado.")
        return
    try:
        logger.info(f"Iniciando carga de archivo {filename} a API {api_url}")
        headers = get_authorization_headers()
        with filepath.open("rb") as f:
            files = {"file": f}
            response = requests.post(api_url, files=files, headers=headers, params=get_wolkvox_params())
            if response.status_code == 200:
                logger.info(f"Carga de {filename} a {api_url}: Éxito")
            else:
                logger.warning(f"Carga de {filename} a {api_url}: Error {response.status_code}")
    except Exception as e:
        logger.error(f"Error cargando {filename} a {api_url}: {e}")


def consume_api(endpoint_id: int):
    endpoint = APIEndpoint.query.get(endpoint_id)
    if not endpoint:
        logger.error(f"Endpoint {endpoint_id} no encontrado.")
        return
    try:
        logger.info(f"Iniciando consumo de API {endpoint.url} (id={endpoint_id})")
        headers = get_authorization_headers()
        response = requests.get(endpoint.url, headers=headers, params=get_wolkvox_params())
        if response.status_code == 200:
            logger.info(f"Consumiendo {endpoint.url}: Éxito")
        else:
            logger.warning(f"Consumiendo {endpoint.url}: Error {response.status_code}")
    except Exception as e:
        logger.error(f"Error consumiendo {endpoint.url}: {e}")


def execute_scheduled_query(query_id: int):
    query = ScheduledQuery.query.get(query_id)
    if not query:
        logger.error(f"Query {query_id} no encontrada.")
        return
    try:
        logger.info(f"Iniciando ejecución de consulta a API {query.api_url} (query_id={query_id})")
        headers = get_authorization_headers()
        response = requests.get(query.api_url, headers=headers, params=get_wolkvox_params())
        if response.status_code == 200:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"query_{query_id}_{timestamp}.json"
            filepath = DOWNLOAD_FOLDER / filename
            with filepath.open("w", encoding="utf-8") as f:
                f.write(response.text)
            query.last_run = datetime.utcnow()
            query.last_status = "EXEC"
            query.last_error = None
            query.last_execution_time = datetime.utcnow()
            db.session.commit()
            logger.info(f"[EXEC] Query a {query.api_url}: Éxito, guardado en {filename}")
        else:
            error_msg = f"HTTP {response.status_code}"
            query.last_status = "FAILED"
            query.last_error = error_msg
            query.last_execution_time = datetime.utcnow()
            db.session.commit()
            logger.warning(f"[EXEC] Query a {query.api_url}: Error {response.status_code}")
    except Exception as e:
        error_msg = str(e)
        query.last_status = "FAILED"
        query.last_error = error_msg
        query.last_execution_time = datetime.utcnow()
        db.session.commit()
        logger.error(f"[EXEC] Error ejecutando query a {query.api_url}: {e}")


def convert_json_to_csv(data, filename):
    """Convierte datos JSON a CSV y los guarda en la carpeta downloads"""
    try:
        import io
        
        # Si la respuesta tiene un campo 'data', usar ese
        if isinstance(data, dict) and 'data' in data:
            rows = data['data']
        else:
            rows = data
        
        # Si es un string que parece ser JSON, intentar parsearlo
        if isinstance(rows, str):
            import json
            try:
                rows = json.loads(rows)
            except:
                return None
        
        # Convertir a lista si es un objeto
        if isinstance(rows, dict):
            rows = [rows]
        elif not isinstance(rows, list):
            return None
        
        if not rows or len(rows) == 0:
            return None
        
        # Obtener TODOS los headers de TODOS los registros (no solo el primero)
        if isinstance(rows[0], dict):
            headers = set()
            for row in rows:
                if isinstance(row, dict):
                    headers.update(row.keys())
            headers = sorted(list(headers))  # Ordenar para consistencia
            
            filepath = DOWNLOAD_FOLDER / filename
            
            with filepath.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, restval='', extrasaction='ignore')
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            
            # Escribir también en BigQuery
            if client is not None:
                result = escribir_resultados_campana(client, rows)
                if result['success']:
                    logger.info(f"Datos también guardados en BigQuery: {result['message']}")
                else:
                    logger.error(f"Error guardando en BigQuery: {result['message']}")
            else:
                logger.warning("Cliente BigQuery no disponible, no se guardaron datos en BigQuery")
            
            logger.info(f"CSV generado: {filename} ({len(rows)} registros, {len(headers)} campos)")
            return str(filepath)
        
        return None
    except Exception as e:
        logger.error(f"Error convirtiendo JSON a CSV: {e}")
        return None


def execute_pending_tasks():
    with app.app_context():
        rotate_log_if_needed()
        now = datetime.utcnow()
        logger.info("=== Iniciando execute_pending_tasks ===")

        pending_csvs = ScheduledCSV.query.filter(ScheduledCSV.status == "pending", ScheduledCSV.scheduled_time <= now).all()
        logger.info(f"CSVs pendientes encontrados: {len(pending_csvs)}")
        for csv_task in pending_csvs:
            logger.info(f"Ejecutando CSV task: {csv_task.filename}")
            upload_csv_to_api(csv_task.filename, csv_task.api_url)
            csv_task.status = "executed"
            db.session.commit()

        endpoints = APIEndpoint.query.all()
        logger.info(f"Endpoints encontrados: {len(endpoints)}")
        for endpoint in endpoints:
            logger.info(f"Consumiendo API endpoint: {endpoint.name} ({endpoint.url})")
            consume_api(endpoint.id)

        queries = ScheduledQuery.query.all()
        logger.info(f"Queries programadas encontradas: {len(queries)}")
        for query in queries:
            logger.info(f"Evaluando query: {query.api_url}, last_run={query.last_run}, frequency={query.frequency_minutes} min")
            if not query.last_run:
                logger.info(f"Query nunca ejecutada. Ejecutando ahora: {query.api_url}")
                execute_scheduled_query(query.id)
            else:
                time_diff = (now - query.last_run).total_seconds()
                required_time = query.frequency_minutes * 60
                logger.info(f"Tiempo desde last_run: {time_diff}s, requerido: {required_time}s")
                if time_diff >= required_time:
                    logger.info(f"Tiempo suficiente. Ejecutando: {query.api_url}")
                    execute_scheduled_query(query.id)
                else:
                    logger.info(f"Tiempo insuficiente. Saltando: {query.api_url}")
