import csv
import json
import logging
import shutil
import threading
from collections import deque
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask
from database import db, init_db, Campaign, ScheduledCSV, APIEndpoint, ScheduledQuery
from auto_campaigns import check_auto_campaigns_schedule
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from bigquery import count_query_results, escribir_resultados_campana
from conexion_bigquery import client, get_bigquery_client
from general_params import (
    get_campaign_check_interval_seconds,
    get_console_message_interval_seconds,
    get_log_retention_hours,
)
from servers import get_server
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
CAMPAIGN_SCHEDULER_JOB_ID = "execute_pending_tasks"
CONSOLE_MESSAGE_JOB_ID = "console_message_heartbeat"

CONFIG = {}


def load_config():
    global CONFIG
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            CONFIG = json.load(f)
            logger.debug(f"Configuración cargada desde {CONFIG_FILE}")
    except FileNotFoundError:
        CONFIG = {}
        logger.warning(f"No se encontró {CONFIG_FILE}. Usando configuración vacía.")
    except Exception as e:
        CONFIG = {}
        logger.error(f"Error cargando {CONFIG_FILE}: {e}")
    return CONFIG


def get_authorization_headers(server_name: str | None = None):
    """Obtener headers de autorización con el token.

    Si se pasa `server_name`, intenta usar el token configurado para ese
    servidor en `config.json`. Si no existe, cae al token global
    `wolkvox-token`.
    """
    headers: dict[str, str] = {}
    token = ""

    if server_name:
        try:
            srv = get_server(server_name)
            if srv:
                t = srv.get("token")
                if t is not None and str(t).strip():
                    token = str(t).strip()
        except Exception:
            token = ""

    if not token:
        token = CONFIG.get("wolkvox-token") or ""

    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["wolkvox-token"] = token

    return headers


# Logging: tablero (memoria) = actividad en vivo; archivo = solo ejecución de tareas.
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_LINES = 3000
ACTIVITY_LOG_MAX_LINES = 300
_log_io_lock = threading.Lock()
_activity_log: deque[str] = deque(maxlen=ACTIVITY_LOG_MAX_LINES)
_activity_lock = threading.Lock()


def _configure_logging() -> None:
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    execution_logger = logging.getLogger("execution")
    execution_logger.setLevel(logging.INFO)
    execution_logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) for h in execution_logger.handlers):
        file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        file_handler.setFormatter(formatter)
        execution_logger.addHandler(file_handler)

    for noisy in ("apscheduler", "apscheduler.scheduler", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)
execution_logger = logging.getLogger("execution")


def _format_log_line(level: str, message: str) -> str:
    ts = datetime.now().strftime(LOG_DATEFMT)
    return f"{ts} - {level.upper()} - {message}"


def log_activity(message: str, level: str = "INFO") -> None:
    """Visible en el tablero (/api/recent_logs); no escribe en disco."""
    line = _format_log_line(level, message)
    with _activity_lock:
        _activity_log.append(line)


def log_to_file(message: str, level: str = "INFO") -> None:
    """Persiste en execution_log.txt y refleja en el tablero web."""
    log_fn = getattr(execution_logger, level.lower(), execution_logger.info)
    log_fn(message)
    log_activity(message, level)


def log_task(message: str, level: str = "INFO") -> None:
    """Ejecución real de tarea: archivo + tablero (una línea por mensaje)."""
    log_to_file(message, level)


def log_gui_action(action: str, **fields) -> None:
    """Acción desde la interfaz: solo tablero (no archivo)."""
    if fields:
        detail = ", ".join(f"{key}={value}" for key, value in fields.items())
        log_activity(f"[GUI] {action} — {detail}")
    else:
        log_activity(f"[GUI] {action}")


def _execution_log_file_handlers():
    """Handlers que escriben en execution_log.txt."""
    log_path = LOG_FILE.resolve()
    for handler in execution_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if Path(handler.baseFilename).resolve() == log_path:
                    yield handler
            except (TypeError, ValueError, OSError):
                continue


def _close_execution_log_handlers():
    for handler in list(_execution_log_file_handlers()):
        handler.close()
        execution_logger.removeHandler(handler)


def _attach_execution_log_handler():
    if any(True for _ in _execution_log_file_handlers()):
        return
    handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    execution_logger.addHandler(handler)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
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


def reschedule_campaign_check_job(seconds: int | None = None) -> int:
    """
    Reprograma el job de revisión de campañas sin reiniciar la aplicación.
    Retorna el intervalo en segundos aplicado.
    """
    interval = int(seconds) if seconds is not None else get_campaign_check_interval_seconds()
    scheduler.reschedule_job(
        CAMPAIGN_SCHEDULER_JOB_ID,
        trigger=IntervalTrigger(seconds=interval),
    )
    logger.info(f"Scheduler de campañas reprogramado: cada {interval} segundos")
    return interval


def reschedule_console_message_job(seconds: int | None = None) -> int:
    """
    Reprograma el mensaje en consola (job independiente de execute_pending_tasks).
    Por defecto cada 60 segundos (1 minuto).
    """
    interval = (
        int(seconds)
        if seconds is not None
        else get_console_message_interval_seconds()
    )
    scheduler.reschedule_job(
        CONSOLE_MESSAGE_JOB_ID,
        trigger=IntervalTrigger(seconds=interval),
    )
    logger.info(f"Mensaje consola reprogramado: cada {interval} segundos")
    return interval


def get_wolkvox_params() -> dict:
    """Parametros extra para peticiones Wolkvox (ajustar segun integracion)."""
    return {}


def upload_csv_to_api(filename: str, api_url: str):
    """Carga un CSV de uploads/ a la API indicada."""
    # filepath = UPLOAD_FOLDER / filename
    # if not filepath.exists():
    #     logger.error(f"Archivo {filename} no encontrado.")
    #     return
    # try:
    #     logger.info(f"Iniciando carga de archivo {filename} a API {api_url}")
    #     headers = get_authorization_headers()
    #     with filepath.open("rb") as f:
    #         files = {"file": f}
    #         response = requests.post(
    #             api_url, files=files, headers=headers, params=get_wolkvox_params()
    #         )
    #         if response.status_code == 200:
    #             logger.info(f"Carga de {filename} a {api_url}: Éxito")
    #         else:
    #             logger.warning(f"Carga de {filename} a {api_url}: Error {response.status_code}")
    # except Exception as e:
    #     logger.error(f"Error cargando {filename} a {api_url}: {e}")
    pass


def consume_api(endpoint_id: int):
    """Consume un endpoint registrado en APIEndpoint."""
    # endpoint = APIEndpoint.query.get(endpoint_id)
    # if not endpoint:
    #     logger.error(f"Endpoint {endpoint_id} no encontrado.")
    #     return
    # try:
    #     logger.info(f"Iniciando consumo de API {endpoint.url} (id={endpoint_id})")
    #     headers = get_authorization_headers()
    #     response = requests.get(
    #         endpoint.url, headers=headers, params=get_wolkvox_params()
    #     )
    #     if response.status_code == 200:
    #         logger.info(f"Consumiendo {endpoint.url}: Éxito")
    #     else:
    #         logger.warning(f"Consumiendo {endpoint.url}: Error {response.status_code}")
    # except Exception as e:
    #     logger.error(f"Error consumiendo {endpoint.url}: {e}")
    pass


def _today_range(now: datetime) -> tuple[datetime, datetime]:
    """Inicio y fin del dia calendario de now (hora local)."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def campaign_to_log_dict(campaign: Campaign) -> dict:
    """Todos los campos de la campaña para el log de ejecución."""
    fecha = campaign.fecha_inicio
    return {
        "id": campaign.id,
        "nombre": campaign.nombre,
        "descripcion": campaign.descripcion or "",
        "operacion": campaign.operacion,
        "tipo": campaign.tipo,
        "tipo_campana": campaign.tipo_campana or "",
        "flujo_proceso_id": campaign.flujo_proceso_id or "",
        "usuario": campaign.usuario,
        "servidor": campaign.servidor or "",
        "api": campaign.api or "",
        "fecha_inicio": fecha.strftime("%Y-%m-%d %H:%M:%S") if fecha else "",
        "consulta": campaign.consulta or "",
        "total_clientes": int(campaign.total_clientes or 0),
        "clientes_llamados": int(campaign.clientes_llamados or 0),
        "clientes_contactados": int(campaign.clientes_contactados or 0),
        "ejecutada": bool(campaign.ejecutada),
    }


def get_client_count_from_query(campaign: Campaign) -> tuple[int | None, str | None]:
    """
    Obtiene cuantos clientes debe procesar la campaña segun su consulta BigQuery.
    Retorna (total, mensaje_error).
    """
    consulta = (campaign.consulta or "").strip()
    if not consulta:
        return None, "La campaña no tiene consulta definida."

    bq_client = get_bigquery_client()
    if bq_client is None:
        return None, "Cliente BigQuery no disponible."

    result = count_query_results(bq_client, consulta)
    if result.get("success"):
        return int(result.get("total", 0)), None
    return None, result.get("message", "Error al contar clientes.")


def _compact_log_value(key: str, value) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if key == "consulta" and len(text) > 100:
        return text[:100] + "…"
    return text


def format_campaign_execution_line(
    campaign: Campaign,
    *,
    prefix: str = "EJECUCION",
    clientes_query: int | None = None,
    nota: str = "",
) -> str:
    """Una sola línea con todos los campos de la campaña."""
    data = campaign_to_log_dict(campaign)
    if clientes_query is not None:
        data["clientes_a_ejecutar_query"] = clientes_query
    if nota:
        data["estado"] = nota
    fields = ", ".join(
        f"{key}={_compact_log_value(key, value)}" for key, value in data.items()
    )
    tag = f"[{prefix}]" if prefix else ""
    return f"{tag} {fields}"


def log_campaign_execution_line(
    campaign: Campaign,
    *,
    prefix: str = "EJECUCION",
    clientes_query: int | None = None,
    nota: str = "",
    level: str = "INFO",
) -> None:
    """Registra campaña en una línea: terminal, archivo y tablero web."""
    line = format_campaign_execution_line(
        campaign,
        prefix=prefix,
        clientes_query=clientes_query,
        nota=nota,
    )
    print(line, flush=True)
    log_task(line, level=level)


def get_campaigns_scheduled_for_today(now: datetime) -> list[Campaign]:
    """Campañas programadas para el dia calendario actual (cualquier hora)."""
    day_start, day_end = _today_range(now)
    return (
        Campaign.query.filter(
            Campaign.fecha_inicio.isnot(None),
            Campaign.fecha_inicio >= day_start,
            Campaign.fecha_inicio <= day_end,
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )


def get_past_unexecuted_campaigns(now: datetime) -> list[Campaign]:
    """Campañas de dias anteriores que no se ejecutaron (no se volveran a ejecutar)."""
    day_start, _ = _today_range(now)
    return (
        Campaign.query.filter(
            Campaign.fecha_inicio.isnot(None),
            Campaign.fecha_inicio < day_start,
            Campaign.ejecutada.is_(False),
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )


def run_campaign_api_consumption(campaign: Campaign) -> bool:
    """
    Inicia la secuencia Wolkvox definida en campaign_execution.get_campaign_execution_rules().
    Retorna True si se lanzó el workflow en segundo plano.
    """
    from campaign_execution import requires_wolkvox_execution, start_campaign_wolkvox_workflow_async

    load_config()
    if not requires_wolkvox_execution(campaign):
        log_task(
            f"[API] Campaña id={campaign.id} ({campaign.nombre}): "
            f"sin flujo Wolkvox; no se invocan APIs"
        )
        return False

    started = start_campaign_wolkvox_workflow_async(campaign.id, app)
    if started:
        log_task(
            f"[API] Campaña id={campaign.id} ({campaign.nombre}) "
            f"servidor={campaign.servidor or '-'} flujo={campaign.flujo_proceso_id}: "
            f"secuencia Wolkvox iniciada en segundo plano"
        )
    else:
        log_task(
            f"[API] Campaña id={campaign.id}: secuencia Wolkvox ya en ejecución",
            level="WARNING",
        )
    return started


def execute_campaign(campaign: Campaign):
    """Ejecuta una campaña del dia: conteo BigQuery y secuencia de APIs Wolkvox."""
    clientes_query, count_error = get_client_count_from_query(campaign)
    level = "WARNING" if count_error else "INFO"
    if not count_error and clientes_query is not None:
        campaign.total_clientes = clientes_query
        db.session.commit()

    run_campaign_api_consumption(campaign)
    campaign.ejecutada = True
    db.session.commit()

    nota = "ejecutada"
    if count_error:
        nota += f"; error_conteo={count_error}"
    from campaign_execution import requires_wolkvox_execution

    if requires_wolkvox_execution(campaign):
        nota += "; secuencia_wolkvox=en_curso"
    log_campaign_execution_line(
        campaign,
        clientes_query=clientes_query,
        nota=nota,
        level=level,
    )


def get_campaigns_due_for_execution(now: datetime) -> list[Campaign]:
    """
    Campañas de HOY cuya hora ya llegó y aún no se ejecutaron.
    No incluye campañas de días anteriores (fecha ya pasó).
    """
    day_start, day_end = _today_range(now)
    return (
        Campaign.query.filter(
            Campaign.fecha_inicio.isnot(None),
            Campaign.fecha_inicio >= day_start,
            Campaign.fecha_inicio <= day_end,
            Campaign.fecha_inicio <= now,
            Campaign.ejecutada.is_(False),
        )
        .order_by(Campaign.fecha_inicio.asc())
        .all()
    )


'''
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
'''

'''def read_csv_metadata(filepath: Path) -> dict:
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
'''


LOG_BACKUP_GLOB = "execution_log_backup_*.txt"


def cleanup_old_log_files() -> list[str]:
    """
    Elimina archivos de log rotados más antiguos que log_retention_hours (config.json).
    No borra execution_log.txt activo.
    """
    retention_hours = get_log_retention_hours()
    cutoff_ts = datetime.now().timestamp() - (retention_hours * 3600)
    deleted: list[str] = []

    for path in BASE_DIR.glob(LOG_BACKUP_GLOB):
        try:
            if path.resolve() == LOG_FILE.resolve():
                continue
            if path.stat().st_mtime < cutoff_ts:
                path.unlink()
                deleted.append(path.name)
        except OSError as e:
            logger.warning(f"No se pudo eliminar log antiguo {path.name}: {e}")

    if deleted:
        log_activity(
            f"Logs de respaldo eliminados (retención {retention_hours} h): {', '.join(deleted)}"
        )
    return deleted


def read_recent_log_lines(limit: int = 20) -> list[str]:
    """Últimas líneas de actividad para el tablero (memoria, no el archivo en disco)."""
    with _activity_lock:
        if not _activity_log:
            return []
        return list(_activity_log)[-limit:]


def rotate_log_if_needed():
    """Rota execution_log.txt al superar LOG_MAX_LINES (cierra el handler en Windows)."""
    with _log_io_lock:
        if not LOG_FILE.exists():
            return
        try:
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except OSError as e:
            logger.warning(f"No se pudo leer el log para rotación: {e}")
            return

        if line_count < LOG_MAX_LINES:
            return

        backup_file = BASE_DIR / (
            f"execution_log_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        _close_execution_log_handlers()
        try:
            # En Windows rename falla si el archivo está abierto; copiar y borrar es más fiable.
            shutil.copy2(LOG_FILE, backup_file)
            LOG_FILE.unlink(missing_ok=True)
            _attach_execution_log_handler()
            log_to_file(f"Archivo de log rotado: {backup_file.name}")
            cleanup_old_log_files()
        except OSError as e:
            _attach_execution_log_handler()
            logger.error(f"No se pudo rotar el log: {e}")

def execute_scheduled_query(query_id: int):
    """
    Ejecuta una consulta HTTP programada (modelo ScheduledQuery).

    Hace GET a query.api_url con token Wolkvox, guarda la respuesta en
    downloads/ y actualiza en BD el estado de la ultima ejecucion.
    La invoca execute_pending_tasks cuando toca por frecuencia o es la primera vez.
    """
    # Cargar registro de la query; si fue borrada, no hay nada que ejecutar.
    query = ScheduledQuery.query.get(query_id)
    if not query:
        log_task(f"Query {query_id} no encontrada.", level="ERROR")
        return
    try:
        log_task(f"Iniciando ejecución de consulta a API {query.api_url} (query_id={query_id})")
        # Token Bearer / wolkvox-token desde config.json.
        headers = get_authorization_headers()
        # GET a la URL configurada; params extras de Wolkvox (si aplica).
        response = requests.get(query.api_url, headers=headers, params=get_wolkvox_params())
        if response.status_code == 200:
            # Respuesta OK: persistir JSON en downloads/ con nombre unico por timestamp.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"query_{query_id}_{timestamp}.json"
            filepath = DOWNLOAD_FOLDER / filename
            with filepath.open("w", encoding="utf-8") as f:
                f.write(response.text)
            # Marcar ejecucion exitosa; last_run alimenta el control de frecuencia.
            query.last_run = datetime.utcnow()
            query.last_status = "EXEC"
            query.last_error = None
            query.last_execution_time = datetime.utcnow()
            db.session.commit()
            log_task(f"[EXEC] Query a {query.api_url}: Éxito, guardado en {filename}")
        else:
            # HTTP distinto de 200: registrar fallo sin guardar archivo.
            error_msg = f"HTTP {response.status_code}"
            query.last_status = "FAILED"
            query.last_error = error_msg
            query.last_execution_time = datetime.utcnow()
            db.session.commit()
            log_task(f"[EXEC] Query a {query.api_url}: Error {response.status_code}", level="WARNING")
    except Exception as e:
        # Red, timeout, JSON invalido, etc.: mismo esquema de estado FAILED.
        error_msg = str(e)
        query.last_status = "FAILED"
        query.last_error = error_msg
        query.last_execution_time = datetime.utcnow()
        db.session.commit()
        log_task(f"[EXEC] Error ejecutando query a {query.api_url}: {e}", level="ERROR")




def console_message_heartbeat():
    """
    Mensaje fijo en consola cada N segundos (config: console_message_interval_seconds).
    No ejecuta campañas; es independiente de execute_pending_tasks.
    """
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Consultando campañas programadas...",
        flush=True,
    )


def execute_pending_tasks():
    """
    Orquestador del scheduler de campañas (intervalo en general_parameters, segundos).

    En cada ciclo:
      1) Mantiene el log (rotación por líneas y borrado de backups por retención).
      2) Recarga config.json (token, parámetros generales).
      3) Registra en log todo lo programado para hoy (sin ejecutar fechas pasadas).
      4) Ejecuta campañas del día cuya hora ya llegó y aún no están marcadas ejecutada.

    Lo invoca APScheduler según campaign_check_interval_seconds.
    El mensaje periódico en consola lo hace console_message_heartbeat (job aparte).
    """
    # Flask-SQLAlchemy requiere contexto de app fuera de una petición HTTP.
    with app.app_context():
        # --- 1) Mantenimiento de archivos de log ---
        try:
            # Rota execution_log.txt si supera LOG_MAX_LINES (genera backup).
            rotate_log_if_needed()
            # Elimina execution_log_backup_*.txt más viejos que log_retention_hours.
            cleanup_old_log_files()
        except Exception as e:
            # Un fallo aquí no detiene la revisión de campañas.
            logger.error(f"Error en rotación de log (se continúa el ciclo): {e}")

        # --- 2) Configuración en memoria ---
        load_config()
        now = datetime.now()

        due_campaigns = get_campaigns_due_for_execution(now)
        today_campaigns = get_campaigns_scheduled_for_today(now)
        pendientes_hoy = sum(1 for c in today_campaigns if not c.ejecutada)

        if due_campaigns:
            log_activity(
                f"Scheduler: ejecutando {len(due_campaigns)} campaña(s) "
                f"({pendientes_hoy} pendiente(s) hoy)"
            )
        else:
            log_activity(
                f"Scheduler: sin campañas por ejecutar ahora "
                f"({pendientes_hoy} pendiente(s) hoy)"
            )

        for campaign in due_campaigns:
            try:
                execute_campaign(campaign)
            except Exception as e:
                log_task(
                    f"Error ejecutando campaña id={campaign.id} ({campaign.nombre}): {e}",
                    level="ERROR",
                )
                db.session.rollback()

        try:
            auto_result = check_auto_campaigns_schedule(app)
            if auto_result.get("due"):
                log_activity(
                    f"Scheduler automático: {auto_result.get('started', 0)} iniciada(s), "
                    f"{auto_result.get('skipped', 0)} omitida(s)"
                )
        except Exception as e:
            log_task(f"Error revisando campañas automáticas: {e}", level="ERROR")
            db.session.rollback()


_initial_interval = get_campaign_check_interval_seconds()
_console_interval = get_console_message_interval_seconds()
_now = datetime.now()
scheduler.add_job(
    execute_pending_tasks,
    trigger=IntervalTrigger(seconds=_initial_interval),
    id=CAMPAIGN_SCHEDULER_JOB_ID,
    replace_existing=True,
)
scheduler.add_job(
    console_message_heartbeat,
    trigger=IntervalTrigger(seconds=_console_interval),
    id=CONSOLE_MESSAGE_JOB_ID,
    replace_existing=True,
    next_run_time=_now,
)
logger.info(
    f"Scheduler de campañas iniciado: cada {_initial_interval} segundos"
)
logger.info(
    f"Mensaje consola (independiente): cada {_console_interval} segundos"
)
log_activity("Aplicación iniciada; monitoreo de campañas activo")
console_message_heartbeat()


# ========== INICIALIZAR DESCARGA AUTOMÁTICA ==========
def init_auto_download_on_startup():
    """Inicializa el sistema de descargas automáticas al iniciar la aplicación."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("download_auto")
        if spec is not None:
            from download_auto import iniciar_scheduler, estado_scheduler
            
            # Mostrar estado actual
            estado = estado_scheduler()
            logger.info("="*60)
            logger.info("📦 SISTEMA DE DESCARGAS AUTOMÁTICAS")
            logger.info("="*60)
            logger.info(f"📁 Ruta base: {estado.get('base_dir')}")
            logger.info(f"📋 Servidores configurados: {estado.get('servidores')}")
            if estado.get('servidores_lista'):
                for i, nombre in enumerate(estado.get('servidores_lista', []), 1):
                    logger.info(f"   {i}. {nombre}")
            logger.info(f"🕐 Horarios: {', '.join(estado.get('horarios', []))}")
            logger.info(f"📊 Mapeo cortes: {estado.get('corte_mapeo', {})}")
            
            # Activar automáticamente al inicio
            logger.info("\n🔄 Activando descargas automáticas...")
            resultado = iniciar_scheduler()
            if resultado:
                logger.info("✅ Descargas automáticas ACTIVADAS")
                logger.info(f"   Horarios configurados: {', '.join(estado.get('horarios', []))}")
            else:
                logger.warning("⚠️ No se pudieron activar las descargas automáticas")
                logger.warning("   Posibles causas:")
                logger.warning("   1. La ruta base no existe")
                logger.warning("   2. No hay servidores configurados en config.json")
                logger.warning("   3. Los tokens no están configurados")
            logger.info("="*60)
        else:
            logger.info("Módulo download_auto no encontrado. Descargas automáticas desactivadas.")
    except Exception as e:
        logger.error(f"Error inicializando descargas automáticas: {e}")

# Llamar a la función
init_auto_download_on_startup()