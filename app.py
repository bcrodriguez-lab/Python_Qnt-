from datetime import datetime, date, timezone,timedelta
from flask import jsonify, render_template, request, send_from_directory, send_file
from uuid import uuid4
import io

from excel_report_builder import build_wolkvox_excel, _safe_filename
import json
import requests
import re
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
import pandas as pd

import pytz
COLOMBIA_TZ = pytz.timezone("America/Bogota")

from datetime import datetime, timedelta
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from backend import (
    _obtener_token_servidor, app, db, scheduler, execute_pending_tasks, LOG_FILE,
    get_authorization_headers, load_config, CONFIG, logger,
    log_task, log_gui_action, read_recent_log_lines,
    cleanup_old_log_files, reschedule_campaign_check_job,
    reschedule_console_message_job, DOWNLOAD_FOLDER,
)
from general_params import load_general_parameters, save_general_parameters
from conexion_bigquery import get_bigquery_client
from bigquery import (
    escribir_resultados_campana, count_query_results,
    fetch_sms_query_rows, sync_campaigns_to_bigquery, validate_query_columns
)
from services.sms_service import (
    SCHEDULE_TABLE, SMS_LOG_TABLE, SmsServiceError, aplicar_validaciones,
    enviar_sms_desde_filas, guardar_programacion, preview_sms,
    verificar_lista_negra, limpiar_numero, obtener_lista_negra,
)

from campaigns import (
    list_campaigns, get_campaign, get_all_campaigns, create_campaign,
    update_campaign, delete_campaign, has_pending_campaigns_for_server,
    TIPO_CAMPANA_OPTIONS, TIPO_CAMPANA_CON_FLUJO, validate_tipo_campana,
)
from servers import (
    load_servers, save_server, get_server, delete_server,
    get_server_url_prefix, get_config_servidor_default,
)
from flujos_proceso import (
    load_flujos_proceso, get_flujo, save_flujo, delete_flujo,
    list_flujos_by_server, validate_flujo_for_campaign,
)
from apis import (
    load_apis, save_api, get_api, delete_api, ensure_demo_api,
    is_system_api, HTTP_METHODS, validate_http_metodo, normalize_http_metodo,
)
from api_runner import (
    list_handler_files, invoke_handler, extract_url_placeholders,
    build_request_body_preview, build_request_headers_preview,
    build_request_url_preview,
)
from server_apis import load_assignment_matrix, set_server_api_active
from dashboard import get_dashboard_data, refresh_dashboard_from_wolkvox
from auto_campaign_executor import (
    is_auto_campaign_running,
    request_stop_auto_campaign,
    start_auto_campaign_async,
    fetch_data_from_bigquery,
    _get_token,
    _get_base_url_wolkvox,
)
from auto_campaigns import (
    create_auto_campaign,
    delete_auto_campaign,
    get_auto_campaign,
    list_auto_campaigns,
    list_execution_logs,
    parse_auto_campaign_id,
    update_auto_campaign,
)
from services.email_client import EmailClient, EmailClientError
from services.email_service import (
    EMAIL_LOG_TABLE, EmailServiceError, detectar_columna_email,
    construir_contenido, preview_email, guardar_email_log, extraer_variables,
)
import logging
from database import db, ProgramacionSms
# ==================== CONFIGURACIÓN GLOBAL ====================
PROJECT_ID = "capable-arbor-209819"
bq_client = None
import os

OPERADORES_SMS = [
    "HELLO_BPO",
    "QNT_RBK_2",
    "MORACERO_CD2",
    "QNT_RBK_1.2",
    "AVALOGIC",
    "DIGITAL_MONTOS_BAJOS",
    "DIGITAL_ESPECIAL",
    "PAZ_SALVO",
    "MANTENIMIENTO_DIGITAL",
    "DIGITAL_1",
    "QNT_PERSONA_JURIDICA",
    "QNT_JUD",
    "MANTENIMIENTO-QNT",
    "DIGITAL_2",
    "GRADUADOS",
    "QNT_OPERADOR_ESPECIAL",
    "GENNIALS_BPO_CD",
    "QNT_COBRO",
    "VICBRA",
    "MORACERO_CD",
    "QNT_FALLECIDOS",
    "QNT_RECAUDO",
    "QNT_RBK_1.1",
    "SATELITE_1",
    "MANTENIMIENTO-QNT-MORAS-ALTAS",
    "MANTENIMIENTO-ESP-QNT",
    "VENTAS_TERCEROSJUD"
]


#==================== CONFIGURACIÓN DE LOGS ====================
# =============== Logger De Programcion Robots 
ruta_log = os.path.abspath("Programacion_Robot_simple.log")

logger_programacion = logging.getLogger("Programacion_Robot_simple")

logger_programacion.setLevel(logging.INFO)

handler_programacion = logging.FileHandler(
    ruta_log,
    encoding="utf-8"
)

formatter_programacion = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

handler_programacion.setFormatter(formatter_programacion)

logger_programacion.addHandler(handler_programacion)

logger_programacion.propagate = False


#================ Logger De Programcion SMS ========================
ruta_log = os.path.abspath("Programacion_sms.log")

logger_sms = logging.getLogger("Programacion_sms")

logger_sms.setLevel(logging.INFO)

handler_sms = logging.FileHandler(
    ruta_log,
    encoding="utf-8"
)

formatter_sms = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

handler_sms.setFormatter(formatter_sms)

logger_sms.addHandler(handler_sms)

logger_sms.propagate = False


 #==================== PÁGINAS PRINCIPALES ====================

@app.route("/")
def index():
    stats = get_dashboard_data()
    return render_template("index.html", **stats)


@app.route('/reports')
def reports_index():
    default_server = get_config_servidor_default() or ""
    today = date.today()
    return render_template(
        "report_download.html",
        default_server=default_server,
        default_start=today.strftime("%Y-%m-%d"),
        default_end=today.strftime("%Y-%m-%d")
    )


@app.route('/reports/download', methods=['POST'])
def reports_download():
    data = request.form or request.get_json() or {}
    server = (data.get('server') or '').strip()
    date_ini = (data.get('date_ini') or '').strip()
    date_end = (data.get('date_end') or '').strip()

    date_ini_ts = _to_wolkvox_ts(date_ini, is_end=False)
    date_end_ts = _to_wolkvox_ts(date_end, is_end=True)

    if not date_ini or not date_end:
        return jsonify({'success': False, 'message': 'Se requieren date_ini y date_end.'}), 400
    if not server:
        return jsonify({'success': False, 'message': 'El parámetro server es obligatorio.'}), 400

    try:
        srv = get_server(server)
    except Exception:
        srv = None

    if srv:
        prefix = (srv.get('url') or '').strip().rstrip('/')
        base_url = prefix if prefix.lower().startswith('http') else f"https://wv{prefix}.wolkvox.com"
        url = f"{base_url}/api/v2/reports_manager.php?api=cdr_1&date_ini={date_ini_ts}&date_end={date_end_ts}"
    else:
        base_url = server.rstrip('/') if server.lower().startswith('http') else f"https://wv{server}.wolkvox.com"
        url = f"{base_url}/api/v2/reports_manager.php?api=cdr_1&date_ini={date_ini_ts}&date_end={date_end_ts}"

    try:
        headers = get_authorization_headers(server) or {}
        resp = requests.get(url, headers=headers, timeout=60)
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error al conectar con Wolkvox: {e}'}), 500

    if resp.status_code != 200:
        return jsonify({'success': False, 'message': f'Wolkvox devolvió {resp.status_code}', 'text': resp.text[:500]}), 502

    try:
        data_json = resp.json()
        if isinstance(data_json, list):
            rows = data_json
        elif isinstance(data_json, dict):
            rows = data_json.get('data', data_json.get('files', [data_json]))
        else:
            rows = [{'raw': resp.text}]
    except Exception:
        rows = [{'raw': resp.text}]

    today_str = datetime.utcnow().strftime('%Y%m%d')
    safe_server = _safe_filename(server or 'server')
    filename = f"1. Detalle de las llamadas.{today_str}-{safe_server}.xlsx"
    bio, _ = build_wolkvox_excel(rows=rows, filename=filename)

    return send_file(bio, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route("/reportes")
def reportes():
    load_config()
    servers_result = load_servers()
    servers = servers_result.get('servers', []) if servers_result.get('success') else []
    today = datetime.utcnow().date()
    return render_template("reportes.html", servidor="", wolkvox_token="",
                          servers=servers, default_start=today.isoformat(), default_end=today.isoformat())


@app.route("/api/invoke", methods=["POST"])
def invoke_api():
    data = request.get_json()
    url = data.get("url")
    if not url:
        return jsonify({"error": "Se requiere 'url'"}), 400
    try:
        log_gui_action("API INVOKE", url=url)
        headers = get_authorization_headers()
        response = requests.get(url, headers=headers, timeout=10)
        result = {
            "success": response.status_code == 200,
            "status": response.status_code,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": "exito" if response.status_code == 200 else f"Error {response.status_code}",
            "data": response.text[:500]
        }
        log_gui_action("API INVOKE OK" if response.status_code == 200 else "API INVOKE fallo", status=response.status_code)
        return jsonify(result), 200
    except requests.Timeout:
        return jsonify({"success": False, "message": "Timeout"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== CONFIGURACIÓN GENERAL ====================

@app.route('/config-general')
def config_general():
    result = load_general_parameters()
    parameters = result.get("parameters", {})
    limits = result.get("limits", {})
    error = None if result.get("success") else result.get("message")
    return render_template("config_general.html", parameters=parameters, limits=limits, error=error)


@app.route('/config-general/save', methods=['POST'])
def config_general_save():
    data = request.get_json() or {}
    result = save_general_parameters(data)
    if not result.get("success"):
        return jsonify(result), 400
    load_config()
    interval = reschedule_campaign_check_job(result["parameters"]["campaign_check_interval_seconds"])
    console_interval = reschedule_console_message_job(result["parameters"]["console_message_interval_seconds"])
    result["scheduler_interval_seconds"] = interval
    result["console_message_interval_seconds"] = console_interval
    result["logs_deleted"] = cleanup_old_log_files()
    return jsonify(result)


# ==================== SERVIDORES ====================

@app.route('/config-servers')
def config_servers():
    result = load_servers()
    servers = result.get('servers', []) if result.get('success') else []
    error = None if result.get('success') else result.get('message')
    return render_template('config_servers.html', servers=servers, error=error)


@app.route('/config-servers/get', methods=['POST'])
def config_servers_get():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre es obligatorio.'}), 400
    server = get_server(name)
    if not server:
        return jsonify({'success': False, 'message': 'No se encontró el servidor.'}), 404
    server['deletable'] = not has_pending_campaigns_for_server(name)
    return jsonify({'success': True, 'server': server})


@app.route('/config-servers/save', methods=['POST'])
def config_servers_save():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    url = (data.get('url') or '').strip()
    token = (data.get('token') or '').strip()
    original_name = (data.get('original_name') or '').strip()
    if not name or not url:
        return jsonify({'success': False, 'message': 'Nombre y URL son obligatorios.'}), 400
    result = save_server(name, url, token, original_name=original_name or None)
    if result.get('success'):
        log_gui_action("Guardar servidor", nombre=name)
        return jsonify(result)
    return jsonify(result), 500


@app.route('/config-servers/delete', methods=['POST'])
def config_servers_delete():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre es obligatorio.'}), 400
    result = delete_server(name)
    if result.get('success'):
        log_gui_action("Eliminar servidor", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


# ==================== APIs ====================

@app.route('/config-apis')
def config_apis():
    ensure_demo_api()
    result = load_apis()
    apis = result.get('apis', []) if result.get('success') else []
    error = None if result.get('success') else result.get('message')
    return render_template('config_apis.html', apis=apis, error=error,
                          handler_files=list_handler_files(), http_methods=HTTP_METHODS)


@app.route('/config-apis/get', methods=['POST'])
def config_apis_get():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre es obligatorio.'}), 400
    if is_system_api(name):
        return jsonify({'success': False, 'message': 'La API DEMO no se puede modificar.'}), 403
    api = get_api(name)
    if not api:
        return jsonify({'success': False, 'message': 'No se encontró la API.'}), 404
    return jsonify({'success': True, 'api': api})


@app.route('/config-apis/save', methods=['POST'])
def config_apis_save():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    url = (data.get('url') or '').strip()
    descripcion = (data.get('descripcion') or '').strip()
    original_name = (data.get('original_name') or '').strip()
    frecuencia = data.get('frecuencia_ejecucion')
    if frecuencia is None or frecuencia == '':
        return jsonify({'success': False, 'message': 'La frecuencia es obligatoria.'}), 400
    try:
        frecuencia = int(frecuencia)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'La frecuencia debe ser un número entero.'}), 400
    archivo = (data.get('archivo') or '').strip()
    metodo_raw = (data.get('metodo') or '').strip()
    if not name or not url or not archivo or not metodo_raw:
        return jsonify({'success': False, 'message': 'Nombre, URL, archivo y método son obligatorios.'}), 400
    metodo_check = validate_http_metodo(metodo_raw)
    if not metodo_check.get('success'):
        return jsonify(metodo_check), 400
    metodo = normalize_http_metodo(metodo_raw)
    result = save_api(name, url, descripcion, frecuencia, archivo, metodo, original_name=original_name or None)
    if result.get('success'):
        log_gui_action("Guardar API", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


@app.route('/config-apis/test-template', methods=['POST'])
def config_apis_test_template():
    data = request.get_json() or {}
    api, error = _resolve_api_for_test(data)
    if error:
        return jsonify({"success": False, "message": error}), 400
    template = _default_api_test_payload(api)
    return jsonify({"success": True, "api": api, "url_placeholders": extract_url_placeholders(api.get("url") or ""), "template": template})


@app.route('/config-apis/preview-body', methods=['POST'])
def config_apis_preview_body():
    data = request.get_json() or {}
    payload, error = _parse_api_test_payload(data.get("payload"))
    if error:
        return jsonify({"success": False, "message": error}), 400
    api, error = _resolve_api_for_test(data)
    if error:
        return jsonify({"success": False, "message": error}), 400
    try:
        body = build_request_body_preview(api.get("archivo") or "", payload or {}, api.get("url") or "", api)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    request_headers = build_request_headers_preview(api.get("archivo") or "", payload or {}, api)
    try:
        request_url = build_request_url_preview(api.get("url") or "", payload or {}, api)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "request_body": body, "request_headers": request_headers, "request_url": request_url})


@app.route('/config-apis/test', methods=['POST'])
def config_apis_test():
    data = request.get_json() or {}
    payload, error = _parse_api_test_payload(data.get("payload"))
    if error:
        return jsonify({"success": False, "message": error}), 400
    api, error = _resolve_api_for_test(data)
    if error:
        return jsonify({"success": False, "message": error}), 400
    archivo = (api.get("archivo") or "").strip()
    metodo = (api.get("metodo") or "").strip()
    if not archivo or not metodo:
        return jsonify({"success": False, "message": "La API no tiene archivo o método configurado."}), 400
    request_body = build_request_body_preview(archivo, payload or {}, api.get("url") or "", api)
    request_headers = build_request_headers_preview(archivo, payload or {}, api)
    try:
        preview_url = build_request_url_preview(api.get("url") or "", payload or {}, api)
    except ValueError:
        preview_url = ""
    result = invoke_handler(archivo, metodo, api, payload or {})
    return jsonify({
        "success": bool(result.get("success")),
        "message": result.get("message", ""),
        "request_body": request_body,
        "request_headers": request_headers,
        "request_url": result.get("url") or preview_url,
        "result": result
    })


@app.route('/config-apis/delete', methods=['POST'])
def config_apis_delete():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre es obligatorio.'}), 400
    result = delete_api(name)
    if result.get('success'):
        log_gui_action("Eliminar API", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


@app.route('/config-server-apis')
def config_server_apis():
    ensure_demo_api()
    result = load_assignment_matrix()
    return render_template('config_server_apis.html', **result)


@app.route('/config-server-apis/toggle', methods=['POST'])
def config_server_apis_toggle():
    data = request.get_json() or {}
    server = (data.get('server') or '').strip()
    api = (data.get('api') or '').strip()
    active = data.get('active')
    if active is None:
        return jsonify({'success': False, 'message': 'El estado activo es obligatorio.'}), 400
    if isinstance(active, str):
        active = active.strip().lower() in ('1', 'true', 'yes', 'on')
    else:
        active = bool(active)
    if not server or not api:
        return jsonify({'success': False, 'message': 'Servidor y API son obligatorios.'}), 400
    result = set_server_api_active(server, api, active)
    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 400


# ==================== FLUJOS DE PROCESO ====================

@app.route("/config-flujos-proceso")
def config_flujos_proceso():
    result = load_flujos_proceso()
    servers_result = load_servers()
    servers = servers_result.get("servers", []) if servers_result.get("success") else []
    return render_template("config_flujos_proceso.html",
                          flujos=result.get("flujos", []), servers=servers,
                          error=None if result.get("success") else result.get("message"))


@app.route("/config-flujos-proceso/save", methods=["POST"])
def config_flujos_proceso_save():
    data = request.get_json() or {}
    flujo_id = (data.get("id") or "").strip()
    nombre = (data.get("nombre") or "").strip()
    servidor = (data.get("servidor") or "").strip()
    original_id = (data.get("original_id") or "").strip()
    if not flujo_id or not nombre or not servidor:
        return jsonify({"success": False, "message": "Id, nombre y servidor son obligatorios."}), 400
    result = save_flujo(flujo_id, nombre, servidor, original_id=original_id or None)
    if result.get("success"):
        return jsonify(result)
    return jsonify(result), 400


@app.route("/config-flujos-proceso/delete", methods=["POST"])
def config_flujos_proceso_delete():
    data = request.get_json() or {}
    flujo_id = (data.get("id") or "").strip()
    if not flujo_id:
        return jsonify({"success": False, "message": "El id es obligatorio."}), 400
    result = delete_flujo(flujo_id)
    if result.get("success"):
        return jsonify(result)
    return jsonify(result), 400


@app.route("/config-bigquery/flujos-proceso", methods=["GET"])
def config_bigquery_flujos_proceso():
    server = (request.args.get("server") or "").strip()
    flujos = list_flujos_by_server(server)
    return jsonify({"success": True, "flujos": flujos})


# ==================== CAMPAÑAS BIGQUERY ====================

@app.route("/config-bigquery")
def config_bigquery():
    campaigns = []
    error = None
    today = date.today()
    servers_result = load_servers()
    servers = servers_result.get('servers', []) if servers_result.get('success') else []
    options = servers_result.get('options', {}) if servers_result.get('success') else {}
    result = list_campaigns(
        start_date=request.args.get("start_date", today.strftime("%Y-%m-%d")),
        end_date=request.args.get("end_date", today.strftime("%Y-%m-%d")),
        search=request.args.get("search", "").strip(),
        server=request.args.get('server', '').strip() or None,
        operacion=request.args.get('operacion', '').strip() or None,
        tipo=request.args.get('tipo', '').strip() or None,
        usuario=request.args.get('usuario', '').strip() or None,
        page=max(1, int(request.args.get("page", 1))),
        page_size=min(max(int(request.args.get("page_size", 10)), 5), 50),
    )
    if result.get("success"):
        campaigns = result.get("campaigns", [])
    else:
        error = result.get("message")
    return render_template("config_bigquery.html", campaigns=campaigns, error=error,
                          servers=servers, operaciones=options.get('operaciones', []),
                          tipos=options.get('tipos', []), usuarios=options.get('usuarios', []),
                          tipos_campana=TIPO_CAMPANA_OPTIONS,
                          tipos_campana_con_flujo=TIPO_CAMPANA_CON_FLUJO,
                          total=result.get("total", 0), total_pages=result.get("total_pages", 1))


@app.route("/config-bigquery/save", methods=["POST"])
def save_bigquery_campaign():
    data = request.get_json() or {}
    payload, error = _parse_campaign_payload(data)
    if error:
        return jsonify({"success": False, "message": error[0]}), error[1]
    save_result = create_campaign(payload)
    if not save_result.get("success"):
        return jsonify({"success": False, "message": save_result.get("message", "Error guardando campaña.")}), 500
    return jsonify({"success": True, "message": save_result.get("message"), "campaign": save_result.get("campaign")})


@app.route("/config-bigquery/update", methods=["POST"])
def update_bigquery_campaign():
    data = request.get_json() or {}
    campaign_id = parse_campaign_id(data.get("id"))
    if campaign_id is None:
        return jsonify({"success": False, "message": "ID obligatorio."}), 400
    payload, error = _parse_campaign_payload(data)
    if error:
        return jsonify({"success": False, "message": error[0]}), error[1]
    existing = get_campaign(campaign_id)
    if not existing:
        return jsonify({"success": False, "message": "No se encontró la campaña."}), 404
    update_result = update_campaign(campaign_id, payload)
    if not update_result.get("success"):
        return jsonify({"success": False, "message": update_result.get("message", "Error actualizando.")}), 500
    return jsonify({"success": True, "message": update_result.get("message"), "campaign": update_result.get("campaign")})


@app.route("/config-bigquery/delete", methods=["POST"])
def delete_bigquery_campaign():
    data = request.get_json() or {}
    campaign_id = parse_campaign_id(data.get("id"))
    if campaign_id is None:
        return jsonify({"success": False, "message": "ID obligatorio."}), 400
    delete_result = delete_campaign(campaign_id)
    if not delete_result.get("success"):
        return jsonify({"success": False, "message": delete_result.get("message")}), 500
    return jsonify({"success": True, "message": delete_result.get("message")})


@app.route("/config-bigquery/sync", methods=["POST"])
def sync_bigquery_campaigns():
    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar BigQuery."}), 500
    sync_result = sync_campaigns_to_bigquery(bq_client, get_all_campaigns())
    if not sync_result.get("success"):
        return jsonify({"success": False, "message": sync_result.get("message")}), 500
    return jsonify({"success": True, "message": sync_result.get("message"), "rows_written": sync_result.get("rows_written", 0)})


@app.route("/config-bigquery/test-count", methods=["POST"])
def test_bigquery_query():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar BigQuery."}), 500
    result = count_query_results(bq_client, query)
    if not result.get("success"):
        return jsonify(result), 400
    campaign_id = parse_campaign_id(data.get("campaign_id") or data.get("id"))
    if campaign_id is not None:
        from database import Campaign
        campaign = Campaign.query.get(campaign_id)
        if campaign:
            campaign.total_clientes = int(result.get("total") or 0)
            db.session.commit()
    return jsonify(result)
# ==================== FUNCIONES AUXILIARES GENERALES ====================

def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
    """Convierte fecha a timestamp Wolkvox."""
    if not s:
        return ''
    try:
        if 'T' in s or len(s) > 10:
            dt = datetime.fromisoformat(s)
        else:
            d = date.fromisoformat(s)
            dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
        return dt.strftime('%Y%m%d%H%M%S')
    except Exception:
        return s


def _limpiar_nat(df):
    """Convierte columnas datetime a string y reemplaza NaT/NaN por None."""
    for col in df.select_dtypes(include=['datetime64[ns]', 'datetime64[ns, UTC]', 'datetimetz']).columns:
        df[col] = df[col].apply(lambda x: x.isoformat() if pd.notnull(x) else None)
    df = df.where(pd.notnull(df), None)
    return df


def _get_sms_rows(query):
    """Ejecuta consulta BigQuery y devuelve filas."""
    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return None, (jsonify({"success": False, "message": "No se pudo inicializar BigQuery."}), 500)
    result = fetch_sms_query_rows(bq_client, query)
    if not result.get("success"):
        return None, (jsonify(result), 400)
    return result["rows"], None


#--------------------------Normalizar telefnos ------------------------------------------------#
def normalizar_telefono(telefono):
    """
    Normaliza un teléfono para comparación en lista negra
    - Elimina caracteres no numéricos
    - Elimina prefijo 57 y +57
    - Maneja diferentes formatos (celulares, fijos, etc.)
    """
    import re
    
    if not telefono:
        return ""
    
    # 1. Eliminar caracteres no numéricos
    limpio = re.sub(r'[^0-9]', '', str(telefono))
    
    if not limpio:
        return ""
    
    # 2. Eliminar prefijo 57 (si existe)
    if limpio.startswith('57'):
        limpio = limpio[2:]
    
    # 3. Eliminar prefijo +57 (por si acaso, después de limpiar)
    if limpio.startswith('57'):
        limpio = limpio[2:]
    
    # 4. Si tiene 9 al inicio y más de 10 dígitos, quitar el 9
    if len(limpio) > 10 and limpio.startswith('9'):
        limpio = limpio[1:]
    
    # 5. Si tiene 10 dígitos, es celular colombiano
    if len(limpio) == 10:
        return limpio
    
    # 6. Si tiene 7 dígitos, es fijo (asumimos Bogotá, código 1)
    if len(limpio) == 7:
        return f"1{limpio}"
    
    # 7. Si tiene 8 dígitos, es fijo con código de ciudad
    if len(limpio) == 8:
        return limpio
    
    # 8. Otros casos, devolver el número limpio sin modificar
    return limpio

#------------------------ Listas Negras --------------------------------------------#


def get_blacklist_phones():
    """
    Obtiene los teléfonos en lista negra desde la tabla Telefonos_Tutela
    Retorna: set con teléfonos normalizados (múltiples variantes)
    """
    import re
    
    try:
        query = """
            SELECT DISTINCT Telefono AS telefono
            FROM `capable-arbor-209819.Tablas_Reporteria.Telefonos_Tutela`
            WHERE Telefono IS NOT NULL
              AND Telefono != ''
        """
        df = bq_client.query(query).to_dataframe()
        
        blacklist = set()
        for telefono in df['telefono'].astype(str):
            # Normalizar usando la misma función
            normalizado = normalizar_telefono(telefono)
            
            if normalizado:
                # Guardar el teléfono normalizado
                blacklist.add(normalizado)
                
                # Guardar variantes para comparación flexible
                if len(normalizado) == 10:
                    # Celular con prefijo 57
                    blacklist.add(f"57{normalizado}")
                elif len(normalizado) == 7:
                    # Fijo con prefijo 57 + código de ciudad
                    blacklist.add(f"571{normalizado}")
                elif len(normalizado) == 8:
                    # Fijo con prefijo 57
                    blacklist.add(f"57{normalizado}")
        
        print(f"📋 Lista negra cargada: {len(blacklist)} teléfonos")
        
        # Mostrar ejemplos para depuración
        ejemplo = list(blacklist)[:5]
        print(f"📋 Ejemplo de teléfonos en lista negra: {ejemplo}")
        
        return blacklist
    except Exception as e:
        print(f"❌ Error cargando lista negra: {e}")
        return set()

    
def validar_destinatarios_email(rows, client, confirmar_reenvio=False):
    """Valida emails: inválidos, duplicados y lista negra."""
    from datetime import datetime, timezone
    
    hoy = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    email_column = None
    for col in rows[0].keys():
        if col.lower() in ['email', 'correo', 'mail']:
            email_column = col
            break
    
    if not email_column:
        return {"success": False, "message": "No se encontró columna de email"}
    
    todos_emails = [str(row[email_column]).strip().lower() for row in rows if row.get(email_column)]
    total_consulta = len(todos_emails)
    validos = [e for e in todos_emails if '@' in e]
    invalidos = total_consulta - len(validos)
    
    emails_validos_str = "', '".join(validos)
    
    # Duplicados
    query_dup = f"""
        SELECT DISTINCT LOWER(email) as email
        FROM `{EMAIL_LOG_TABLE}`
        WHERE DATE(fecha_envio) = '{hoy}'
        AND LOWER(email) IN ('{emails_validos_str}')
    """
    try:
        df_dup = client.query(query_dup).to_dataframe()
        duplicados_set = set(df_dup['email'].tolist()) if not df_dup.empty else set()
    except:
        duplicados_set = set()
    
    # Lista negra
    query_black = f"""
        SELECT DISTINCT LOWER(EMAIL) as email
        FROM `Tablas_Reporteria.Email_Tutela`
        WHERE LOWER(EMAIL) IN ('{emails_validos_str}')
    """
    try:
        df_black = client.query(query_black).to_dataframe()
        blacklist_set = set(df_black['email'].tolist()) if not df_black.empty else set()
    except:
        blacklist_set = set()
    
    if confirmar_reenvio:
        a_enviar = [e for e in validos if e not in blacklist_set]
    else:
        a_enviar = [e for e in validos if e not in duplicados_set and e not in blacklist_set]
    
    return {
        "success": True,
        "total_consulta": total_consulta,
        "total_validos": len(validos),
        "invalidos": invalidos,
        "duplicados": len(duplicados_set),
        "blacklist": len(blacklist_set),
        "a_enviar": len(a_enviar),
        "email_column": email_column
    }


def init_bigquery():
    """Inicializar conexión BigQuery."""
    global bq_client
    try:
        bq_client = get_bigquery_client()
        logger.info("Conexion a BigQuery establecida exitosamente")
    except Exception as e:
        logger.error(f"Error al conectar con BigQuery: {e}")
        bq_client = None


def _auto_campaign_form_context(campaign=None, logs=None):
    servers_result = load_servers()
    servers = servers_result.get("servers", []) if servers_result.get("success") else []
    options = servers_result.get("options", {}) if servers_result.get("success") else {}
    return {
        "campaign": campaign,
        "logs": logs or [],
        "servers": servers,
        "operaciones": options.get("operaciones", []),
        "tipos": options.get("tipos", []),
        "usuarios": options.get("usuarios", []),
        "tipos_campana": TIPO_CAMPANA_OPTIONS,
        "tipos_campana_con_flujo": TIPO_CAMPANA_CON_FLUJO,
    }

#================= SMS - PÁGINA ====================




@app.route("/config-sms")
def config_sms():
    return render_template("config_sms.html")



@app.route("/api/sms/preview", methods=["POST"])
def sms_preview():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()

    if not query or not plantilla:
        return jsonify({"success": False, "message": "La consulta SQL y la plantilla son obligatorias."}), 400

    try:
        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response
        result = preview_sms(rows, plantilla)
        if not result.get("success"):
            return jsonify(result), 400
        return jsonify({"success": True, **result})
    except Exception as exc:
        logger.exception("Error en vista previa SMS")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - ENVIAR ====================

@app.route("/api/sms/enviar", methods=["POST"])
def sms_send():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    campaign = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    allow_resend = bool(data.get("confirmar_reenvio", False))

    if not query or not plantilla:
        return jsonify({"success": False, "message": "La consulta SQL y la plantilla son obligatorias."}), 400

    try:
        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response
        infobip_config = (CONFIG or load_config()).get("infobip", {})
        result = enviar_sms_desde_filas(
            rows, plantilla, infobip_config,
            client=bq_client, campaign=campaign, usuario=usuario,
            query_sql=query, allow_resend=allow_resend
        )
        registrar_resumen_sms(campaign, usuario, query, plantilla,
                             result["total_preparados"], result["enviados"],
                             result["fallidos"], "exitoso" if result["fallidos"] == 0 else "parcial")
        return jsonify({"success": result["fallidos"] == 0, **result})
    except SmsServiceError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error enviando SMS")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - VALIDAR ====================

@app.route("/api/sms/validar", methods=["POST"])
def sms_validar():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    allow_resend = bool(data.get("confirmar_reenvio", False))

    if not query or not plantilla:
        return jsonify({"success": False, "message": "La consulta SQL y la plantilla son obligatorias."}), 400

    try:
        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response
        preview = preview_sms(rows, plantilla)
        if not preview.get("success"):
            return jsonify(preview), 400

        from services.sms_service import preparar_sms, verificar_lista_negra, verificar_duplicados
        prepared, details = preparar_sms(rows, plantilla)
        phones = [item["phone"] for item in prepared]
        blocked = verificar_lista_negra(bq_client, phones)
        duplicates = verificar_duplicados(bq_client, phones)

        allowed = [item for item in prepared if item["phone"] not in blocked]
        if not allow_resend:
            allowed = [item for item in allowed if item["phone"] not in duplicates]

        preview_mensajes = [{"telefono": item["phone"], "mensaje": item["text"], "longitud": len(item["text"])} for item in allowed[:3]]

        return jsonify({
            "success": True,
            "total_consulta": len(rows),
            "total_validos": details.get("total_validos", 0),
            "invalidos": details.get("invalid_numbers", 0),
            "duplicados": len(duplicates),
            "blacklist": len(blocked),
            "a_enviar": len(allowed),
            "preview": preview_mensajes
        })
    except Exception as exc:
        logger.exception("Error en validación SMS")
        return jsonify({"success": False, "message": str(exc)}), 500
    

def registrar_resumen_sms(campaign, usuario, query, plantilla, total, enviados, fallidos, estado):
    """Registra un resumen del envío en el log (no interrumpe el flujo)."""
    try:
        log_task(
            f"[SMS] Campaña='{campaign}' Usuario='{usuario}' "
            f"Total={total} Enviados={enviados} Fallidos={fallidos} Estado={estado}"
        )
    except Exception:
        pass
# ==================== SCHEDULER SMS ====================

def _sms_job_id(prog):
    """ID único del job para una programación."""
    return f"sms_{prog.tipo_programacion}_{prog.id}"


def _sms_now():
    return datetime.now(COLOMBIA_TZ)


def execute_sms_schedule(schedule_id: str):
    """
    Ejecuta UNA programación SMS específica.
    - Simple: se ejecuta una vez y queda como 'enviado'.
    - Recurrente: valida hora/fecha_fin/ya-ejecutado-hoy antes de enviar.
    """
    with app.app_context():
        from database import db, ProgramacionSms

        prog = ProgramacionSms.query.get(int(schedule_id))
        if not prog:
            logger_sms.warning(f"⚠️ Programación SMS {schedule_id} no existe")
            return

        if prog.estado != 'pendiente':
            logger_sms.info(f"⏭️ SMS #{prog.id}: estado={prog.estado}, se omite")
            return

        tipo = (prog.tipo_programacion or 'simple').lower()
        ahora = _sms_now()
        hoy = ahora.date()

        # ---------- VALIDACIONES SOLO PARA RECURRENTE ----------
        if tipo == 'recurrente':
            hora_str = (prog.hora_inicio or '08:00').strip()
            try:
                hh, mm = map(int, hora_str.split(':'))
            except Exception:
                hh, mm = 8, 0

            # 1) ¿Ya se ejecutó hoy?
            if prog.fecha_ejecucion:
                fe = prog.fecha_ejecucion
                if fe.tzinfo is None:
                    fe = pytz.utc.localize(fe).astimezone(COLOMBIA_TZ)
                if fe.date() == hoy:
                    logger_sms.info(f"✅ Recurrente SMS #{prog.id}: ya se ejecutó hoy")
                    return

            # 2) ¿Ya llegó la hora?
            objetivo = COLOMBIA_TZ.localize(
                datetime.combine(hoy, datetime.min.time())
            ).replace(hour=hh, minute=mm, second=0, microsecond=0)

            if ahora < objetivo:
                logger_sms.info(f"⏰ Recurrente SMS #{prog.id}: aún no es la hora")
                return

            # 3) ¿Ya pasó fecha_fin?
            if prog.fecha_fin:
                try:
                    ffin = datetime.strptime(prog.fecha_fin, "%Y-%m-%d").date()
                    if hoy > ffin:
                        logger_sms.info(f"🛑 Recurrente SMS #{prog.id}: pasó fecha_fin, completando")
                        prog.estado = 'completado'
                        prog.fecha_actualizacion = datetime.utcnow()
                        db.session.commit()
                        _sms_desagendar(prog)
                        return
                except Exception:
                    pass

        # ---------- EJECUTAR ----------
        try:
            logger_sms.info(f"📤 Ejecutando SMS {tipo.upper()} #{prog.id} - {prog.campana}")

            config = (CONFIG or load_config())
            infobip_config = config.get("infobip", {})

            fetched = fetch_sms_query_rows(bq_client, prog.consulta_sql)
            if not fetched.get("success"):
                raise SmsServiceError(fetched.get("message", "Error en consulta"))

            resultado = enviar_sms_desde_filas(
                fetched["rows"],
                prog.plantilla,
                infobip_config,
                client=bq_client,
                campaign=prog.campana or "",
                usuario=prog.usuario or "sistema",
                query_sql=prog.consulta_sql,
                allow_resend=bool(prog.confirmar_reenvio),
            )

            if tipo == 'simple':
                prog.estado = 'enviado'

            prog.fecha_ejecucion = datetime.utcnow()
            prog.total_destinatarios = resultado.get("total_preparados", 0)
            prog.fecha_actualizacion = datetime.utcnow()
            db.session.commit()

            logger_sms.info(f"✅ SMS {tipo.capitalize()} enviado: #{prog.id}")
            log_gui_action("SMS programado ejecutado", programacion=str(prog.id))

        except Exception as exc:
            logger_sms.exception(f"❌ Error ejecutando SMS #{prog.id}")
            try:
                prog.estado = 'fallido'
                prog.fecha_actualizacion = datetime.utcnow()
                db.session.commit()
            except Exception:
                pass




def agendar_programacion_sms(prog):
    """Registra el job correcto en el scheduler según el tipo."""
    job_id = _sms_job_id(prog)

    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    if prog.estado != 'pendiente':
        return

    tipo = (prog.tipo_programacion or 'simple').lower()

    # ---------- SIMPLE: DateTrigger a la hora exacta ----------
    if tipo == 'simple':
        if not prog.fecha_programada:
            logger_sms.warning(f"⚠️ SMS simple #{prog.id}: sin fecha_programada")
            return

        fecha = prog.fecha_programada
        if fecha.tzinfo is None:
            fecha = COLOMBIA_TZ.localize(fecha)
        else:
            fecha = fecha.astimezone(COLOMBIA_TZ)

        ahora = _sms_now()
        run_date = fecha if fecha > ahora else ahora + timedelta(seconds=5)

        scheduler.add_job(
            execute_sms_schedule,
            trigger=DateTrigger(run_date=run_date),
            args=[str(prog.id)],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger_sms.info(f"🗓️ SMS simple #{prog.id} agendado para {run_date}")

    # ---------- RECURRENTE: IntervalTrigger cada 10 min ----------
    elif tipo == 'recurrente':
        
        scheduler.add_job(
            execute_sms_schedule,
            trigger=IntervalTrigger(minutes=1),
            args=[str(prog.id)],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger_sms.info(f"🔄 SMS recurrente #{prog.id} agendado cada 1 min")


def _sms_desagendar(prog):
    """Quita el job del scheduler."""
    try:
        scheduler.remove_job(_sms_job_id(prog))
        logger_sms.info(f"🗑️ Job SMS removido: {_sms_job_id(prog)}")
    except Exception:
        pass


def cargar_programaciones_sms_al_iniciar():
    """Al arrancar, re-agenda todos los jobs SMS pendientes."""
    from database import ProgramacionSms
    try:
        pendientes = ProgramacionSms.query.filter_by(estado='pendiente').all()
        logger_sms.info(f"🔁 Re-agendando {len(pendientes)} programaciones SMS pendientes")
        for prog in pendientes:
            try:
                agendar_programacion_sms(prog)
            except Exception as e:
                logger_sms.exception(f"Error agendando SMS #{prog.id}: {e}")
    except Exception as e:
        logger_sms.exception(f"Error cargando programaciones SMS: {e}")


# ==================== API SMS - PROGRAMAR ====================

@app.route("/api/sms/programar", methods=["POST"])
def sms_programar_simple():
    """Programa un envío SMS UNA sola vez a una fecha/hora específica."""
    from services.sms_service import (
        guardar_programacion, aplicar_validaciones, SmsServiceError
    )

    global bq_client
    if bq_client is None:
        init_bigquery()

    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    campana = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    allow_resend = bool(data.get("confirmar_reenvio", False))
    fecha_programada = (data.get("fecha_programada") or "").strip()

    if not query or not plantilla or not fecha_programada:
        return jsonify({
            "success": False,
            "message": "Consulta, plantilla y fecha programada son obligatorias."
        }), 400

    try:
        run_date = datetime.fromisoformat(fecha_programada)
        if run_date.tzinfo is None:
            run_date = COLOMBIA_TZ.localize(run_date)
        else:
            run_date = run_date.astimezone(COLOMBIA_TZ)

        if run_date <= datetime.now(COLOMBIA_TZ):
            raise SmsServiceError("La fecha programada debe ser futura.")

        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response

        mensajes_validos, _ = aplicar_validaciones(
            rows, plantilla, bq_client, allow_resend=allow_resend
        )
        if not mensajes_validos:
            raise SmsServiceError("La programación no tiene destinatarios válidos.")

        schedule_id = guardar_programacion(
            query=query,
            plantilla=plantilla,
            campaign=campana,
            usuario=usuario,
            allow_resend=allow_resend,
            total_dest=len(mensajes_validos),
            tipo_programacion="simple",
            fecha_programada=run_date.isoformat(),
            hora_inicio=run_date.strftime("%H:%M"),
        )

        from database import ProgramacionSms
        prog = ProgramacionSms.query.get(schedule_id)
        agendar_programacion_sms(prog)

        log_gui_action(
            "SMS programado (simple)",
            programacion=schedule_id,
            fecha=run_date.isoformat(),
        )

        return jsonify({
            "success": True,
            "id": schedule_id,
            "tipo_programacion": "simple",
            "fecha_programada": run_date.isoformat(),
            "total_destinatarios": len(mensajes_validos),
            "message": "SMS programado correctamente.",
        })

    except (ValueError, SmsServiceError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger_sms.exception("Error programando SMS simple")
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/programar-recurrente", methods=["POST"])
def sms_programar_recurrente():
    """Programa un envío SMS recurrente diario a una hora hasta fecha_fin."""
    from services.sms_service import (
        guardar_programacion, aplicar_validaciones, SmsServiceError
    )

    global bq_client
    if bq_client is None:
        init_bigquery()

    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    campana = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    allow_resend = bool(data.get("confirmar_reenvio", False))
    hora_inicio = (data.get("hora_inicio") or "").strip()
    fecha_fin = (data.get("fecha_fin") or "").strip() or None

    if not query or not plantilla or not hora_inicio:
        return jsonify({
            "success": False,
            "message": "Consulta, plantilla y hora son obligatorias."
        }), 400

    if not re.match(r'^\d{2}:\d{2}$', hora_inicio):
        return jsonify({
            "success": False,
            "message": "Formato de hora inválido. Use HH:MM."
        }), 400

    try:
        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response

        mensajes_validos, _ = aplicar_validaciones(
            rows, plantilla, bq_client, allow_resend=allow_resend
        )
        if not mensajes_validos:
            raise SmsServiceError("La programación no tiene destinatarios válidos.")

        schedule_id = guardar_programacion(
            query=query,
            plantilla=plantilla,
            campaign=campana,
            usuario=usuario,
            allow_resend=allow_resend,
            total_dest=len(mensajes_validos),
            tipo_programacion="recurrente",
            hora_inicio=hora_inicio,
            fecha_fin=fecha_fin,
        )

        from database import ProgramacionSms
        prog = ProgramacionSms.query.get(schedule_id)
        agendar_programacion_sms(prog)

        logger_sms.info(f"✅ SMS recurrente creado: #{schedule_id} (hora={hora_inicio}, fecha_fin={fecha_fin})")

        return jsonify({
            "success": True,
            "id": schedule_id,
            "tipo_programacion": "recurrente",
            "hora_inicio": hora_inicio,
            "fecha_fin": fecha_fin,
            "total_destinatarios": len(mensajes_validos),
            "message": f"SMS recurrente programado para las {hora_inicio}.",
        })

    except (ValueError, SmsServiceError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger_sms.exception("Error programando SMS recurrente")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== ARRANQUE SCHEDULER SMS ====================


@app.route("/api/sms/operadores", methods=["GET"])
def sms_operadores():
    try:
        return jsonify({
            "success": True, "items": OPERADORES_SMS,
            "message": "Lista de operadores obtenida correctamente"
            })
        
    except Exception as exc:
        return jsonify({
            "success": False, "message": str(exc)
            }), 500


@app.route("/api/sms/tipos-por-operador", methods=["GET"])
def sms_tipos_por_operador():
    try:
        from database import MensajeOperacion
        operador = (request.args.get("operador") or "").strip()
        if not operador:
            return jsonify({"success": False, "message": "Se requiere operador"}), 400
        tipos = MensajeOperacion.query.filter(
            MensajeOperacion.Estado == 1,
            (MensajeOperacion.Operador == operador) | (MensajeOperacion.Operadores.contains(operador))
        ).with_entities(MensajeOperacion.Tipo).distinct().order_by(MensajeOperacion.Tipo).all()
        items = [t[0] for t in tipos if t[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/tipos-por-categoria", methods=["GET"])
def sms_tipos_por_categoria():
    try:
        from database import MensajeOperacion
        categoria = (request.args.get("categoria") or "").strip()
        if not categoria:
            return jsonify({"success": False, "message": "Se requiere categoría"}), 400
        tipos = MensajeOperacion.query.filter(
            MensajeOperacion.Estado == 1, MensajeOperacion.Categoria == categoria
        ).filter(MensajeOperacion.Tipo.isnot(None), MensajeOperacion.Tipo != '') \
            .with_entities(MensajeOperacion.Tipo).distinct().order_by(MensajeOperacion.Tipo).all()
        items = [t[0] for t in tipos if t[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/categorias", methods=["GET"])
def sms_categorias():
    try:
        from database import MensajeOperacion
        categorias = MensajeOperacion.query.filter(
            MensajeOperacion.Estado == 1,
            MensajeOperacion.Categoria.isnot(None), MensajeOperacion.Categoria != ''
        ).with_entities(MensajeOperacion.Categoria).distinct().order_by(MensajeOperacion.Categoria).all()
        items = [c[0] for c in categorias if c[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/categorias-por-operador", methods=["GET"])
def sms_categorias_por_operador():
    try:
        from database import MensajeOperacion
        operador = (request.args.get("operador") or "").strip()
        if not operador:
            return jsonify({"success": False, "message": "Se requiere operador"}), 400
        categorias = MensajeOperacion.query.filter(
            MensajeOperacion.Estado == 1,
            (MensajeOperacion.Operador == operador) | (MensajeOperacion.Operadores.contains(operador)),
            MensajeOperacion.Categoria.isnot(None), MensajeOperacion.Categoria != ''
        ).with_entities(MensajeOperacion.Categoria).distinct().order_by(MensajeOperacion.Categoria).all()
        items = [c[0] for c in categorias if c[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/tipos", methods=["GET"])
def sms_tipos_flexibles():
    try:
        from database import MensajeOperacion
        operador = (request.args.get("operador") or "").strip()
        categoria = (request.args.get("categoria") or "").strip()
        query = MensajeOperacion.query.filter(MensajeOperacion.Estado == 1)
        if operador:
            query = query.filter((MensajeOperacion.Operador == operador) | (MensajeOperacion.Operadores.contains(operador)))
        if categoria:
            query = query.filter(MensajeOperacion.Categoria == categoria)
        tipos = query.filter(MensajeOperacion.Tipo.isnot(None), MensajeOperacion.Tipo != '') \
            .with_entities(MensajeOperacion.Tipo).distinct().order_by(MensajeOperacion.Tipo).all()
        items = [t[0] for t in tipos if t[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/mensajes-operacion", methods=["GET"])
def sms_mensajes_operacion_flexible():
    try:
        from database import MensajeOperacion
        operador = (request.args.get("operador") or "").strip()
        tipo = (request.args.get("tipo") or "").strip()
        categoria = (request.args.get("categoria") or "").strip()
        query = MensajeOperacion.query.filter(MensajeOperacion.Estado == 1)
        if operador:
            query = query.filter((MensajeOperacion.Operador == operador) | (MensajeOperacion.Operadores.contains(operador)))
        if tipo:
            query = query.filter(MensajeOperacion.Tipo == tipo)
        if categoria:
            query = query.filter(MensajeOperacion.Categoria == categoria)
        mensajes = query.order_by(MensajeOperacion.Operador, MensajeOperacion.Tipo, MensajeOperacion.id.desc()).all()
        items = [m.to_dict() for m in mensajes]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - CRUD MENSAJES (SQLite) ====================

@app.route("/config-mensajes")
def config_mensajes():
    return render_template("config_mensajes.html")


@app.route("/api/sms/mensajes", methods=["GET"])
def api_mensajes_list():
    try:
        from database import MensajeOperacion
        mensajes = MensajeOperacion.query.order_by(MensajeOperacion.Operador, MensajeOperacion.Tipo, MensajeOperacion.id.desc()).all()
        return jsonify({"success": True, "items": [m.to_dict() for m in mensajes]})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/mensajes", methods=["POST"])
def api_mensajes_create():
    try:
        from database import MensajeOperacion, db
        data = request.get_json() or {}
        operador = (data.get("operador") or "").strip()
        mensaje_texto = (data.get("mensaje") or "").strip()
        tipo = (data.get("tipo") or "").strip()
        operadores = (data.get("operadores") or "").strip()
        categoria = (data.get("categoria") or "").strip()
        estado = int(data.get("estado", 1))
        if not operador or not mensaje_texto:
            return jsonify({"success": False, "message": "Operador y mensaje son obligatorios"}), 400
        nuevo = MensajeOperacion(Operador=operador, Mensaje=mensaje_texto, Tipo=tipo, Categoria=categoria, Operadores=operadores, Estado=estado)
        db.session.add(nuevo)
        db.session.commit()
        return jsonify({"success": True, "message": "Mensaje creado", "item": nuevo.to_dict()})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/mensajes/<int:mensaje_id>", methods=["PUT"])
def api_mensajes_update(mensaje_id):
    try:
        from database import MensajeOperacion, db
        mensaje = MensajeOperacion.query.get(mensaje_id)
        if not mensaje:
            return jsonify({"success": False, "message": "Mensaje no encontrado"}), 404
        data = request.get_json() or {}
        if "operador" in data: mensaje.Operador = data["operador"].strip()
        if "mensaje" in data: mensaje.Mensaje = data["mensaje"].strip()
        if "tipo" in data: mensaje.Tipo = data["tipo"].strip()
        if "categoria" in data: mensaje.Categoria = data["categoria"].strip()
        if "estado" in data: mensaje.Estado = int(data["estado"])
        if "operadores" in data: mensaje.Operadores = data["operadores"].strip()
        db.session.commit()
        return jsonify({"success": True, "message": "Mensaje actualizado", "item": mensaje.to_dict()})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/mensajes/<int:mensaje_id>", methods=["DELETE"])
def api_mensajes_delete(mensaje_id):
    try:
        from database import MensajeOperacion, db
        mensaje = MensajeOperacion.query.get(mensaje_id)
        if not mensaje:
            return jsonify({"success": False, "message": "Mensaje no encontrado"}), 404
        db.session.delete(mensaje)
        db.session.commit()
        return jsonify({"success": True, "message": "Mensaje eliminado"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/sms/mensajes/<int:mensaje_id>/toggle", methods=["POST"])
def api_mensajes_toggle(mensaje_id):
    try:
        from database import MensajeOperacion, db
        mensaje = MensajeOperacion.query.get(mensaje_id)
        if not mensaje:
            return jsonify({"success": False, "message": "Mensaje no encontrado"}), 404
        mensaje.Estado = 0 if mensaje.Estado == 1 else 1
        db.session.commit()
        return jsonify({"success": True, "message": f"Mensaje {'activado' if mensaje.Estado == 1 else 'desactivado'}", "estado": mensaje.Estado})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500








# ==================== EMAIL - PÁGINA ====================

@app.route("/config-email")
def config_email():
    return render_template("config_email.html")


# ==================== EMAIL - PREVIEW ====================

@app.route("/api/email/preview", methods=["POST"])
def email_preview():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    asunto = (data.get("asunto") or "").strip()

    if not query or not plantilla:
        return jsonify({"success": False, "message": "La consulta SQL y la plantilla son obligatorias."}), 400

    try:
        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response
        from services.email_service import validar_variables_plantilla
        validacion = validar_variables_plantilla(rows, plantilla, asunto)
        if not validacion["valido"]:
            return jsonify({"success": False, "message": validacion["error"], "validacion": validacion}), 400
        result = preview_email(rows, plantilla, asunto)
        if not result.get("success"):
            return jsonify(result), 400
        return jsonify({"success": True, **result})
    except Exception as exc:
        logger.exception("Error en vista previa Email")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== EMAIL - VALIDAR ====================

@app.route("/api/email/validar", methods=["POST"])
def email_validar():
    try:
        data = request.get_json(silent=True) or {}
        query = (data.get("query") or "").strip()
        plantilla = (data.get("plantilla") or "").strip()
        asunto = (data.get("asunto") or "").strip()
        confirmar_reenvio = data.get("confirmar_reenvio", False)

        if not query:
            return jsonify({"success": False, "message": "Consulta SQL requerida"}), 400

        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response

        validacion = validar_destinatarios_email(rows, bq_client, confirmar_reenvio)
        if not validacion.get("success"):
            return jsonify(validacion), 400

        preview = []
        email_column = validacion["email_column"]
        for row in rows[:3]:
            contenido = construir_contenido(row, plantilla)
            asunto_reemplazado = construir_contenido(row, asunto) if asunto else "(Sin asunto)"
            preview.append({
                "email": row.get(email_column, ''),
                "asunto": asunto_reemplazado,
                "contenido": contenido
            })

        return jsonify({"success": True, **validacion, "preview": preview})
    except Exception as exc:
        logger.exception("Error validando email")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== EMAIL - ENVIAR ====================

@app.route("/api/email/enviar", methods=["POST"])
def email_send():
    """Envía emails usando campos personalizados de la API."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    asunto = (data.get("asunto") or "").strip()
    campana_nombre = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    reply_email = (data.get("reply_email") or "").strip()
    confirmar_reenvio = data.get("confirmar_reenvio", False)

    if not query or not plantilla:
        return jsonify({"success": False, "message": "La consulta SQL y la plantilla son obligatorias."}), 400

    try:
        config = (CONFIG or load_config())
        email_config = config.get("email", {})
        api_key = email_config.get("api_key", "")
        from_email = email_config.get("from_email", "")
        from_alias = email_config.get("from_alias", "")

        if not api_key:
            raise EmailServiceError("Configure email.api_key en config.json")
        if not from_email:
            raise EmailServiceError("Configure email.from_email en config.json")

        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response

        email_column = detectar_columna_email(rows)

        # Filtro lista negra y duplicados
        todos_emails = [str(row.get(email_column, "")).strip().lower() for row in rows if row.get(email_column)]
        blacklist = set()
        duplicados = set()

        if todos_emails:
            emails_str = "', '".join(todos_emails)
            try:
                query_black = f"""
                    SELECT DISTINCT LOWER(EMAIL) as email FROM `Tablas_Reporteria.Email_Tutela`
                    WHERE LOWER(EMAIL) IN ('{emails_str}')
                """
                df_black = bq_client.query(query_black).to_dataframe()
                if not df_black.empty:
                    blacklist = set(df_black['email'].tolist())
            except Exception as e:
                logger.warning(f"Error lista negra: {e}")

            if not confirmar_reenvio:
                try:
                    hoy = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    query_dup = f"""
                        SELECT DISTINCT LOWER(email) as email FROM `{EMAIL_LOG_TABLE}`
                        WHERE DATE(fecha_envio) = '{hoy}' AND LOWER(email) IN ('{emails_str}')
                    """
                    df_dup = bq_client.query(query_dup).to_dataframe()
                    if not df_dup.empty:
                        duplicados = set(df_dup['email'].tolist())
                except Exception as e:
                    logger.warning(f"Error duplicados: {e}")

            excluir = blacklist | duplicados
            if excluir:
                rows = [row for row in rows if str(row.get(email_column, "")).strip().lower() not in excluir]

        if not rows:
            return jsonify({
                "success": False,
                "message": "No hay destinatarios después de aplicar filtros.",
                "blacklist": len(blacklist), "duplicados": len(duplicados)
            }), 400

        from services.email_service import validar_variables_plantilla
        validacion = validar_variables_plantilla(rows, plantilla, asunto)
        if not validacion["valido"]:
            return jsonify({"success": False, "message": validacion["error"], "validacion": validacion}), 400

        client = EmailClient(api_key)

        campos_result = client.obtener_campos_personalizados()
        if not campos_result.get("success"):
            raise EmailServiceError("No se pudieron obtener los campos personalizados")

        email_config = config.get("email",{})
        mapeo_campos = email_config.get("field_mapping", {})

        if not mapeo_campos:
        # Usar mapeo por defecto
            mapeo_campos = {
                "customer_name": 1, "nombre": 1, "customer_id": 3, "id_cliente": 3,
                "cuotas": 5, "segmento": 7, "link_pago": 8, "link": 8,
                "campaign_id": 10, "id_campana": 10, "id_mensaje": 11,
                "descripcion_campana": 12, "objetivo_campana": 13,
                "nombre_banco": 14, "nombre_campana": 15,
                "canal_ley": 16, "ley_contacto": 17,
                "id_asunto": 18, "asunto": 19,
                "tel1": 21, "telefono": 21, "celular": 21,
                "valor_pagar": 22, "porcentaje": 23, "fecha_pago": 24,
                "valor_pagar_2": 2, "valor_oferta_esp_2": 4,
                "porcentaje_2": 6, "link_2": 9, "fecha_pago_2": 20,
            }
        for campo in campos_result.get("data", []):
            nombre_original = campo["name"]
            id_campo = campo["id"]
            mapeo_campos[nombre_original.lower()] = id_campo
            mapeo_campos[nombre_original.lower().replace(" ", "_")] = id_campo
            mapeo_campos[nombre_original.lower().replace(" ", "")] = id_campo

        lista_nombre = f"API_Email_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        lista_result = client.crear_lista(lista_nombre)
        if not lista_result.get("success"):
            raise EmailServiceError("No se pudo crear la lista de contactos")
        lista_id = lista_result["data"]["id"]

        contact_ids = []
        emails_enviados = []
        errores = []
        contenido_preview = ""
        asunto_preview = ""

        # 🔥🔥🔥 RECORRER FILAS Y PROCESAR PLANTILLA 🔥🔥🔥
        for i, row in enumerate(rows):
            email = str(row.get(email_column, "")).strip()
            if not email or "@" not in email:
                continue

            # 🔥 PROCESAR PLANTILLA PARA CADA FILA
            contenido_personalizado = construir_contenido(row, plantilla)
            asunto_personalizado = construir_contenido(row, asunto) if asunto else "Sin asunto"
            
            if i == 0:
                contenido_preview = contenido_personalizado
                asunto_preview = asunto_personalizado

            # Mapear valores de la fila a campos personalizados
            campos_personalizados = {}
            for col_name, col_value in row.items():
                if col_value is None or str(col_value).strip() == "":
                    continue
                col_lower = col_name.lower().strip()
                col_guion = col_lower.replace(" ", "_")
                col_sin_espacios = col_lower.replace(" ", "")

                if col_lower in mapeo_campos:
                    campo_id = mapeo_campos[col_lower]
                elif col_guion in mapeo_campos:
                    campo_id = mapeo_campos[col_guion]
                elif col_sin_espacios in mapeo_campos:
                    campo_id = mapeo_campos[col_sin_espacios]
                else:
                    continue
                campos_personalizados[campo_id] = str(col_value).strip()

            contact_result = client.crear_contacto(email, campos_personalizados)

            if contact_result.get("success"):
                contact_id = contact_result.get("data", {}).get("id")
                ya_existe = contact_result.get("ya_existe", False)
                if contact_id:
                    contact_ids.append(contact_id)
                    emails_enviados.append(email)
                elif ya_existe:
                    buscar = client.obtener_contactos(email=email)
                    if buscar.get("success"):
                        encontrados = buscar.get("data", {}).get("data", [])
                        if encontrados:
                            old_id = encontrados[0]["id"]
                            client.eliminar_contactos([old_id])
                            nuevo_result = client.crear_contacto(email, campos_personalizados)
                            if nuevo_result.get("success") and nuevo_result.get("data", {}).get("id"):
                                contact_ids.append(nuevo_result["data"]["id"])
                                emails_enviados.append(email)
                            else:
                                errores.append({"email": email, "error": "No se pudo recrear contacto"})
                        else:
                            errores.append({"email": email, "error": "Contacto no encontrado"})
                    else:
                        errores.append({"email": email, "error": "Error buscando contacto"})
                else:
                    errores.append({"email": email, "error": "No se obtuvo ID"})
            else:
                errores.append({"email": email, "error": str(contact_result.get("error", "Error"))})

        if not contact_ids:
            raise EmailServiceError("No se pudieron crear contactos")

        client.suscribir_contactos(contact_ids, lista_id)

        # ========== 🔥🔥🔥 CREAR CAMPAÑA CON CONTENIDO PROCESADO 🔥🔥🔥 ==========
        from services.email_service import traducir_a_member
        
        # 🔥 IMPORTANTE: Traducir la plantilla ORIGINAL (con {{variables}}) a formato Member
        plantilla_api = traducir_a_member(plantilla, mapeo_campos)
        asunto_api = traducir_a_member(asunto, mapeo_campos) if asunto else ""

        # 🔥 La campaña se crea con la plantilla traducida
        # Infobip reemplazará %Member:CustomFieldX% con los valores de cada contacto
        campana_result = client.crear_campana({
            "name": campana_nombre or f"Email {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "subject": asunto_api or "Sin asunto",
            "fromAlias": from_alias or "QNT",
            "fromEmail": from_email,
            "replyEmail": reply_email or from_email,
            "content": plantilla_api,  # ← Plantilla traducida con %Member:CustomFieldX%
            "mailListsIds": [lista_id]
        })
        
        if not campana_result.get("success"):
            raise EmailServiceError("No se pudo crear la campaña")

        campana_id = campana_result["data"]["id"]
        send_result = client.enviar_campana(campana_id, send_now=1)
        if not send_result.get("success"):
            raise EmailServiceError("No se pudo enviar la campaña")

        # ========== GUARDAR LOGS ==========
        now = datetime.now(timezone.utc).isoformat()
        registros = []
        for email in emails_enviados:
            registros.append({
                "id": str(uuid4()),
                "email": email,
                "asunto": asunto_preview or asunto,
                "contenido": str(contenido_preview)[:1000] if contenido_preview else plantilla[:1000],
                "campana_id": str(campana_id),
                "campana_nombre": campana_nombre or "",
                "fecha_envio": now,
                "resultado": "enviado",
                "bulk_id": str(campana_id),
                "error": "",
                "campana": campana_nombre or "",
                "usuario": usuario or "",
                "fecha_creacion": now,
                "fecha_actualizacion": now
            })

        guardar_email_log(bq_client, registros)
        log_gui_action("Envio Email", campana_id=campana_id, enviados=len(emails_enviados))

        return jsonify({
            "success": True,
            "campana_id": campana_id,
            "enviados": len(emails_enviados),
            "fallidos": len(errores),
            "errores": errores[:10],
            "blacklist_excluidos": len(blacklist),
            "duplicados_excluidos": len(duplicados),
            "message": f"{len(emails_enviados)} emails enviados"
        })
        
    except (EmailServiceError, EmailClientError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error enviando emails")
        return jsonify({"success": False, "message": str(exc)}), 500

# ==================== EMAIL - HISTORIAL ====================

@app.route("/api/email/historial")
def email_history():
    try:
        from google.cloud import bigquery
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
        offset = (page - 1) * per_page

        clauses = []
        parameters = []
        fecha = (request.args.get("fecha") or "").strip()
        if fecha:
            clauses.append("DATE(fecha_envio) = @fecha")
            parameters.append(bigquery.ScalarQueryParameter("fecha", "DATE", fecha))
        campana = (request.args.get("campana") or "").strip()
        if campana:
            clauses.append("LOWER(campana) LIKE LOWER(@campana)")
            parameters.append(bigquery.ScalarQueryParameter("campana", "STRING", f"%{campana}%"))
        email = (request.args.get("email") or "").strip()
        if email:
            clauses.append("email LIKE @email")
            parameters.append(bigquery.ScalarQueryParameter("email", "STRING", f"%{email}%"))

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend([
            bigquery.ScalarQueryParameter("limit", "INT64", per_page),
            bigquery.ScalarQueryParameter("offset", "INT64", offset)
        ])

        query_sql = f"""
            SELECT * FROM `{EMAIL_LOG_TABLE}` {where}
            ORDER BY fecha_envio DESC LIMIT @limit OFFSET @offset
        """
        job_config = bigquery.QueryJobConfig(query_parameters=parameters)
        df = bq_client.query(query_sql, job_config=job_config).to_dataframe()
        items = df.to_dict('records') if not df.empty else []
        return jsonify({"success": True, "page": page, "items": items})
    except Exception as exc:
        logger.exception("Error en historial Email")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== EMAIL - PROGRAMAR ====================

@app.route("/api/email/programar", methods=["POST"])
def email_schedule():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    asunto = (data.get("asunto") or "").strip()
    campana_nombre = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    reply_email = (data.get("reply_email") or "").strip()
    fecha_programada = (data.get("fecha_programada") or "").strip()

    if not query or not plantilla or not fecha_programada:
        return jsonify({"success": False, "message": "Consulta, plantilla y fecha son obligatorias."}), 400

    try:
        run_date = datetime.fromisoformat(fecha_programada)
        if run_date <= datetime.now():
            raise EmailServiceError("La fecha programada debe ser futura.")

        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response

        from services.email_service import validar_variables_plantilla
        validacion = validar_variables_plantilla(rows, plantilla, asunto)
        if not validacion["valido"]:
            return jsonify({"success": False, "message": validacion["error"], "validacion": validacion}), 400

        schedule_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        from google.cloud import bigquery
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", schedule_id),
            bigquery.ScalarQueryParameter("fecha_prog", "TIMESTAMP", run_date.isoformat()),
            bigquery.ScalarQueryParameter("consulta", "STRING", query),
            bigquery.ScalarQueryParameter("plantilla_param", "STRING", plantilla),
            bigquery.ScalarQueryParameter("asunto_param", "STRING", asunto),
            bigquery.ScalarQueryParameter("campana", "STRING", campana_nombre or ""),
            bigquery.ScalarQueryParameter("usuario", "STRING", usuario or ""),
            bigquery.ScalarQueryParameter("reply", "STRING", reply_email or ""),
            bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
        ])

        insert_sql = """
            INSERT INTO `capable-arbor-209819.Temporal.ProgramacionEmail`
            (id, fecha_programada, consulta_sql, plantilla, asunto, campana, estado, usuario, reply_email, fecha_creacion, fecha_actualizacion)
            VALUES (@id, @fecha_prog, @consulta, @plantilla_param, @asunto_param, @campana, 'pendiente', @usuario, @reply, @now, @now)
        """
        bq_client.query(insert_sql, job_config=job_config).result()

        scheduler.add_job(
            execute_email_schedule, trigger="date", run_date=run_date,
            args=[schedule_id], id=f"email_programado_{schedule_id}", replace_existing=True
        )

        return jsonify({"success": True, "id": schedule_id, "fecha_programada": run_date.isoformat(), "message": "Envío programado."})
    except (ValueError, EmailServiceError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error programando email")
        return jsonify({"success": False, "message": str(exc)}), 500


def execute_email_schedule(schedule_id: str):
    """Job de APScheduler que ejecuta una programación de email."""
    try:
        client = bq_client or get_bigquery_client()
        if client is None:
            raise EmailServiceError("No se pudo conectar a BigQuery")

        query = f"""
            SELECT * FROM `capable-arbor-209819.Temporal.ProgramacionEmail`
            WHERE id = '{schedule_id}' AND estado = 'pendiente'
        """
        df = client.query(query).to_dataframe()
        if df.empty:
            return

        prog = df.iloc[0].to_dict()
        config = CONFIG or load_config()
        email_config = config.get("email", {})
        api_key = email_config.get("api_key", "")
        from_email = email_config.get("from_email", "")
        from_alias = email_config.get("from_alias", "")

        fetched = fetch_sms_query_rows(client, prog['consulta_sql'])
        if not fetched.get("success"):
            raise EmailServiceError(fetched.get("message", "Error en consulta"))

        rows = fetched["rows"]
        email_column = detectar_columna_email(rows)
        email_client = EmailClient(api_key)

        campos_result = email_client.obtener_campos_personalizados()
        mapeo_campos = {}
        for campo in campos_result.get("data", []):
            mapeo_campos[campo["name"].lower()] = campo["id"]

        from services.email_service import traducir_a_member
        plantilla_api = traducir_a_member(prog['plantilla'], mapeo_campos)
        asunto_api = traducir_a_member(prog.get('asunto', ''), mapeo_campos)

        lista_nombre = f"API_Sched_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        lista_result = email_client.crear_lista(lista_nombre)
        lista_id = lista_result["data"]["id"]

        contact_ids = []
        for row in rows:
            email = str(row.get(email_column, "")).strip()
            if not email or "@" not in email:
                continue
            campos_personalizados = {}
            for col_name, col_value in row.items():
                if col_value is None or str(col_value).strip() == "":
                    continue
                col_lower = col_name.lower().strip()
                if col_lower in mapeo_campos:
                    campos_personalizados[mapeo_campos[col_lower]] = str(col_value).strip()
            contact_result = email_client.crear_contacto(email, campos_personalizados)
            if contact_result.get("success"):
                cid = contact_result.get("data", {}).get("id")
                if cid:
                    contact_ids.append(cid)
                elif contact_result.get("ya_existe"):
                    buscar = email_client.obtener_contactos(email=email)
                    if buscar.get("success"):
                        encontrados = buscar.get("data", {}).get("data", [])
                        if encontrados:
                            email_client.eliminar_contactos([encontrados[0]["id"]])
                            nuevo = email_client.crear_contacto(email, campos_personalizados)
                            if nuevo.get("success") and nuevo.get("data", {}).get("id"):
                                contact_ids.append(nuevo["data"]["id"])

        if not contact_ids:
            raise EmailServiceError("No se pudieron crear contactos")

        email_client.suscribir_contactos(contact_ids, lista_id)

        campana_result = email_client.crear_campana({
            "name": prog.get('campana') or f"Email Programado {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "subject": asunto_api or "Sin asunto",
            "fromAlias": from_alias or "QNT",
            "fromEmail": from_email,
            "replyEmail": prog.get('reply_email') or from_email,
            "content": plantilla_api,
            "mailListsIds": [lista_id]
        })
        campana_id = campana_result["data"]["id"]
        email_client.enviar_campana(campana_id, send_now=1)

        client.query(f"""
            UPDATE `capable-arbor-209819.Temporal.ProgramacionEmail`
            SET estado = 'enviado', fecha_actualizacion = CURRENT_TIMESTAMP()
            WHERE id = '{schedule_id}'
        """).result()
    except Exception as exc:
        logger.exception(f"Error en programación Email {schedule_id}")
        try:
            client = bq_client or get_bigquery_client()
            if client:
                client.query(f"""
                    UPDATE `capable-arbor-209819.Temporal.ProgramacionEmail`
                    SET estado = 'fallido', fecha_actualizacion = CURRENT_TIMESTAMP()
                    WHERE id = '{schedule_id}'
                """).result()
        except:
            pass


# ==================== EMAIL - PROGRAMACIONES ====================

@app.route("/api/email/programaciones", methods=["GET"])
def email_programaciones():
    try:
        client = bq_client or get_bigquery_client()
        if client is None:
            return jsonify({"success": False, "message": "No se pudo conectar a BigQuery"}), 500

        query = """
            SELECT id, fecha_programada, consulta_sql, plantilla, asunto, campana, usuario, reply_email, estado, fecha_creacion, fecha_actualizacion
            FROM `capable-arbor-209819.Temporal.ProgramacionEmail`
            ORDER BY fecha_programada DESC LIMIT 100
        """
        result = client.query(query).result()
        items = []
        for row in result:
            item = {}
            for key, value in row.items():
                if value is None:
                    item[key] = None
                elif hasattr(value, 'strftime'):
                    item[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                elif hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
                else:
                    item[key] = value
            items.append(item)
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/email/cancelar/<schedule_id>", methods=["POST"])
def email_cancelar_programacion(schedule_id):
    try:
        from google.cloud import bigquery
        client = bq_client or get_bigquery_client()
        if client is None:
            return jsonify({"success": False, "message": "No se pudo conectar a BigQuery"}), 500

        query = """
            UPDATE `capable-arbor-209819.Temporal.ProgramacionEmail`
            SET estado = 'cancelado', fecha_actualizacion = CURRENT_TIMESTAMP()
            WHERE id = @schedule_id AND estado = 'pendiente'
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("schedule_id", "STRING", schedule_id)]
        )
        client.query(query, job_config=job_config).result()
        try:
            scheduler.remove_job(f"email_programado_{schedule_id}")
        except:
            pass
        return jsonify({"success": True, "message": "Programación cancelada"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== AUTO CAMPAIGNS (WOLKVOX) ====================

@app.route("/auto-campaigns", methods=["GET"])
def auto_campaigns_index():
    campaigns = list_auto_campaigns()
    return render_template("auto_campaigns/index.html", campaigns=campaigns)


@app.route("/auto-campaigns/new", methods=["GET"])
def auto_campaigns_new():
    return render_template("auto_campaigns/form.html", **_auto_campaign_form_context())


@app.route("/auto-campaigns", methods=["POST"])
def create_auto_campaign():
    from database import AutoCampaign, db
    data = request.get_json() or {}
    required = ['name', 'wolkvox_campaign_id', 'server_name', 'bigquery_query']
    for field in required:
        if not data.get(field):
            return jsonify({"success": False, "message": f"Falta el campo: {field}"}), 400

    if not data.get('wolkvox_add_record_endpoint'):
        server_mapping = {
            "operacion-interna": "wv0016", "qnt_digital": "wv0016",
            "qnt_juridico_blaster": "wv0016", "qnt_cobro_blaster": "wv0016",
            "Qnt_RBK_blaster": "wv0016", "Qnt_recaudo_blaster": "wv0016",
        }
        server_code = server_mapping.get(data.get('server_name'), "wv0016")
        campaign_id = data.get('wolkvox_campaign_id')
        campaign_type = data.get('campaign_type', 'predictive')
        data['wolkvox_add_record_endpoint'] = f"https://{server_code}.wolkvox.com/api/v2/campaign.php?api=add_record&type_campaign={campaign_type}&campaign_id={campaign_id}&campaign_status=1"

    campaign = AutoCampaign(
        name=data['name'],
        wolkvox_campaign_id=data['wolkvox_campaign_id'],
        server_name=data['server_name'],
        bigquery_query=data['bigquery_query'],
        campaign_type=data.get('campaign_type', 'predictive'),
        wolkvox_add_record_endpoint=data['wolkvox_add_record_endpoint'],
        field_mapping=data.get('field_mapping', {}),
        status=data.get('status', True) if isinstance(data.get('status'), bool) else True,
    )
    db.session.add(campaign)
    db.session.commit()
    return jsonify({"success": True, "campaign": {"id": campaign.id, "name": campaign.name}})


@app.route("/auto-campaigns/<int:campaign_id>", methods=["GET"])
def auto_campaigns_detail(campaign_id):
    campaign = get_auto_campaign(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "No se encontró la campaña."}), 404
    logs = list_execution_logs(campaign_id)
    if request.headers.get("Accept", "").find("application/json") >= 0:
        return jsonify({"success": True, "campaign": campaign, "logs": logs})
    return render_template("auto_campaigns/form.html", **_auto_campaign_form_context(campaign, logs))



@app.route("/auto-campaigns/<int:campaign_id>/report", methods=["GET"])
def auto_campaigns_report(campaign_id):
    from database import AutoCampaignExecutionLog
    execution_id = parse_auto_campaign_id(request.args.get("execution_id"))
    query = AutoCampaignExecutionLog.query.filter_by(auto_campaign_id=campaign_id)
    if execution_id is not None:
        query = query.filter_by(id=execution_id)
    log = query.order_by(AutoCampaignExecutionLog.start_time.desc()).first()
    if not log:
        return jsonify({"success": False, "message": "No hay informes."}), 404
    payload = {
        "execution_id": log.id, "auto_campaign_id": log.auto_campaign_id,
        "start_time": log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else "",
        "end_time": log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else "",
        "records_fetched": log.records_fetched, "records_sent": log.records_sent,
        "records_failed": log.records_failed, "error_message": log.error_message or "",
        "csv_file_path": log.csv_file_path or "", "report_file_path": log.report_file_path or "",
    }
    bio = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(bio, as_attachment=True, download_name=f"auto_campaign_{campaign_id}_{log.id}.json", mimetype="application/json")





@app.route("/auto-campaigns/validate-query-fields", methods=["POST"])
def auto_campaigns_validate_query_fields():
    from services.query_validator import validate_and_normalize, describe_field_aliases, map_column_name

    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400

    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar BigQuery."}), 500

    try:
        query_text = query.strip().rstrip(";")
        if not re.match(r"^(SELECT|WITH)\b", query_text, re.IGNORECASE):
            return jsonify({"success": False, "message": "Solo se permiten consultas SELECT o WITH."}), 400

        job = bq_client.query(query_text)
        result = job.result(max_results=1)
        rows = list(result)
        if not rows:
            return jsonify({"success": False, "message": "La consulta no retorna resultados."}), 400

        success, normalized_rows, error_msg = validate_and_normalize(rows)
        raw_columns = list(rows[0].keys()) if rows else []
        mapped_columns = {}
        for col in raw_columns:
            mapped = map_column_name(col)
            mapped_columns[col] = mapped or "NO_MAPEADO"

        sample_row = None
        if rows:
            try:
                sample_row = dict(rows[0].items())
            except:
                sample_row = {k: str(v) for k, v in rows[0].items()}

        response = {
            "success": success,
            "message": error_msg or "Consulta válida.",
            "detected_columns": raw_columns,
            "column_mapping": mapped_columns,
            "field_aliases": describe_field_aliases(),
            "sample_row": sample_row,
        }
        if not success:
            return jsonify(response), 400
        return jsonify(response)
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


def validar_consulta_wolkvox(query, excluir_duplicados=True):

    # ============================================================
    # 1. EJECUTAR CONSULTA PRINCIPAL
    # ============================================================
    rows = fetch_data_from_bigquery(query)

    if not rows:
        raise ValueError("La consulta no retornó registros.")

    # ============================================================
    # 2. DETECTAR COLUMNA DE TELÉFONO
    # ============================================================
    columnas = list(rows[0].keys())

    telefono_col = None

    for candidata in [
        'tel1',
        'telefono',
        'celular',
        'Celular',
        'movil',
        'phone'
    ]:
        if candidata in columnas:
            telefono_col = candidata
            break

    if not telefono_col:
        raise ValueError(
            f"No se encontró columna de teléfono. "
            f"Columnas disponibles: {columnas}"
        )

    logger.info(
        f"📞 Columna de teléfono detectada: {telefono_col}"
    )

    # ============================================================
    # 3. LISTA NEGRA + VALIDACIÓN
    # ============================================================
    blacklist = get_blacklist_phones()

    registros_validos = []
    registros_bloqueados = []
    registros_invalidos = 0

    for row in rows:

        telefono_raw = str(
            row.get(telefono_col, '')
        ).strip()

        telefono_limpio = re.sub(
            r'[^0-9]',
            '',
            telefono_raw
        )

        # --------------------------------------------------------
        # Teléfono inválido
        # --------------------------------------------------------
        if len(telefono_limpio) < 10:

            registros_invalidos += 1

            continue

        # --------------------------------------------------------
        # Quitar prefijo 57 para comparar
        # --------------------------------------------------------
        if (
            telefono_limpio.startswith('57')
            and len(telefono_limpio) == 12
        ):
            telefono_limpio = telefono_limpio[2:]

        # --------------------------------------------------------
        # Lista negra
        # --------------------------------------------------------
        if telefono_limpio in blacklist:

            registros_bloqueados.append({
                'telefono': telefono_limpio,
                'motivo': 'Lista negra'
            })

            continue

        registros_validos.append(row)

    # ============================================================
    # 4. DUPLICADOS DEL DÍA
    #
    # Tabla:
    # Operacion_Analitica.Embudos_Robot_Advanced
    #
    # IMPORTANTE:
    # Fecha_dia es STRING con formato YYYY-MM-DD
    # ============================================================
    telefonos_duplicados = set()
    registros_duplicados = []

    if registros_validos:

        # --------------------------------------------------------
        # Normalizar teléfonos de la consulta
        # --------------------------------------------------------
        telefonos_limpios = []

        for row in registros_validos:

            telefono_raw = str(
                row.get(telefono_col, '')
            ).strip()

            t_limpio = re.sub(
                r'[^0-9]',
                '',
                telefono_raw
            )

            # Quitar 57
            if (
                t_limpio.startswith('57')
                and len(t_limpio) == 12
            ):
                t_limpio = t_limpio[2:]

            if len(t_limpio) >= 10:

                telefonos_limpios.append(
                    t_limpio
                )

        # --------------------------------------------------------
        # Eliminar repetidos antes del IN
        # --------------------------------------------------------
        telefonos_limpios = list(
            set(telefonos_limpios)
        )

        if telefonos_limpios:

            logger.info(
                f"🔎 Buscando "
                f"{len(telefonos_limpios)} teléfonos "
                f"en Embudos_Robot_Advanced..."
            )

            # ----------------------------------------------------
            # Construir IN
            # ----------------------------------------------------
            telefonos_str = ", ".join(
                f"'{telefono}'"
                for telefono in telefonos_limpios
            )

            # ----------------------------------------------------
            # CONSULTA CORREGIDA
            #
            # Fecha_dia es STRING
            # ----------------------------------------------------
            query_dup = f"""
                SELECT DISTINCT

                    REGEXP_REPLACE(
                        CAST(TELEPHONE AS STRING),
                        r'^57',
                        ''
                    ) AS telefono_limpio

                FROM `capable-arbor-209819.Operacion_Analitica.Embudos_Robot_Advanced`

                WHERE SAFE_CAST(
                    Fecha_dia AS DATE
                ) = CURRENT_DATE('America/Bogota')

                AND REGEXP_REPLACE(
                    CAST(TELEPHONE AS STRING),
                    r'^57',
                    ''
                ) IN ({telefonos_str})
            """

            try:

                df_dup = (
                    bq_client
                    .query(query_dup)
                    .to_dataframe()
                )

                # ------------------------------------------------
                # RESULTADOS ENCONTRADOS
                # ------------------------------------------------
                if not df_dup.empty:

                    telefonos_duplicados = set(
                        df_dup[
                            'telefono_limpio'
                        ]
                        .astype(str)
                        .str.strip()
                        .tolist()
                    )

                    logger.info(
                        f"🔁 Teléfonos únicos encontrados "
                        f"en Advanced: "
                        f"{len(telefonos_duplicados)}"
                    )

                    # ------------------------------------------------
                    # AHORA CONTAMOS LOS REGISTROS DE LA BASE
                    # QUE REALMENTE DEBEN SER EXCLUIDOS
                    # ------------------------------------------------
                    for row in registros_validos:

                        telefono_raw = str(
                            row.get(
                                telefono_col,
                                ''
                            )
                        ).strip()

                        t_limpio = re.sub(
                            r'[^0-9]',
                            '',
                            telefono_raw
                        )

                        # Quitar 57
                        if (
                            t_limpio.startswith('57')
                            and len(t_limpio) == 12
                        ):
                            t_limpio = t_limpio[2:]

                        if (
                            t_limpio
                            in telefonos_duplicados
                        ):

                            registros_duplicados.append({
                                'telefono': t_limpio,

                                'customer_id': str(
                                    row.get(
                                        'customer_id',
                                        ''
                                    )
                                ),

                                'customer_name': str(
                                    row.get(
                                        'customer_name',
                                        ''
                                    )
                                ),
                            })

                    logger.info(
                        f"🚫 Registros que serán excluidos "
                        f"por duplicados: "
                        f"{len(registros_duplicados)}"
                    )

                else:

                    logger.info(
                        "✅ No se encontraron "
                        "duplicados para hoy."
                    )

            except Exception as e:

                logger.error(
                    f"❌ Error consultando duplicados: {e}"
                )

                # IMPORTANTE:
                # Si la consulta falla, NO debemos fingir
                # que hay 0 duplicados.
                #
                # Dejamos la validación en estado de error.
                raise RuntimeError(
                    f"No fue posible validar duplicados "
                    f"contra Embudos_Robot_Advanced: {e}"
                )

    # ============================================================
    # 5. ESTADÍSTICAS
    # ============================================================
    total_consulta = len(rows)

    validos = len(registros_validos)

    invalidos = registros_invalidos

    lista_negra = len(registros_bloqueados)

    # Cantidad REAL de registros que se eliminarán
    duplicados = len(registros_duplicados)

    # ============================================================
    # 6. TOTAL A ENVIAR
    # ============================================================
    if excluir_duplicados:

        a_enviar = validos - duplicados

    else:

        a_enviar = validos

    a_enviar = max(0, a_enviar)

    # ============================================================
    # 7. LOG FINAL
    # ============================================================
    logger.info(
        f"📊 Validación Wolkvox: "
        f"total={total_consulta} | "
        f"válidos={validos} | "
        f"inválidos={invalidos} | "
        f"lista_negra={lista_negra} | "
        f"duplicados={duplicados} | "
        f"a_enviar={a_enviar} | "
        f"excluir_dup={excluir_duplicados}"
    )

    # ============================================================
    # 8. RETORNAR RESULTADO
    # ============================================================
    return {
        "success": True,
        "rows": rows,
        "telefono_col": telefono_col,
        "registros_validos": registros_validos,
        "registros_bloqueados": registros_bloqueados,
        "registros_duplicados": registros_duplicados,
        "telefonos_duplicados": telefonos_duplicados,
        "total_consulta": total_consulta,
        "validos": validos,
        "invalidos": invalidos,
        "duplicados": duplicados,
        "lista_negra": lista_negra,
        "a_enviar": a_enviar,
    }




def numero_a_letras_es(numero):
    """
    Convierte un número entero a su representación en letras en español.
    Soporta hasta 999,999,999,999 (miles de millones).

    Ejemplos:
        100000     -> "cien mil"
        407815     -> "cuatrocientos siete mil ochocientos quince"
        989597     -> "novecientos ochenta y nueve mil quinientos noventa y siete"
        1500000    -> "un millón quinientos mil"
    """
    if numero is None:
        return ""

    # Convertir a entero si viene como string o float
    try:
        numero = int(float(str(numero).replace(',', '').replace('.', '').strip()))
    except (ValueError, TypeError):
        return ""

    if numero == 0:
        return "cero"

    if numero < 0:
        return "menos " + numero_a_letras_es(abs(numero))

    # ═══ Diccionarios base ═══
    UNIDADES = [
        "", "uno", "dos", "tres", "cuatro", "cinco",
        "seis", "siete", "ocho", "nueve", "diez",
        "once", "doce", "trece", "catorce", "quince",
        "dieciséis", "diecisiete", "dieciocho", "diecinueve",
        "veinte", "veintiuno", "veintidós", "veintitrés",
        "veinticuatro", "veinticinco", "veintiséis",
        "veintisiete", "veintiocho", "veintinueve"
    ]
    DECENAS = [
        "", "", "", "treinta", "cuarenta", "cincuenta",
        "sesenta", "setenta", "ochenta", "noventa"
    ]
    CENTENAS = [
        "", "ciento", "doscientos", "trescientos", "cuatrocientos",
        "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"
    ]

    def _convertir_centenas(n):
        """Convierte de 0 a 999."""
        if n == 0:
            return ""
        if n == 100:
            return "cien"
        if n < 30:
            return UNIDADES[n]

        c = n // 100
        resto = n % 100

        if c == 0:
            d = n // 10
            u = n % 10
            if u == 0:
                return DECENAS[d]
            return f"{DECENAS[d]} y {UNIDADES[u]}"

        resultado = CENTENAS[c]
        if resto > 0:
            if resto < 30:
                resultado += f" {UNIDADES[resto]}"
            else:
                d = resto // 10
                u = resto % 10
                if u == 0:
                    resultado += f" {DECENAS[d]}"
                else:
                    resultado += f" {DECENAS[d]} y {UNIDADES[u]}"
        return resultado

    def _apocope(texto):
        """Ajusta 'uno' -> 'un' antes de 'mil' o 'millón'."""
        if texto.endswith("uno"):
            return texto[:-3] + "un"
        if texto == "uno":
            return "un"
        return texto

    # ═══ Casos ═══
    if numero < 1000:
        return _convertir_centenas(numero)

    if numero < 1_000_000:
        miles = numero // 1000
        resto = numero % 1000
        if miles == 1:
            texto = "mil"
        else:
            texto = _apocope(_convertir_centenas(miles)) + " mil"
        if resto > 0:
            texto += " " + _convertir_centenas(resto)
        return texto

    if numero < 1_000_000_000:
        millones = numero // 1_000_000
        resto = numero % 1_000_000
        if millones == 1:
            texto = "un millón"
        else:
            texto = _apocope(_convertir_centenas(millones)) + " millones"
        if resto > 0:
            texto += " " + numero_a_letras_es(resto)
        return texto

    if numero < 1_000_000_000_000:
        miles_millones = numero // 1_000_000_000
        resto = numero % 1_000_000_000
        if miles_millones == 1:
            texto = "mil millones"
        else:
            texto = _apocope(_convertir_centenas(miles_millones)) + " mil millones"
        if resto > 0:
            texto += " " + numero_a_letras_es(resto)
        return texto

    return str(numero)

def Cargue_Wolkvox(campaign, token, excluir_duplicados=True):
    """
    Formatea registros y envía a Wolkvox en lotes.
    Limpia la campaña antes de cargar para evitar acumulación.

    Args:
        campaign: objeto de campaña
        token: token de Wolkvox
        excluir_duplicados: si True, filtra teléfonos ya enviados hoy
    """
    from backend import limpiar_campana_wolkvox_por_id
    from datetime import datetime
    import json as _json

    # 1. Validar consulta
    validacion = validar_consulta_wolkvox(
        campaign.bigquery_query,
        excluir_duplicados=excluir_duplicados
    )
    registros_validos = validacion["registros_validos"]
    telefono_col = validacion["telefono_col"]

    if not registros_validos:
        raise ValueError("Todos los registros fueron bloqueados por lista negra.")

    # 1.2 Filtrar duplicados si aplica
    if excluir_duplicados:
        telefonos_dup = validacion.get("telefonos_duplicados", set())
        if telefonos_dup:
            antes = len(registros_validos)
            registros_filtrados = []
            for r in registros_validos:
                t = re.sub(r'[^0-9]', '', str(r.get(telefono_col, '')).strip())
                if t.startswith('57') and len(t) == 12:
                    t = t[2:]
                if t not in telefonos_dup:
                    registros_filtrados.append(r)
            registros_validos = registros_filtrados
            logger.info(
                f"🚫 Filtrados {antes - len(registros_validos)} duplicados. "
                f"Quedan {len(registros_validos)} registros."
            )
            if not registros_validos:
                raise ValueError("Todos los registros fueron excluidos por duplicados.")

    # 1.3 Limpiar campaña antes de cargar
    logger.info(f"🧹 Limpiando campaña {campaign.wolkvox_campaign_id} en {campaign.server_name}...")
    limpieza_ok = limpiar_campana_wolkvox_por_id(
        server_name=campaign.server_name,
        campaign_id=campaign.wolkvox_campaign_id,
        token=token,
    )
    if not limpieza_ok:
        logger.warning(f"⚠️ No se pudo limpiar campaña {campaign.wolkvox_campaign_id}")

    # ═══════════ HELPERS ═══════════
    def formatear_telefono(telefono):
        telefono = re.sub(r'[^0-9]', '', str(telefono))
        return f"{telefono}"

    def _clean(val, default=''):
        if val is None:
            return default
        s = str(val).strip()
        return default if s in ('nan', 'None', 'NaT', 'NoneType', '') else s

    def normalizar_gender(val):
        s = _clean(val).upper()
        if s.startswith('M') or s in ('MASCULINO', 'HOMBRE', 'MALE'):
            return "M"
        if s.startswith('F') or s in ('FEMENINO', 'MUJER', 'FEMALE'):
            return "F"
        return "O"

    def formatear_recall_date(val):
        s = _clean(val)
        if s and s.isdigit() and len(s) == 14:
            return s
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:len(fmt) + 2], fmt).strftime("%Y%m%d%H%M%S")
            except (ValueError, TypeError):
                continue
        return datetime.now().strftime("%Y%m%d%H%M%S")

    # 2. Formatear registros
    records = []
    for idx, row in enumerate(registros_validos):
        Contaco__c = _clean(row.get('Contaco__c', ''))
        if not Contaco__c:
            Contaco__c = _clean(row.get('tel1', '')) or f"CLI-{idx}"

        telefono_formateado = formatear_telefono(row.get('tel1', ''))
        Name = _clean(row.get('Name', '')) or 'Sin Nombre'

        saldo_raw = row.get('Saldo_Capital_cliente', '')
        opt7_valor = _clean(saldo_raw)
        opt11_valor = numero_a_letras_es(saldo_raw)

        valor_oferta = row.get('AcuVrTotalAcuerdo', '')
        opt4_valor = _clean(valor_oferta)
        opt12_valor = numero_a_letras_es(valor_oferta)

        apellido = _clean(row.get('customer_last_name', ''))
        MailPreferente = _clean(row.get('MailPreferente', ''))

        record = {
            "customer_name": Name,
            "customer_last_name": apellido,
            "id_type": "CC",
            "customer_id": Contaco__c,
            "tel1": telefono_formateado,
            "tel2": _clean(row.get('Telefono_2', '')),
            "tel3": _clean(row.get('Telefono_3', '')),
            "tel4": _clean(row.get('Telefono_Whatsapp', '')),
            "tel5": "",
            "tel6": "", "tel7": "", "tel8": "", "tel9": "", "tel10": "",
            "tel_extra": "",
            "email": MailPreferente,

            "age":     _clean(row.get('AcuDiaPagoCuotaMensual', '')),
            "gender":  normalizar_gender(row.get('AcuFechaCuota1', '')),
            "country": _clean(row.get('AcuFuenteDeIngresos', '')) or "COL",
            "state":   _clean(row.get('AcuGacsPorcentaje', '')),
            "city":    _clean(row.get('AcuGacsPorcentajeIva', '')),
            "zone":    _clean(row.get('AcuGacsValorTotal', '')),
            "address": _clean(row.get('AcuMotivoMora', '')),

            "opt1":  _clean(row.get('AcuPlazoAceptado', '')),
            "opt2":  _clean(row.get('AcuVrCuota1', '')),
            "opt3":  _clean(row.get('AcuVrCuotaMensual', '')),
            "opt4":  opt4_valor,
            "opt5":  _clean(row.get('Ubicacion_Contacto__c', '')),
            "opt6":  _clean(row.get('UbicacionName__c', '')),
            "opt7":  opt7_valor,
            "opt8":  _clean(row.get('Fecha_Gestion__c', '')),
            "opt9":  _clean(row.get('OportunityProducts', '')),
            "opt10": _clean(row.get('Skill_TELEAMIGO', '')),
            "opt11": opt11_valor,
            "opt12": opt12_valor,

            "recall_date":      formatear_recall_date(row.get('recall_date', '')),
            "recall_telephone": formatear_telefono(
                                    _clean(row.get('recall_telephone', ''))
                                ),
        }
        records.append(record)

    # 3. Señuelos
    senuelos_data_ = []
    start_id = len(records) + 1
    for i, (nombre_s, telefono_s, customer_id_s) in enumerate(senuelos_data_, start=start_id):
        senuelo = {
            "customer_name": nombre_s,
            "customer_last_name": "",
            "id_type": "CC",
            "customer_id": customer_id_s,
            "tel1": formatear_telefono(telefono_s),
            "tel2": "", "tel3": "", "tel4": "", "tel5": "",
            "tel6": "", "tel7": "", "tel8": "", "tel9": "", "tel10": "",
            "tel_extra": "", "email": "",
            "age": "30", "gender": "O", "country": "COL",
            "state": "", "city": "", "zone": "", "address": "",
            "opt1": "", "opt2": "", "opt3": "", "opt4": "",
            "opt5": "", "opt6": "", "opt7": "", "opt8": "", "opt9": "",
            "opt10": "", "opt11": "", "opt12": "",
            "recall_date": datetime.now().strftime("%Y%m%d%H%M%S"),
            "recall_telephone": formatear_telefono(telefono_s),
        }
        records.append(senuelo)

    # 4. URL Wolkvox
    server_mapping = {
        "operacion-interna": "https://wv0016.wolkvox.com",
        "qnt_digital": "https://wv0010.wolkvox.com/",
        "qnt_juridico_blaster": "https://wv0016.wolkvox.com",
        "qnt_cobro_blaster": "https://wv0016.wolkvox.com",
        "Qnt_RBK_blaster": "https://wv0016.wolkvox.com",
        "Qnt_recaudo_blaster": "https://wv0016.wolkvox.com",
    }
    server_url = server_mapping.get(campaign.server_name, "https://wv0016.wolkvox.com")
    url = f"{server_url}/api/v2/campaign.php"
    params = {
        "api": "add_record",
        "type_campaign": campaign.campaign_type or "predictive",
        "campaign_id": campaign.wolkvox_campaign_id,
        "campaign_status": "1"
    }

    if records:
        logger.info(f"📤 PAYLOAD PRIMER REGISTRO:\n{_json.dumps(records[0], indent=2, ensure_ascii=False, default=str)}")

    # 5. Enviar en lotes
    headers = {"wolkvox-token": token, "Content-Type": "application/json"}
    batch_size = 100
    total_enviados = 0
    errores = []

    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        try:
            response = requests.post(url, params=params, headers=headers, json=batch, timeout=60)
            logger.info(f"📥 Wolkvox HTTP {response.status_code}: {response.text[:1500]}")

            if response.status_code in [200, 201]:
                total_enviados += len(batch)
                logger.info(f"✅ Lote {i//batch_size + 1}: {len(batch)} registros enviados")
            else:
                errores.append({"status": response.status_code, "response": response.text[:500]})
                logger.warning(f"❌ Lote {i//batch_size + 1}: HTTP {response.status_code}")
        except Exception as e:
            errores.append({"error": str(e)})
            logger.warning(f"❌ Lote {i//batch_size + 1}: Error {str(e)}")

    # 6. Guardar WolkvoxLog
    if records:
        try:
            guardar_wolkvox_log(bq_client, records, campaign, usuario='sistema')
        except Exception as e:
            logger.warning(f"Error guardando WolkvoxLog: {e}")

    return {
        "success": len(errores) == 0,
        "records_sent": total_enviados,
        "records_fetched": validacion["total_consulta"],
        "records_blocked": validacion["lista_negra"],
        "records_failed": len(records) - total_enviados,
        "duplicados": validacion["duplicados"],
        "invalidos": validacion["invalidos"],
        "errors": errores,
        "limpieza_previa": limpieza_ok,
        "message": f"{total_enviados} registros cargados. {validacion['lista_negra']} bloqueados."
    }

@app.route("/api/wolkvox/validar", methods=["POST"])
def wolkvox_validar():
    """Valida consulta y devuelve estadísticas (con soporte de exclusión de duplicados)."""
    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar BigQuery."}), 500
    
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    excluir_duplicados = bool(data.get("excluir_duplicados", True))  
    
    if not query:
        return jsonify({"success": False, "message": "Consulta SQL requerida"}), 400
    
    try:
        resultado = validar_consulta_wolkvox(query, excluir_duplicados=excluir_duplicados)
        return jsonify({
            "success": True,
            "total_consulta": resultado["total_consulta"],
            "validos": resultado["validos"],
            "invalidos": resultado["invalidos"],
            "duplicados": resultado["duplicados"],
            "lista_negra": resultado["lista_negra"],
            "a_enviar": resultado["a_enviar"],
            "excluir_duplicados": excluir_duplicados,  
        })
    except Exception as exc:
        logger.exception("Error validando consulta Wolkvox")
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/auto-campaigns/<int:campaign_id>/load-wkv", methods=["POST"])
def auto_campaigns_load_wkv(campaign_id):
    """Carga registros manualmente a Wolkvox."""
    from database import AutoCampaign, AutoCampaignExecutionLog, db
    from auto_campaign_executor import _get_token

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "Campaña no encontrada."}), 404

    token = _get_token(campaign)
    if not token:
        return jsonify({"success": False, "message": "No se encontró token Wolkvox."}), 400

    # 🆕 Leer el flag del body
    data = request.get_json(silent=True) or {}
    excluir_duplicados = bool(data.get("excluir_duplicados", True))

    log = AutoCampaignExecutionLog(
        auto_campaign_id=campaign.id,
        start_time=datetime.now(timezone.utc)
    )
    db.session.add(log)
    db.session.commit()

    try:
        resultado = Cargue_Wolkvox(
            campaign, token,
            excluir_duplicados=excluir_duplicados   # 🆕
        )

        log.records_fetched = resultado.get("records_fetched", 0)
        log.records_sent = resultado.get("records_sent", 0)
        log.records_failed = resultado.get("records_failed", 0)
        log.end_time = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify(resultado)

    except Exception as e:
        db.session.rollback()
        log.end_time = datetime.now(timezone.utc)
        log.error_message = str(e)
        db.session.commit()
        return jsonify({"success": False, "message": str(e)}), 500



    
#============= Programcion Simple ==================#
@app.route("/auto-campaigns/programar", methods=["POST"])
def campaigns_schedule_simple():
    """Programa un envío UNA sola vez con datos del formulario."""
    from database import ProgramacionCampana, db

    # ═══════════════════════════════════════════════════════════════
    # LOG 1: Entró la petición
    # ═══════════════════════════════════════════════════════════════
    logger_programacion.info("=" * 70)
    logger_programacion.info("🟢 [SIMPLE-1] Petición recibida en /auto-campaigns/programar")

    data = request.get_json(silent=True) or {}
    logger_programacion.info(f"🟢 [SIMPLE-1] Payload crudo: {data}")

    # 1. Leer TODOS los datos del formulario
    nombre = (data.get("nombre") or "").strip()
    bigquery_query = (data.get("bigquery_query") or "").strip()
    wolkvox_campaign_id = (data.get("wolkvox_campaign_id") or "").strip()
    server_name = (data.get("server_name") or "").strip()
    fecha_programada = (data.get("fecha_programada") or "").strip()
    hora_terminar = (data.get("hora_fin") or "").strip()

    # ═══════════════════════════════════════════════════════════════
    # LOG 2: Campos leídos
    # ═══════════════════════════════════════════════════════════════
    logger_programacion.info(
        f"🟢 [SIMPLE-2] Campos leídos:\n"
        f"     nombre               = '{nombre}'\n"
        f"     wolkvox_campaign_id  = '{wolkvox_campaign_id}'\n"
        f"     server_name          = '{server_name}'\n"
        f"     fecha_programada     = '{fecha_programada}'\n"
        f"     hora_fin             = '{hora_terminar}'\n"
        f"     query (primeros 80)  = '{bigquery_query[:80]}...'"
    )

    # 2. Validar campos obligatorios
    if not nombre:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: nombre vacío")
        return jsonify({"success": False, "message": "El nombre es obligatorio."}), 400
    if not bigquery_query:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: query vacía")
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400
    if not wolkvox_campaign_id:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: campaign_id vacío")
        return jsonify({"success": False, "message": "El Campaign ID es obligatorio."}), 400
    if not server_name:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: server_name vacío")
        return jsonify({"success": False, "message": "El servidor es obligatorio."}), 400
    if not fecha_programada:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: fecha_programada vacía")
        return jsonify({"success": False, "message": "La fecha programada es obligatoria."}), 400
    if not hora_terminar:
        logger_programacion.warning("🔴 [SIMPLE-3] Validación: hora_fin vacía")
        return jsonify({"success": False, "message": "No hay una fecha para terminar la programación"}), 400

    logger_programacion.info("🟢 [SIMPLE-3] Validaciones OK")

    try:
        # ═══════════════════════════════════════════════════════════
        # LOG 4: Parseo de fecha
        # ═══════════════════════════════════════════════════════════
        logger_programacion.info(f"🟢 [SIMPLE-4] Parseando fecha: '{fecha_programada}'")
        try:
            run_date = datetime.fromisoformat(fecha_programada)
            logger_programacion.info(f"🟢 [SIMPLE-4] Fecha parseada: {run_date} | tzinfo={run_date.tzinfo}")
        except ValueError as e:
            logger_programacion.error(f"🔴 [SIMPLE-4] Error parseando fecha: {e}")
            return jsonify({"success": False, "message": f"Formato de fecha inválido: {e}"}), 400

        # Normalizar SIEMPRE a Colombia
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=COLOMBIA_TZ)
            logger_programacion.info(f"🟢 [SIMPLE-4] Sin tzinfo → asignado COLOMBIA_TZ: {run_date}")
        else:
            run_date_original = run_date
            run_date = run_date.astimezone(COLOMBIA_TZ)
            logger_programacion.info(f"🟢 [SIMPLE-4] Con tzinfo → normalizado a Colombia: {run_date_original} → {run_date}")

        # ═══════════════════════════════════════════════════════════
        # LOG 5: Parseo de hora_fin
        # ═══════════════════════════════════════════════════════════
        logger_programacion.info(f"🟢 [SIMPLE-5] Parseando hora_fin: '{hora_terminar}'")
        try:
            hora_fin = datetime.strptime(hora_terminar, "%H:%M").strftime("%H:%M")
            logger_programacion.info(f"🟢 [SIMPLE-5] hora_fin normalizada: '{hora_fin}'")
        except ValueError as e:
            logger_programacion.error(f"🔴 [SIMPLE-5] Error parseando hora_fin: {e}")
            return jsonify({
                "success": False,
                "message": "Formato de hora de finalización inválido. Use HH:MM."
            }), 400

        ahora = datetime.now(COLOMBIA_TZ)

        # ═══════════════════════════════════════════════════════════
        # LOG 6: Datos listos para guardar
        # ═══════════════════════════════════════════════════════════
        logger_programacion.info(
            f"🟢 [SIMPLE-6] Datos listos para guardar:\n"
            f"     ahora             = {ahora.isoformat()}\n"
            f"     run_date (Colombia) = {run_date.isoformat()}\n"
            f"     hora_inicio       = {run_date.strftime('%H:%M')}\n"
            f"     hora_fin          = {hora_fin}\n"
            f"     fecha_programada ISO = {run_date.isoformat()}"
        )

        # ═══════════════════════════════════════════════════════════
        # LOG 7: Crear registro en DB
        # ═══════════════════════════════════════════════════════════
        logger_programacion.info("🟢 [SIMPLE-7] Creando ProgramacionCampana...")
        nueva = ProgramacionCampana(
            nombre=nombre,
            bigquery_query=bigquery_query,
            wolkvox_campaign_id=wolkvox_campaign_id,
            server_name=server_name,
            tipo_programacion='simple',
            fecha_programada=run_date,
            hora_inicio=run_date.strftime('%H:%M'),
            hora_fin=hora_fin,
            fecha_creacion=ahora,
            estado='pendiente'
        )
        db.session.add(nueva)
        db.session.commit()

        logger_programacion.info(
            f"🟢 [SIMPLE-7] ✅ Registro guardado en DB:\n"
            f"     ID             = {nueva.id}\n"
            f"     estado         = {nueva.estado}\n"
            f"     hora_inicio    = {nueva.hora_inicio}\n"
            f"     hora_fin       = {nueva.hora_fin}\n"
            f"     fecha_programada = {nueva.fecha_programada}"
        )

        # ═══════════════════════════════════════════════════════════
        # LOG 8: Registrar job en APScheduler
        # ═══════════════════════════════════════════════════════════
        job_id = f"wolkvox_simple_{nueva.id}"
        logger_programacion.info(f"🟢 [SIMPLE-8] Registrando job '{job_id}' con run_date={run_date.isoformat()}")

        try:
            job = scheduler.add_job(
                execute_wolkvox_schedule,
                trigger="date",
                run_date=run_date,
                id=job_id,
                replace_existing=True,
            )
            logger_programacion.info(
                f"🟢 [SIMPLE-8] ✅ Job registrado:\n"
                f"     job.id        = {job.id}\n"
                f"     next_run_time = {job.next_run_time if hasattr(job, 'next_run_time') else 'N/A'}\n"
                f"     trigger       = {job.trigger}"
            )
        except Exception as e:
            logger_programacion.exception(f"🔴 [SIMPLE-8] Error registrando job: {e}")
            # No hacemos rollback porque el registro en DB ya quedó

        # ═══════════════════════════════════════════════════════════
        # LOG 9: Jobs actualmente activos en el scheduler
        # ═══════════════════════════════════════════════════════════
        try:
            jobs_actuales = scheduler.get_jobs()
            logger_programacion.info(
                f"🟢 [SIMPLE-9] Jobs activos en el scheduler ({len(jobs_actuales)}):"
            )
            for j in jobs_actuales:
                next_run = getattr(j, 'next_run_time', 'N/A')
                logger_programacion.info(f"     - {j.id} | next_run={next_run} | trigger={j.trigger}")
        except Exception as e:
            logger_programacion.warning(f"🟡 [SIMPLE-9] No se pudo listar jobs: {e}")

        logger_programacion.info(
            f"✅ [SIMPLE-END] Campaña {nueva.id} creada y programada para {run_date.isoformat()}"
        )

        return jsonify({
            "success": True,
            "id": nueva.id,
            "tipo_programacion": "simple",
            "fecha_programada": run_date.isoformat(),
            "message": "Programación simple creada."
        })

    except Exception as exc:
        db.session.rollback()
        logger_programacion.exception("🔴 [SIMPLE-ERROR] Error programando Wolkvox simple")
        return jsonify({"success": False, "message": str(exc)}), 500
    
    
    
    
# ==================== PROGRAMAR RECURRENTE ====================

@app.route("/auto-campaigns/programar-recurrente", methods=["POST"])
def campaigns_schedule_recurrent():
    """Programa un envío recurrente diario con datos del formulario."""
    from database import ProgramacionCampana, db

    data = request.get_json(silent=True) or {}

    nombre = (data.get("nombre") or "").strip()
    bigquery_query = (data.get("bigquery_query") or "").strip()
    wolkvox_campaign_id = (data.get("wolkvox_campaign_id") or "").strip()
    server_name = (data.get("server_name") or "").strip()
    campaign_type = (data.get("campaign_type") or "predictive").strip()
    hora_inicio = (data.get("hora_inicio") or "").strip()
    fecha_fin = (data.get("fecha_fin") or "").strip() or None

    # 2. Validar campos obligatorios
    if not nombre:
        return jsonify({"success": False, "message": "El nombre es obligatorio."}), 400
    if not bigquery_query:
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400
   
    if not server_name:
        return jsonify({"success": False, "message": "El servidor es obligatorio."}), 400
    if not hora_inicio:
        return jsonify({"success": False, "message": "La hora de envío es obligatoria."}), 400
    if not re.match(r'^\d{2}:\d{2}$', hora_inicio):
        return jsonify({"success": False, "message": "Formato de hora inválido. Use HH:MM."}), 400

    if not wolkvox_campaign_id:
        return jsonify({"success": False, "message": "El Campaign ID es obligatorio."}), 400

    try:
        nueva = ProgramacionCampana(
            nombre=nombre,
            bigquery_query=bigquery_query,
            wolkvox_campaign_id=wolkvox_campaign_id,
            server_name=server_name,
            tipo_programacion='recurrente',
            hora_inicio=hora_inicio,
            fecha_fin=fecha_fin,
            estado='pendiente'
        )
        db.session.add(nueva)
        db.session.commit()

        scheduler.add_job(
            execute_wolkvox_schedule,
            trigger="interval",
            minutes=10,
            id=f"wolkvox_recurrente_{nueva.id}",
            replace_existing=True
        )

        logger_programacion.info(f"Programación recurrente creada para ID: {nueva.id}")
        return jsonify({"success": True, "id": nueva.id, "message": "Programación recurrente creada."})
    except Exception as exc:
        db.session.rollback()
        logger_programacion.exception("Error programando Wolkvox recurrente")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SCHEDULER ====================
def execute_wolkvox_schedule():
    """Busca y ejecuta todas las programaciones pendientes de Wolkvox."""
    from database import ProgramacionCampana, db

    with app.app_context():
        try:
            pendientes = ProgramacionCampana.query.filter_by(estado='pendiente').all()

            if not pendientes:
                return

            ahora = datetime.now(COLOMBIA_TZ)
            hoy_str = ahora.strftime("%Y-%m-%d")
            hora_actual = ahora.strftime("%H:%M")

            for prog in pendientes:
                try:
                    # ================== RECURRENTE ==================
                    if prog.tipo_programacion == 'recurrente':
                        if hora_actual < prog.hora_inicio:
                            continue

                        if prog.fecha_ejecucion:
                            fe_ejec = prog.fecha_ejecucion.strftime("%Y-%m-%d")
                            if fe_ejec == hoy_str:
                                continue

                        if prog.fecha_fin and hoy_str > prog.fecha_fin:
                            prog.estado = 'completado'
                            db.session.commit()
    
                            continue

                        logger_programacion.info(f"Procesando programación {prog.id}: {prog.nombre} (tipo: {prog.tipo_programacion})")

                    # ==================== SIMPLE ====================
                    elif prog.tipo_programacion == 'simple':
                        logger_programacion.info(f"Procesando campaña simple {prog.id}: {prog.nombre}")
                        # COND 1: ¿Ya pasó la hora fin? -> limpiar SIEMPRE,
                        # se haya ejecutado o no.
                        if prog.hora_fin and hora_actual >= prog.hora_fin:
                            token_clear = _get_token_desde_server(prog.server_name)
                            if token_clear:
                                try:
                                    base_url = _get_base_url_wolkvox(prog.server_name)
                                    clear_url = f"{base_url}/api/v2/campaign.php"
                                    clear_params = {
                                        "api": "clear_campaign",
                                        "type_campaign": "predictive",
                                        "campaign_id": prog.wolkvox_campaign_id,
                                    }
                                    clear_resp = requests.delete(
                                        clear_url, params=clear_params,
                                        headers={"wolkvox-token": token_clear},
                                        timeout=60,
                                    )
                                    logger_programacion.info(f"Campaña {prog.wolkvox_campaign_id} limpiada: HTTP {clear_resp.status_code}")
                                    if not clear_resp.ok:
                                        logger.warning(
                                            f"No se pudo limpiar campaña {prog.wolkvox_campaign_id} "
                                            f"(prog {prog.id}): HTTP {clear_resp.status_code}"
                                        )
                                except Exception:
                                    logger.exception(
                                        f"Error limpiando campaña de programación {prog.id}"
                                    )
                            else:
                                logger.warning(f"Sin token para limpiar programación {prog.id}")

                            prog.estado = 'completado'
                            db.session.commit()

                            try:
                                scheduler.remove_job(f"wolkvox_simple_{prog.id}")
                            except Exception:
                                pass
                            continue

                        # COND 2: ¿Ya se ejecutó? -> no volver a ejecutar,
                        # solo esperar a que llegue hora_fin para limpiar.
                        if prog.fecha_ejecucion:
                            logger_programacion.info(f"Campaña simple {prog.id} ya ejecutada a las {prog.fecha_ejecucion}")

                            continue
                        logger_programacion.info(f"Campaña simple {prog.id}: hora_inicio={prog.hora_inicio}, hora_fin={prog.hora_fin}, hora_actual={hora_actual}, fecha_ejecucion={prog.fecha_ejecucion}")
                        # COND 3: ¿Ya llegó la hora de inicio?
                        if hora_actual < prog.hora_inicio:
                            continue
                        logger_programacion.info(f"Campaña simple {prog.id} lista para ejecutar: hora_inicio={prog.hora_inicio}, hora_fin={prog.hora_fin}, hora_actual={hora_actual}")
                    else:
                        logger_programacion.info(f"Procesando campaña simple  {prog.id}: {prog.nombre} (tipo: {prog.tipo_programacion})")

                    logger_programacion.info(f"Ejecutando campaña simple {prog.id}: {prog.nombre} (tipo: {prog.tipo_programacion})")

                     
                    # 3. Obtener token
                    token = _get_token_desde_server(prog.server_name)
                    if not token:
                        logger_programacion.warning(f"Sin token para ejecutar campaña simple {prog.id}")
                        continue  # reintenta en el próximo barrido

                    # 4. Crear objeto temporal
                    class CampaignWrapper:
                        pass

                    campaign = CampaignWrapper()
                    campaign.id = prog.id
                    campaign.name = prog.nombre
                    campaign.bigquery_query = prog.bigquery_query
                    campaign.wolkvox_campaign_id = prog.wolkvox_campaign_id
                    campaign.server_name = prog.server_name
                    campaign.campaign_type = 'predictive'

                    # 5. Ejecutar carga (UNA sola vez, para ambos tipos)
                    logger_programacion.info(f"📤 Ejecutando campaña simple {prog.id}: {prog.nombre}")
                    resultado = Cargue_Wolkvox(campaign, token)

                    # 6. Actualizar registro
                    prog.fecha_ejecucion = ahora
                    prog.total_destinatarios = resultado.get("records_sent", 0)
                    prog.fecha_actualizacion = datetime.now(COLOMBIA_TZ)
                    logger_programacion.info(f"📊 Campaña simple {prog.id} actualizada: {prog.total_destinatarios} destinatarios")

                    if prog.tipo_programacion == 'recurrente':
                        
                        prog.estado = 'enviado' if resultado.get("success") else 'fallido'
                  
                    db.session.commit()

                except Exception as e:
                    logger.exception(f"Error con campaña simple {prog.id}")
                    if prog.tipo_programacion == 'recurrente':
                        prog.estado = 'fallido'
                        db.session.commit()
                    
        except Exception as exc:
            logger.exception("Error en scheduler Wolkvox")

scheduler.add_job(
    execute_wolkvox_schedule,
    trigger=IntervalTrigger(seconds=60),
    id="wolkvox_schedule_scanner",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=120,
)

import requests
from flask import request, jsonify
from datetime import datetime

# ============================================
# FUNCIONES AUXILIARES
# ============================================

import requests
from flask import request, jsonify

# ============================================
# FUNCIONES AUXILIARES (compartidas)


def _get_base_url_wolkvox(server_name: str) -> str:
    """Obtiene la URL base de Wolkvox para un servidor."""
    from backend import get_server
    
    try:
        srv = get_server(server_name)
    except Exception:
        srv = None
    
    if srv:
        prefix = (srv.get("url") or "").strip().rstrip("/")
        if prefix.lower().startswith("http"):
            return prefix
        return f"https://wv{prefix}.wolkvox.com"
    else:
        if server_name.lower().startswith("http"):
            return server_name.rstrip("/")
        return f"https://wv{server_name}.wolkvox.com"

def _ejecutar_accion_wolkvox(campaign_id, server_name, accion, metodo_http):
    """
    Función genérica para ejecutar acciones en Wolkvox.
    
    Args:
        campaign_id: ID de la campaña en Wolkvox
        server_name: Nombre del servidor
        accion: 'start', 'stop', 'clear_campaign'
        metodo_http: 'POST', 'PUT', 'DELETE'
    """
    # Validar server_name
    if not server_name:
        return {"success": False, "message": "Se requiere server_name"}, 400
    
    # Obtener token
    token = _obtener_token_servidor(server_name)
    if not token:
        return {"success": False, "message": f"Token no encontrado para {server_name}"}, 400
    
    # Construir URL
    base_url = _get_base_url_wolkvox(server_name)
    url = f"{base_url}/api/v2/campaign.php?api={accion}&campaign_id={campaign_id}"
    
    # Ejecutar petición
    try:
        response = requests.request(
            method=metodo_http,
            url=url,
            headers={"wolkvox-token": token},
            timeout=60
        )
        
        if response.ok:
            return {"success": True, "message": f"Campaña {campaign_id} {accion.replace('_', 'da ')}"}, 200
        else:
            return {"success": False, "message": f"Error HTTP {response.status_code}"}, response.status_code
    except requests.Timeout:
        return {"success": False, "message": f"Timeout en {server_name}"}, 504
    except Exception as e:
        return {"success": False, "message": str(e)}, 500


# ============================================
# ENDPOINTS (solo lo esencial)
# ============================================

@app.route("/api/campaigns/<string:campaign_id>/start", methods=["POST"])
def api_campaigns_start(campaign_id):
    data = request.get_json() or {}
    server_name = data.get("server_name", "").strip()
    result, status = _ejecutar_accion_wolkvox(campaign_id, server_name, "start", "POST")
    return jsonify(result), status


@app.route("/api/campaigns/<string:campaign_id>/stop", methods=["POST"])
def api_campaigns_stop(campaign_id):
    data = request.get_json() or {}
    server_name = data.get("server_name", "").strip()
    result, status = _ejecutar_accion_wolkvox(campaign_id, server_name, "stop", "PUT")
    return jsonify(result), status


@app.route("/api/campaigns/<string:campaign_id>/clear", methods=["DELETE"])
def api_campaigns_clear(campaign_id):
    data = request.get_json() or {}
    server_name = data.get("server_name", "").strip()
    result, status = _ejecutar_accion_wolkvox(campaign_id, server_name, "clear_campaign", "DELETE")
    return jsonify(result), status



# ================== LISTAR PROGRAMACIONES (API) ==================
@app.route("/api/programaciones", methods=["GET"])
def list_programacion():
    from database import ProgramacionCampana, db

    query = ProgramacionCampana.query

    estado = request.args.get("estado")
    if estado:
        query = query.filter_by(estado=estado)

    tipo = request.args.get("tipo_programacion")
    if tipo:
        query = query.filter_by(tipo_programacion=tipo)

    programaciones = query.order_by(ProgramacionCampana.id.desc()).all()

    data = []
    for prog in programaciones:
        data.append({
            'id': prog.id,
            'nombre': prog.nombre,
            'campana_id': prog.campana_id,
            'bigquery_query': prog.bigquery_query,
            'wolkvox_campaign_id': prog.wolkvox_campaign_id,
            'server_name': prog.server_name,
            'tipo_programacion': prog.tipo_programacion,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'hora_fin': prog.hora_fin,
            'fecha_fin': prog.fecha_fin,
            'estado': prog.estado,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'total_destinatarios': prog.total_destinatarios,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        })

    return jsonify({
        "success": True,
        "total": len(data),
        "data": data
    }), 200
# ================== VER UNA ==================

@app.route("/auto-campaigns/programaciones/<int:prog_id>", methods=["GET"])
def get_programacion(prog_id):
    from database import ProgramacionCampana

    prog = ProgramacionCampana.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "Programación no encontrada"}), 404

    # ✅ Serializar a diccionario según tu modelo
    return jsonify({
        "success": True,
        "data": {
            'id': prog.id,
            'nombre': prog.nombre,
            'campana_id': prog.campana_id,
            'bigquery_query': prog.bigquery_query,
            'wolkvox_campaign_id': prog.wolkvox_campaign_id,
            'server_name': prog.server_name,
            'tipo_programacion': prog.tipo_programacion,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'hora_fin': prog.hora_fin,
            'fecha_fin': prog.fecha_fin,
            'estado': prog.estado,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'total_destinatarios': prog.total_destinatarios,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        }
    }), 200


# ================== EDITAR ==================

@app.route("/auto-campaigns/programaciones/<int:prog_id>", methods=["PATCH"])
def editar_programacion(prog_id):
    from database import ProgramacionCampana, db
    from datetime import datetime
    import re

    prog = ProgramacionCampana.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "Programación no encontrada"}), 404

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"success": False, "error": "JSON inválido o vacío"}), 400

    # Columnas válidas de tu modelo
    columnas_validas = {
        'nombre', 'campana_id', 'bigquery_query', 'wolkvox_campaign_id',
        'server_name', 'tipo_programacion', 'fecha_programada', 'hora_inicio',
        'hora_fin', 'fecha_fin', 'estado', 'total_destinatarios'
    }

    campos_ignorados = []
    campos_actualizados = []

    for campo, valor in body.items():
        if campo not in columnas_validas:
            campos_ignorados.append(campo)
            continue
        
        # 🔥 CONVERTIR fecha_programada de string a datetime
        if campo == 'fecha_programada' and valor:
            try:
                if isinstance(valor, str):
                    # Intentar formato ISO (2026-09-01T16:51)
                    if 'T' in valor:
                        valor = datetime.fromisoformat(valor)
                    else:
                        valor = datetime.strptime(valor, "%Y-%m-%d %H:%M:%S")
                # Si ya es datetime, usarlo tal cual
            except (ValueError, TypeError) as e:
                return jsonify({
                    "success": False, 
                    "error": f"Formato de fecha inválido: {valor}. Use formato ISO (YYYY-MM-DDTHH:MM)"
                }), 400
        elif campo == 'fecha_programada' and not valor:
            # Si viene vacío o null, mantenerlo como None
            valor = None
        
        # Validar formato de hora_fin (HH:MM)
        if campo == 'hora_fin' and valor:
            if not re.match(r'^\d{2}:\d{2}$', valor):
                return jsonify({
                    "success": False, 
                    "error": f"Formato de hora inválido: {valor}. Use HH:MM"
                }), 400
        
        setattr(prog, campo, valor)
        campos_actualizados.append(campo)

    if not campos_actualizados:
        return jsonify({
            "success": False,
            "error": "No se envió ningún campo válido para actualizar",
            "campos_ignorados": campos_ignorados,
        }), 400

    try:
        # Actualizar fecha_actualizacion
        prog.fecha_actualizacion = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error editando programación {prog_id}")
        return jsonify({"success": False, "error": str(e)}), 500

    logger.info(f"✏️ Programación {prog_id} actualizada: {campos_actualizados}")

    return jsonify({
        "success": True,
        "campos_actualizados": campos_actualizados,
        "campos_ignorados": campos_ignorados,
        "data": {
            'id': prog.id,
            'nombre': prog.nombre,
            'campana_id': prog.campana_id,
            'bigquery_query': prog.bigquery_query,
            'wolkvox_campaign_id': prog.wolkvox_campaign_id,
            'server_name': prog.server_name,
            'tipo_programacion': prog.tipo_programacion,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'hora_fin': prog.hora_fin,
            'fecha_fin': prog.fecha_fin,
            'estado': prog.estado,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'total_destinatarios': prog.total_destinatarios,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        }
    }), 200

@app.route("/auto-campaigns/programaciones/<int:prog_id>", methods=["DELETE"])
def eliminar_programacion(prog_id):
    from database import ProgramacionCampana, db

    prog = ProgramacionCampana.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "Programación no encontrada"}), 404

    # Si tiene job en scheduler, lo removemos
    try:
        scheduler.remove_job(f"wolkvox_simple_{prog.id}")
    except Exception:
        pass
    try:
        scheduler.remove_job(f"wolkvox_recurrente_{prog.id}")
    except Exception:
        pass

    try:
        db.session.delete(prog)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error eliminando programación {prog_id}")
        return jsonify({"success": False, "error": str(e)}), 500

    logger.info(f"🗑️ Programación {prog_id} eliminada")

    return jsonify({"success": True, "message": f"Programación {prog_id} eliminada"}), 200




#=========Informacion Campañas Wolkvox=======================#
@app.route("/api/servers/<server_name>/url", methods=["GET"])
def get_server_url(server_name):
    try:
        server = get_server(server_name)
        if server:
            url = (server.get('url') or '').strip().rstrip('/')
            if url and not url.startswith(('http://', 'https://')):
                url = f'https://wv{url}.wolkvox.com'
            return jsonify({"success": True, "url": url})
        return jsonify({"success": False, "message": "Servidor no encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
def _get_token_desde_server(server_name):
    """Obtiene token de Wolkvox desde config.json según server_name."""
    from backend import get_authorization_headers, load_config
    
    load_config()
    headers = get_authorization_headers(server_name or None)
    return headers.get("wolkvox-token") or ""



# ==================== funcion para guardar logs en Wolkvox ====================
def guardar_wolkvox_log(client, records, campaign, usuario='sistema', resultado='enviado'):
    """Guarda cada registro enviado a Wolkvox en BigQuery."""
    from google.cloud import bigquery
    
    if not records:
        return
    
    now = datetime.now(COLOMBIA_TZ).isoformat()
    batch_size = 200
    total_guardados = 0
    
    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        
        insert_sql = """
        INSERT INTO `capable-arbor-209819.Temporal.WolkvoxLog`
        (id, telefono, customer_id, customer_name, customer_last_name, email,
         fecha_pago, fecha_pago_2, valor_pagar, valor_pagar_2,
         valor_oferta_esp, valor_oferta_esp_2, cuotas, porcentaje, porcentaje_2,
         segmento, link_pago, id_campana_origen, id_mensaje,
         descripcion_campana, objetivo_campana, nombre_banco, nombre_campana,
         canal_ley, ley_contacto, empresa, nit_empresa, direccion_cliente,
         valor_capital, valor_total, campana_id, campana_nombre,
         usuario, resultado, error, fecha_carga)
        VALUES
        """
        
        value_rows = []
        parameters = []
        
        for idx, record in enumerate(batch):
            value_rows.append(f"""
                (@id_{idx}, @telefono_{idx}, @customer_id_{idx}, @customer_name_{idx},
                 @customer_last_name_{idx}, @email_{idx}, @fecha_pago_{idx}, @fecha_pago_2_{idx},
                 @valor_pagar_{idx}, @valor_pagar_2_{idx}, @valor_oferta_esp_{idx},
                 @valor_oferta_esp_2_{idx}, @cuotas_{idx}, @porcentaje_{idx},
                 @porcentaje_2_{idx}, @segmento_{idx}, @link_pago_{idx},
                 @id_campana_origen_{idx}, @id_mensaje_{idx}, @descripcion_campana_{idx},
                 @objetivo_campana_{idx}, @nombre_banco_{idx}, @nombre_campana_{idx},
                 @canal_ley_{idx}, @ley_contacto_{idx}, @empresa_{idx},
                 @nit_empresa_{idx}, @direccion_cliente_{idx}, @valor_capital_{idx},
                 @valor_total_{idx}, @campana_id_{idx}, @campana_nombre_{idx},
                 @usuario_{idx}, @resultado_{idx}, @error_{idx}, @fecha_carga_{idx})
            """)
            
            parameters.extend([
                bigquery.ScalarQueryParameter(f"id_{idx}", "STRING", str(uuid4())),
                bigquery.ScalarQueryParameter(f"telefono_{idx}", "STRING", record.get('tel1', '')),
                bigquery.ScalarQueryParameter(f"customer_id_{idx}", "STRING", record.get('customer_id', '')),
                bigquery.ScalarQueryParameter(f"customer_name_{idx}", "STRING", record.get('customer_name', '')),
                bigquery.ScalarQueryParameter(f"customer_last_name_{idx}", "STRING", record.get('customer_last_name', '')),
                bigquery.ScalarQueryParameter(f"email_{idx}", "STRING", record.get('email', '')),
                bigquery.ScalarQueryParameter(f"fecha_pago_{idx}", "STRING", record.get('opt1', '')),
                bigquery.ScalarQueryParameter(f"fecha_pago_2_{idx}", "STRING", record.get('opt5', '')),
                bigquery.ScalarQueryParameter(f"valor_pagar_{idx}", "STRING", record.get('opt2', '')),
                bigquery.ScalarQueryParameter(f"valor_pagar_2_{idx}", "STRING", record.get('opt6', '')),
                bigquery.ScalarQueryParameter(f"valor_oferta_esp_{idx}", "STRING", record.get('opt7', '')),
                bigquery.ScalarQueryParameter(f"valor_oferta_esp_2_{idx}", "STRING", record.get('opt8', '')),
                bigquery.ScalarQueryParameter(f"cuotas_{idx}", "STRING", record.get('opt9', '')),
                bigquery.ScalarQueryParameter(f"porcentaje_{idx}", "STRING", record.get('opt10', '')),
                bigquery.ScalarQueryParameter(f"porcentaje_2_{idx}", "STRING", record.get('opt11', '')),
                bigquery.ScalarQueryParameter(f"segmento_{idx}", "STRING", record.get('opt3', '')),
                bigquery.ScalarQueryParameter(f"link_pago_{idx}", "STRING", record.get('opt12', '')),
                bigquery.ScalarQueryParameter(f"id_campana_origen_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"id_mensaje_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"descripcion_campana_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"objetivo_campana_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"nombre_banco_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"nombre_campana_{idx}", "STRING", campaign.name if campaign else ''),
                bigquery.ScalarQueryParameter(f"canal_ley_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"ley_contacto_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"empresa_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"nit_empresa_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"direccion_cliente_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"valor_capital_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"valor_total_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"campana_id_{idx}", "STRING", str(campaign.id) if campaign else ''),
                bigquery.ScalarQueryParameter(f"campana_nombre_{idx}", "STRING", campaign.name if campaign else ''),
                bigquery.ScalarQueryParameter(f"usuario_{idx}", "STRING", usuario),
                bigquery.ScalarQueryParameter(f"resultado_{idx}", "STRING", resultado),
                bigquery.ScalarQueryParameter(f"error_{idx}", "STRING", ''),
                bigquery.ScalarQueryParameter(f"fecha_carga_{idx}", "TIMESTAMP", now),
            ])
        
        insert_sql += ", ".join(value_rows)
        job_config = bigquery.QueryJobConfig(query_parameters=parameters)
        
        client.query(insert_sql, job_config=job_config).result()
        total_guardados += len(batch)
    
    logger.info(f"✅ {total_guardados} registros guardados en WolkvoxLog")

# ==================== DASHBOARD ====================

@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"success": True, **get_dashboard_data()})


#https://wv0016.wolkvox.com/api/v2/real_time.php?api=campaigns
# == Listar informacion de las campañas de wolkvox=====#
@app.route("/api/dashboard/refresh", methods=["POST"])
def api_dashboard_refresh():
    from backend import total_campañas_hoy
    
    try:
        datos_campañas = total_campañas_hoy()
        campañas = datos_campañas.get("campañas", [])
        
        # Calcular estadísticas
        total_campanas = len(campañas)
        total_clientes = sum(int(c.get("records", 0)) for c in campañas)
        total_llamados = sum(int(c.get("dial", 0)) for c in campañas)
        total_contactados = sum(int(c.get("answer", 0)) for c in campañas)
        total_pendientes = total_clientes - total_contactados
        llamadas_x_minuto = sum(int(c.get("calls_x_min", 0)) for c in campañas)
        
        porcentaje = 0
        if total_clientes > 0:
            porcentaje = round((total_contactados / total_clientes) * 100, 1)
        
        # Procesar campañas para la tabla
        campañas_procesadas = []
        for c in campañas:
            nombre_completo = c.get("campaign", "Sin nombre")
            camp_id = nombre_completo.split("-")[0].strip() if "-" in nombre_completo else nombre_completo
            
            estado = c.get("status", "desconocido")
            estado_label = {
                "started": "En curso",
                "stopped": "Detenida",
                "paused": "Pausada",
                "finished": "Terminada"
            }.get(estado, estado.capitalize())
            
            campañas_procesadas.append({
                "nombre": nombre_completo,
                "id": camp_id,  # ← Este es el ID de Wolkvox (ej: "20717")
                "servidor": c.get("servidor", "No asignado"),
                "tipo": "Predictivo",
                "estado": estado,
                "estado_label": estado_label,
                "records": int(c.get("records", 0) or 0),
                "dial": int(c.get("dial", 0) or 0),
                "answer": int(c.get("answer", 0) or 0),
                "clean": int(c.get("clean", 0) or 0),
                "clientes": int(c.get("records", 0) or 0),
                "llamados": int(c.get("dial", 0) or 0),
                "contactados": int(c.get("answer", 0) or 0),
                "llamadas_x_minuto": int(c.get("calls_x_min", 0) or 0),
                "faltantes": int(c.get("records", 0) or 0) - int(c.get("answer", 0) or 0)
            })
        
        data = {
            "campañas": campañas_procesadas,
            "servidores_count": len(datos_campañas.get("servidores", [])),
            "porcentaje_progreso": f"{porcentaje}%",
            "clientes_contactados": total_contactados,
            "clientes_pendientes": total_pendientes,
            "total_campanas": total_campanas,
            "total_clientes": total_clientes,
            "total_llamados": total_llamados,
            "llamadas_x_minuto": llamadas_x_minuto
        }
        
        return jsonify({
            "success": True,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(exc)}), 500
















@app.route("/api/recent_logs", methods=["GET"])
def api_recent_logs():
    return jsonify(read_recent_log_lines(200))


@app.route("/downloads/<filename>", methods=["GET"])
def download_file(filename):
    try:
        return send_from_directory(str(DOWNLOAD_FOLDER), filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": "Archivo no encontrado"}), 404





















# ================== RUTAS PARA LOS NUEVOS TEMPLATES ==================

@app.route("/auto-campaigns/form")
def auto_campaigns_form():
    """Página para crear/editar campaña"""
    campaign_id = request.args.get("id")
    campaign = None
    if campaign_id:
        from database import AutoCampaign
        campaign = AutoCampaign.query.get(int(campaign_id))

    result = load_servers()
    servers = result.get("servers", []) if result.get("success") else []
    return render_template("auto_campaigns/form.html", campaign=campaign, servers=servers)

@app.route("/auto-campaigns/programar")
def auto_campaigns_programar():
    """Página para programar envíos"""
    result = load_servers()
    servers = result.get("servers", []) if result.get("success") else []
    return render_template("auto_campaigns/programar.html", servers=servers)

@app.route("/auto-campaigns/ver_programaciones")
def auto_campaigns_programaciones():
    """Página para ver todas las programaciones"""
    result = load_servers()
    servers = result.get("servers", []) if result.get("success") else []
    return render_template("auto_campaigns/programaciones.html", servers=servers)
# ================== API PROGRAMACIONES ==================

@app.route("/api/programaciones", methods=["GET"])
def api_list_programaciones():
    """API para listar programaciones (JSON)"""
    from database import ProgramacionCampana

    query = ProgramacionCampana.query

    estado = request.args.get("estado")
    if estado:
        query = query.filter_by(estado=estado)

    tipo = request.args.get("tipo_programacion")
    if tipo:
        query = query.filter_by(tipo_programacion=tipo)

    programaciones = query.order_by(ProgramacionCampana.id.desc()).all()

    data = []
    for prog in programaciones:
        data.append({
            'id': prog.id,
            'nombre': prog.nombre,
            'campana_id': prog.campana_id,
            'bigquery_query': prog.bigquery_query,
            'wolkvox_campaign_id': prog.wolkvox_campaign_id,
            'server_name': prog.server_name,
            'tipo_programacion': prog.tipo_programacion,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'hora_fin': prog.hora_fin,
            'fecha_fin': prog.fecha_fin,
            'estado': prog.estado,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'total_destinatarios': prog.total_destinatarios,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        })

    return jsonify({
        "success": True,
        "total": len(data),
        "data": data
    }), 200



# ================== RUTAS PARA LOS TEMPLATES ==================

@app.route("/api/sms/editar", methods=["POST", "GET"])
def auto_campaigns_forms():
    """Página para crear/editar campaña"""
    sms_id = request.args.get("id")
    campana = None
    if sms_id:
        from database import ProgramacionSms
        campana = ProgramacionSms.query.get(int(sms_id))

    result = load_servers()
    usuario = result.get("usuario", []) if result.get("success") else []
    return render_template("sms/Programaiones.html",
                           campana=campana,
                           usuario=usuario
                          )


@app.route("/api/sms/programars")
def auto_campaigns_programars():
    """Página para programar envíos"""
    result = load_servers()
    servers = result.get("servers", []) if result.get("success") else []
    return render_template("auto_campaigns/programar.html", servers=servers)


@app.route("/api/sms/ver_programaciones")
def auto_campaigns_programacioness():
    """Página para ver todas las programaciones"""
    result = load_servers()
    servers = result.get("servers", []) if result.get("success") else []
    return render_template("sms/programaciones.html", servers=servers)


# ================== API PROGRAMACIONES ==================

@app.route("/api/sms/programaciones", methods=["GET"])
def api_list_programaciones_sms():
    """API para listar programaciones de SMS (JSON) desde SQLite."""
    from database import ProgramacionSms

    estado = request.args.get("estado")
    tipo = request.args.get("tipo_programacion")
    query = ProgramacionSms.query
    if estado:
        query = query.filter_by(estado=estado)
    if tipo:
        query = query.filter_by(tipo_programacion=tipo)

    programaciones = query.order_by(ProgramacionSms.id.desc()).all()

    data = []
    for prog in programaciones:
        data.append({
            'id': prog.id,
            'nombre': prog.campana,
            'bigquery_query': prog.consulta_sql,
            'tipo_programacion': prog.tipo_programacion,
            'consulta_sql': prog.consulta_sql,
            'plantilla': prog.plantilla,
            'campana': prog.campana,
            'usuario': prog.usuario,
            'estado': prog.estado,
            'total_destinatarios': prog.total_destinatarios,
            'confirmar_reenvio': prog.confirmar_reenvio,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'fecha_fin': prog.fecha_fin,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        })
    logger.info(f"📄 Listando {len(data)} programaciones de SMS")

    return jsonify({
        "success": True,
        "total": len(data),
        "data": data
    }), 200


@app.route("/api/sms/programaciones/<int:prog_id>", methods=["GET"])
def api_get_programacion(prog_id):
    from database import ProgramacionSms
    prog = ProgramacionSms.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "No encontrada"}), 404
    logger.info(f"📄 Obteniendo programación de SMS con ID {prog_id}")
    return jsonify({
        "success": True,
        "data": {
            'id': prog.id,
            'nombre': prog.campana,
            'bigquery_query': prog.consulta_sql,
            'tipo_programacion': prog.tipo_programacion,
            'consulta_sql': prog.consulta_sql,
            'plantilla': prog.plantilla,
            'campana': prog.campana,
            'usuario': prog.usuario,
            'estado': prog.estado,
            'total_destinatarios': prog.total_destinatarios,
            'confirmar_reenvio': prog.confirmar_reenvio,
            'fecha_programada': prog.fecha_programada.isoformat() if prog.fecha_programada else None,
            'hora_inicio': prog.hora_inicio,
            'fecha_fin': prog.fecha_fin,
            'fecha_ejecucion': prog.fecha_ejecucion.isoformat() if prog.fecha_ejecucion else None,
            'fecha_creacion': prog.fecha_creacion.isoformat() if prog.fecha_creacion else None,
            'fecha_actualizacion': prog.fecha_actualizacion.isoformat() if prog.fecha_actualizacion else None,
        }
    }), 200


@app.route("/api/sms/programaciones/<int:prog_id>", methods=["PATCH"])
def api_update_programacion(prog_id):
    from database import ProgramacionSms, db
    prog = ProgramacionSms.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "No encontrada"}), 404

    data = request.get_json() or {}
    logger.info(f"📄 Actualizando programación de SMS con ID {prog_id}")
    campos = [
        'tipo_programacion', 'consulta_sql', 'plantilla',
        'campana', 'usuario', 'estado', 'total_destinatarios',
        'confirmar_reenvio', 'hora_inicio', 'fecha_fin'
    ]
    for campo in campos:
        if campo in data:
            setattr(prog, campo, data[campo])

    if 'fecha_programada' in data and data['fecha_programada']:
        try:
            prog.fecha_programada = datetime.fromisoformat(
                data['fecha_programada'].replace('Z', '+00:00')
            )
        except Exception as e:
            return jsonify({"success": False, "error": f"Fecha inválida: {e}"}), 400
    elif 'fecha_programada' in data:
        prog.fecha_programada = None

    try:
        db.session.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sms/programaciones/<int:prog_id>", methods=["DELETE"])
def api_delete_programacion(prog_id):
    from database import ProgramacionSms, db
    prog = ProgramacionSms.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "No encontrada"}), 404
    logger.info(f"📄 Eliminando programación de SMS con ID {prog_id}")
    try:
        db.session.delete(prog)
        db.session.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sms/programaciones/<int:prog_id>/cancelar", methods=["POST"])
def api_cancelar_programacion(prog_id):
    logger.info(f"📄 Cancelando programación de SMS con ID {prog_id}")
    from database import ProgramacionSms, db
    prog = ProgramacionSms.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "No encontrada"}), 404
    prog.estado = 'cancelado'
    try:
        db.session.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sms/programaciones/<int:prog_id>/reejecutar", methods=["POST"])
def api_reejecutar_programacion(prog_id):
    from database import ProgramacionSms, db
    prog = ProgramacionSms.query.get(prog_id)
    if not prog:
        return jsonify({"success": False, "error": "No encontrada"}), 404
    prog.estado = 'pendiente'
    prog.fecha_ejecucion = None
    try:
        db.session.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500



# ==================== MAIN ====================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    init_bigquery()
    app.run(debug=True, host="0.0.0.0", port=5000)
