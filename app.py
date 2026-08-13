from datetime import datetime, date, timezone
from flask import jsonify, render_template, request, send_from_directory, send_file
from uuid import uuid4
import io
import csv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.filters import AutoFilter
from excel_report_builder import build_wolkvox_excel, _safe_filename
import json
import requests
import re
import pandas as pd

from backend import (
    app, db, scheduler, execute_pending_tasks, LOG_FILE,
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
from auto_campaigns import (
    create_auto_campaign, delete_auto_campaign, get_auto_campaign,
    list_auto_campaigns, list_execution_logs, parse_auto_campaign_id,
    update_auto_campaign,
)
from auto_campaign_executor import (
    is_auto_campaign_running, request_stop_auto_campaign,
    start_auto_campaign_async,
)
from services.email_client import EmailClient, EmailClientError
from services.email_service import (
    EMAIL_LOG_TABLE, EmailServiceError, detectar_columna_email,
    construir_contenido, preview_email, guardar_email_log, extraer_variables,
)

# ==================== CONFIGURACIÓN GLOBAL ====================
PROJECT_ID = "capable-arbor-209819"
bq_client = None


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


@app.context_processor
def inject_admin_ui():
    return {"current_endpoint": request.endpoint or ""}


















# ==================== SMS - PÁGINA ====================

@app.route("/config-sms")
def config_sms():
    return render_template("config_sms.html")


# ==================== SMS - PREVIEW ====================

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


# ==================== SMS - PROGRAMAR ====================

@app.route("/api/sms/programar", methods=["POST"])
def sms_schedule():
    from datetime import timezone

    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    plantilla = (data.get("plantilla") or "").strip()
    when = (data.get("fecha_programada") or "").strip()
    campaign = (data.get("campana") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    allow_resend = bool(data.get("confirmar_reenvio", False))

    es_recurrente = bool(data.get("es_recurrente", False))
    frecuencia_tipo = data.get("frecuencia_tipo") if es_recurrente else None
    franja_horaria = data.get("franja_horaria") if es_recurrente else None
    fecha_limite = data.get("fecha_limite") if es_recurrente else None
    repeticiones_max = data.get("repeticiones_max") if es_recurrente else None

    hora_inicio = None
    hora_fin = None
    frecuencia_valor = None
    frecuencia_unidad = None

    if es_recurrente:
        if franja_horaria == 'mañana':
            hora_inicio, hora_fin = '07:00', '12:00'
        elif franja_horaria == 'tarde':
            hora_inicio, hora_fin = '15:00', '18:00'
        elif franja_horaria == 'noche':
            hora_inicio, hora_fin = '18:00', '21:00'

        if frecuencia_tipo == 'diario':
            frecuencia_valor = 1
            frecuencia_unidad = 'dias'
        elif frecuencia_tipo == 'semanal':
            frecuencia_valor = 7
            frecuencia_unidad = 'dias'
        elif frecuencia_tipo == 'mensual':
            frecuencia_valor = 30
            frecuencia_unidad = 'dias'

    if not query or not plantilla or not when:
        return jsonify({"success": False, "message": "Consulta, plantilla y fecha programada son obligatorias."}), 400

    try:
        run_date = datetime.fromisoformat(when)
        ahora_utc = datetime.now(timezone.utc)
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=timezone.utc)
        if run_date <= ahora_utc:
            raise SmsServiceError("La fecha programada debe ser futura.")
        if es_recurrente and not fecha_limite and not repeticiones_max:
            raise SmsServiceError("Define una fecha límite o máximo de repeticiones.")

        rows, error_response = _get_sms_rows(query)
        if error_response:
            return error_response
        mensajes_validos, _ = aplicar_validaciones(rows, plantilla, bq_client, allow_resend=allow_resend)
        if not mensajes_validos:
            raise SmsServiceError("La programación no tiene destinatarios válidos.")

        schedule_id = guardar_programacion(
            bq_client, query=query, plantilla=plantilla,
            campaign=campaign, usuario=usuario,
            scheduled_at=run_date.isoformat(), allow_resend=allow_resend,
            total_dest=len(mensajes_validos),
            es_recurrente=es_recurrente, frecuencia_tipo=frecuencia_tipo,
            frecuencia_valor=frecuencia_valor, frecuencia_unidad=frecuencia_unidad,
            franja_horaria=franja_horaria, hora_inicio=hora_inicio,
            hora_fin=hora_fin, fecha_limite=fecha_limite,
            repeticiones_max=repeticiones_max,
        )

        scheduler.add_job(
            execute_sms_schedule, trigger="date", run_date=run_date,
            args=[schedule_id], id=f"sms_programado_{schedule_id}", replace_existing=True
        )

        tipo = "recurrente" if es_recurrente else "simple"
        response = {
            "success": True, "id": schedule_id,
            "fecha_programada": run_date.isoformat(),
            "total_destinatarios": len(mensajes_validos),
            "es_recurrente": es_recurrente,
            "message": f"Envío SMS programado ({tipo})."
        }
        if es_recurrente:
            response.update({
                "frecuencia_tipo": frecuencia_tipo,
                "franja_horaria": franja_horaria,
                "fecha_limite": fecha_limite,
                "repeticiones_max": repeticiones_max,
            })
        return jsonify(response)
    except (ValueError, SmsServiceError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error programando SMS")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - EJECUTAR PROGRAMACIÓN ====================

def execute_sms_schedule(schedule_id: str):
    """Job de APScheduler que ejecuta una programación y reprograma si es recurrente."""
    try:
        from google.cloud import bigquery
        from datetime import timedelta

        client = bq_client or get_bigquery_client()
        if client is None:
            raise SmsServiceError("No se pudo conectar a BigQuery")

        query = f"""
            SELECT * FROM `{PROJECT_ID}.Temporal.ProgramacionSMS`
            WHERE id = '{schedule_id}' AND estado = 'pendiente'
            LIMIT 1
        """
        df = client.query(query).to_dataframe()
        if df.empty:
            logger.warning(f"Programación {schedule_id} no encontrada o ya ejecutada")
            return

        scheduled = df.iloc[0].to_dict()
        infobip_config = (CONFIG or load_config()).get("infobip", {})

        fetched = fetch_sms_query_rows(client, scheduled['consulta_sql'])
        if not fetched.get("success"):
            raise SmsServiceError(fetched.get("message", "Error en consulta"))

        enviar_sms_desde_filas(
            fetched["rows"], scheduled['plantilla'], infobip_config,
            client=client, usuario=scheduled.get('usuario', 'sistema'),
            query_sql=scheduled['consulta_sql'],
            allow_resend=bool(scheduled.get('confirmar_reenvio', False))
        )

        es_recurrente = bool(scheduled.get('es_recurrente', False))
        ahora = datetime.now(timezone.utc)

        if es_recurrente:
            repeticiones_realizadas = int(scheduled.get('repeticiones_realizadas', 0) or 0) + 1
            repeticiones_max = scheduled.get('repeticiones_max')
            fecha_limite = scheduled.get('fecha_limite')
            hora_inicio = scheduled.get('hora_inicio') or '08:00'
            frecuencia_tipo = scheduled.get('frecuencia_tipo') or 'diario'
            frecuencia_valor = int(scheduled.get('frecuencia_valor', 1) or 1)

            limite_alcanzado = False
            if repeticiones_max and repeticiones_realizadas >= int(repeticiones_max):
                limite_alcanzado = True
            if not limite_alcanzado and fecha_limite:
                try:
                    fecha_lim = datetime.fromisoformat(str(fecha_limite))
                    if fecha_lim.tzinfo is None:
                        fecha_lim = fecha_lim.replace(tzinfo=timezone.utc)
                    if ahora.date() >= fecha_lim.date():
                        limite_alcanzado = True
                except:
                    pass

            if limite_alcanzado:
                client.query(f"""
                    UPDATE `{PROJECT_ID}.Temporal.ProgramacionSMS`
                    SET estado = 'completado', repeticiones_realizadas = {repeticiones_realizadas},
                        fecha_ejecucion = CURRENT_TIMESTAMP(), fecha_actualizacion = CURRENT_TIMESTAMP()
                    WHERE id = '{schedule_id}'
                """).result()
            else:
                hora, minuto = hora_inicio.split(':')
                if frecuencia_tipo == 'diario':
                    proxima_fecha = ahora + timedelta(days=1)
                    if proxima_fecha.weekday() == 6:
                        proxima_fecha += timedelta(days=1)
                elif frecuencia_tipo == 'semanal':
                    proxima_fecha = ahora + timedelta(days=7)
                elif frecuencia_tipo == 'mensual':
                    proxima_fecha = ahora + timedelta(days=30)
                else:
                    proxima_fecha = ahora + timedelta(days=frecuencia_valor)

                proxima_fecha = proxima_fecha.replace(hour=int(hora), minute=int(minuto), second=0, microsecond=0)
                if proxima_fecha <= ahora:
                    proxima_fecha += timedelta(days=1)
                    if frecuencia_tipo == 'diario' and proxima_fecha.weekday() == 6:
                        proxima_fecha += timedelta(days=1)

                client.query(f"""
                    UPDATE `{PROJECT_ID}.Temporal.ProgramacionSMS`
                    SET fecha_programada = '{proxima_fecha.strftime('%Y-%m-%d %H:%M:%S')}',
                        repeticiones_realizadas = {repeticiones_realizadas},
                        fecha_ejecucion = CURRENT_TIMESTAMP(), fecha_actualizacion = CURRENT_TIMESTAMP()
                    WHERE id = '{schedule_id}'
                """).result()

                scheduler.add_job(
                    execute_sms_schedule, trigger="date", run_date=proxima_fecha,
                    args=[schedule_id], id=f"sms_programado_{schedule_id}", replace_existing=True
                )
        else:
            client.query(f"""
                UPDATE `{PROJECT_ID}.Temporal.ProgramacionSMS`
                SET estado = 'enviado', fecha_ejecucion = CURRENT_TIMESTAMP(), fecha_actualizacion = CURRENT_TIMESTAMP()
                WHERE id = '{schedule_id}'
            """).result()

        log_gui_action("SMS programado ejecutado", programacion=schedule_id)

    except Exception as exc:
        logger.exception("Error en programación SMS %s", schedule_id)
        try:
            client = bq_client or get_bigquery_client()
            if client:
                client.query(f"""
                    UPDATE `{PROJECT_ID}.Temporal.ProgramacionSMS`
                    SET estado = 'fallido', fecha_actualizacion = CURRENT_TIMESTAMP()
                    WHERE id = '{schedule_id}'
                """).result()
        except:
            pass


# ==================== SMS - PROGRAMACIONES ====================

@app.route("/api/sms/programaciones", methods=["GET"])
def sms_programaciones():
    """Lista las programaciones de SMS."""
    try:
        client = bq_client or get_bigquery_client()
        if client is None:
            return jsonify({"success": False, "message": "No se pudo conectar a BigQuery"}), 500

        query = """
            SELECT * FROM `capable-arbor-209819.Temporal.ProgramacionSMS`
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


@app.route("/api/sms/cancelar/<schedule_id>", methods=["POST"])
def sms_cancelar_programacion(schedule_id):
    """Cancela una programación de SMS pendiente."""
    try:
        from google.cloud import bigquery
        client = bq_client or get_bigquery_client()
        if client is None:
            return jsonify({"success": False, "message": "No se pudo conectar a BigQuery"}), 500

        query = """
            UPDATE `capable-arbor-209819.Temporal.ProgramacionSMS`
            SET estado = 'cancelado', fecha_actualizacion = CURRENT_TIMESTAMP()
            WHERE id = @schedule_id AND estado = 'pendiente'
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("schedule_id", "STRING", schedule_id)]
        )
        client.query(query, job_config=job_config).result()

        try:
            scheduler.remove_job(f"sms_programado_{schedule_id}")
        except:
            pass
        return jsonify({"success": True, "message": "Programación cancelada"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - HISTORIAL ====================

@app.route("/api/sms/historial")
def sms_history():
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
        telefono = (request.args.get("telefono") or "").strip()
        if telefono:
            clauses.append("telefono LIKE @telefono")
            parameters.append(bigquery.ScalarQueryParameter("telefono", "STRING", f"%{telefono}%"))

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend([
            bigquery.ScalarQueryParameter("limit", "INT64", per_page),
            bigquery.ScalarQueryParameter("offset", "INT64", offset)
        ])

        query_sql = f"""
            SELECT * FROM `{SMS_LOG_TABLE}` {where}
            ORDER BY fecha_envio DESC LIMIT @limit OFFSET @offset
        """
        job_config = bigquery.QueryJobConfig(query_parameters=parameters)
        df = bq_client.query(query_sql, job_config=job_config).to_dataframe()
        df = _limpiar_nat(df)
        items = df.to_dict('records') if not df.empty else []
        return jsonify({"success": True, "page": page, "items": items})
    except Exception as exc:
        logger.exception("Error en historial SMS")
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - LISTA NEGRA ====================

@app.route("/api/sms/lista-negra", methods=["GET"])
def sms_blacklist():
    try:
        query = """
            SELECT Telefono AS telefono, Motivo AS motivo, Fecha AS fecha_creacion
            FROM `capable-arbor-209819.Tablas_Reporteria.Telefonos_Tutela`
            ORDER BY Fecha DESC LIMIT 500
        """
        df = bq_client.query(query).to_dataframe()
        items = df.to_dict('records') if not df.empty else []
        return jsonify({"success": True, "items": items, "read_only": True, "total": len(items)})
    except Exception as exc:
        if "Not found" in str(exc):
            return jsonify({"success": True, "items": [], "read_only": True, "total": 0})
        return jsonify({"success": False, "message": str(exc)}), 500


# ==================== SMS - OPERADORES / TIPOS / CATEGORÍAS ====================

@app.route("/api/sms/operadores", methods=["GET"])
def sms_operadores():
    try:
        from database import MensajeOperacion
        operadores = MensajeOperacion.query.filter(MensajeOperacion.Estado == 1) \
            .with_entities(MensajeOperacion.Operador).distinct().order_by(MensajeOperacion.Operador).all()
        items = [op[0] for op in operadores if op[0]]
        return jsonify({"success": True, "items": items})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


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

        mapeo_campos = {}
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

        for i, row in enumerate(rows):
            email = str(row.get(email_column, "")).strip()
            if not email or "@" not in email:
                continue

            contenido = construir_contenido(row, plantilla)
            if i == 0:
                contenido_preview = contenido

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

        if not contenido_preview or len(str(contenido_preview).strip()) < 50:
            contenido_preview = plantilla

        from services.email_service import traducir_a_member
        plantilla_api = traducir_a_member(plantilla, mapeo_campos)
        asunto_api = traducir_a_member(asunto, mapeo_campos) if asunto else ""

        campana_result = client.crear_campana({
            "name": campana_nombre or f"Email {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "subject": asunto_api or "Sin asunto",
            "fromAlias": from_alias or "QNT",
            "fromEmail": from_email,
            "replyEmail": reply_email or from_email,
            "content": plantilla_api,
            "mailListsIds": [lista_id]
        })
        if not campana_result.get("success"):
            raise EmailServiceError("No se pudo crear la campaña")

        campana_id = campana_result["data"]["id"]
        send_result = client.enviar_campana(campana_id, send_now=1)
        if not send_result.get("success"):
            raise EmailServiceError("No se pudo enviar la campaña")

        now = datetime.now(timezone.utc).isoformat()
        registros = []
        for email in emails_enviados:
            registros.append({
                "id": str(uuid4()), "email": email, "asunto": asunto,
                "contenido": str(contenido_preview)[:1000],
                "campana_id": str(campana_id), "campana_nombre": campana_nombre or "",
                "fecha_envio": now, "resultado": "enviado", "bulk_id": str(campana_id),
                "error": "", "campana": campana_nombre or "", "usuario": usuario or "",
                "fecha_creacion": now, "fecha_actualizacion": now
            })

        guardar_email_log(bq_client, registros)
        log_gui_action("Envio Email", campana_id=campana_id, enviados=len(emails_enviados))

        return jsonify({
            "success": True, "campana_id": campana_id,
            "enviados": len(emails_enviados), "fallidos": len(errores),
            "errores": errores[:10],
            "blacklist_excluidos": len(blacklist), "duplicados_excluidos": len(duplicados),
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


@app.route("/auto-campaigns/<int:campaign_id>", methods=["PUT", "POST"])
def auto_campaigns_update(campaign_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    result = update_auto_campaign(campaign_id, data)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/auto-campaigns/<int:campaign_id>", methods=["DELETE"])
def auto_campaigns_delete(campaign_id):
    result = delete_auto_campaign(campaign_id)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/auto-campaigns/<int:campaign_id>/run", methods=["POST"])
def auto_campaigns_run(campaign_id):
    if is_auto_campaign_running(campaign_id):
        return jsonify({"success": False, "message": "La campaña ya está en ejecución."}), 409
    started = start_auto_campaign_async(campaign_id, app)
    if not started:
        return jsonify({"success": False, "message": "La campaña ya está en ejecución."}), 409
    return jsonify({"success": True, "message": "Ejecución iniciada."})


@app.route("/auto-campaigns/<int:campaign_id>/stop", methods=["POST"])
def auto_campaigns_stop(campaign_id):
    stopped = request_stop_auto_campaign(campaign_id)
    if not stopped:
        return jsonify({"success": False, "message": "La campaña no está en ejecución."}), 404
    return jsonify({"success": True, "message": "Solicitud de detención enviada."})


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


@app.route("/auto-campaigns/<int:campaign_id>/clear-wkv", methods=["DELETE"])
def auto_campaigns_clear_wkv(campaign_id):
    from database import AutoCampaign
    from auto_campaign_executor import _get_token, _get_base_url_wolkvox

    campaign = db.session.get(AutoCampaign, campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "Campaña no encontrada."}), 404

    try:
        token = _get_token(campaign)
        if not token:
            return jsonify({"success": False, "message": "No se encontró token Wolkvox."}), 400
        campaign_id_wkv = str(campaign.wolkvox_campaign_id or "").strip()
        base_url = _get_base_url_wolkvox(campaign.server_name or "")
        campaign_type = campaign.campaign_type or "predictive"

        url = f"{base_url}/api/v2/campaign.php"
        params = {"api": "clear_campaign", "type_campaign": campaign_type, "campaign_id": campaign_id_wkv}
        headers = {"wolkvox-token": token}

        response = requests.delete(url, params=params, headers=headers, timeout=60)
        if response.ok:
            return jsonify({"success": True, "message": f"Campaña {campaign_id_wkv} limpiada."})
        return jsonify({"success": False, "message": f"Error HTTP {response.status_code}", "detail": response.text[:500]}), response.status_code
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/auto-campaigns/<int:campaign_id>/load-wkv", methods=["POST"])
def auto_campaigns_load_wkv(campaign_id):
    from database import AutoCampaign, AutoCampaignExecutionLog, db
    from auto_campaign_executor import fetch_data_from_bigquery, _get_token

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "Campaña no encontrada."}), 404

    token = _get_token(campaign)
    if not token:
        return jsonify({"success": False, "message": "No se encontró token Wolkvox."}), 400

    server_mapping = {
        "operacion-interna": "https://wv0016.wolkvox.com",
        "qnt_digital": "https://wv0016.wolkvox.com",
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

    log = AutoCampaignExecutionLog(auto_campaign_id=campaign.id, start_time=datetime.now(timezone.utc))
    db.session.add(log)
    db.session.commit()

    try:
        rows = fetch_data_from_bigquery(campaign.bigquery_query)
        log.records_fetched = len(rows)
        if not rows:
            raise ValueError("La consulta no retornó registros.")

        blacklist = get_blacklist_phones()
        registros_validos = []
        registros_bloqueados = []

        for row in rows:
            telefono_raw = str(row.get('tel1', '')).strip()
            telefono_limpio = re.sub(r'[^0-9]', '', telefono_raw)
            if telefono_limpio.startswith('57'):
                telefono_limpio = telefono_limpio[2:]
            if telefono_limpio in blacklist:
                registros_bloqueados.append({'row': row, 'telefono': telefono_limpio, 'motivo': 'Lista negra'})
            else:
                registros_validos.append(row)

        if not registros_validos:
            raise ValueError("Todos los registros fueron bloqueados por lista negra.")

        records = []
        for idx, row in enumerate(registros_validos):
            def formatear_telefono(telefono):
                telefono = re.sub(r'[^0-9]', '', str(telefono))
                if not telefono:
                    return "91570000000000"
                if telefono.startswith('57'):
                    telefono = telefono[2:]
                if telefono.startswith('+57'):
                    telefono = telefono[3:]
                if telefono.startswith('9157'):
                    return telefono
                return f"9157{telefono}"

            customer_id = str(row.get('customer_id', '')).strip()
            if not customer_id or customer_id in ('nan', 'None'):
                customer_id = str(row.get('tel1', f"CLI-{idx}")).strip()
                if not customer_id or customer_id in ('nan', 'None'):
                    customer_id = f"CLI-{idx}"

            telefono_formateado = formatear_telefono(str(row.get('tel1', '')).strip())

            nombre = str(row.get('customer_name', '')).strip()
            if not nombre or nombre in ('nan', 'None'):
                nombre = 'Sin Nombre'

            apellido = str(row.get('customer_last_name', '')).strip()
            if apellido in ('nan', 'None'):
                apellido = ''

            email = str(row.get('email', '')).strip()
            if email in ('nan', 'None'):
                email = ''

            record = {
                "customer_name": nombre, "customer_last_name": apellido,
                "id_type": "CC", "customer_id": customer_id,
                "tel1": telefono_formateado,
                "tel2": "", "tel3": "", "tel4": "", "tel5": "",
                "tel6": "", "tel7": "", "tel8": "", "tel9": "", "tel10": "",
                "tel_extra": "", "email": email,
                "age": "", "gender": "", "country": "", "state": "",
                "city": "", "zone": "", "address": "",
                "opt1": str(row.get('fecha_pago', '')), "opt2": str(row.get('valor_pagar', '')),
                "opt3": str(row.get('segmento', '')), "opt4": str(row.get('empresa', '')),
                "opt5": str(row.get('fecha_pago_2', '')), "opt6": str(row.get('valor_pagar_2', '')),
                "opt7": str(row.get('valor_oferta_esp', '')), "opt8": str(row.get('valor_oferta_esp_2', '')),
                "opt9": str(row.get('cuotas', '')), "opt10": str(row.get('porcentaje', '')),
                "opt11": str(row.get('porcentaje_2', '')), "opt12": str(row.get('link_pago', '')),
                "recall_date": "", "recall_telephone": ""
            }
            records.append(record)

        headers = {"wolkvox-token": token, "Content-Type": "application/json"}
        batch_size = 100
        total_enviados = 0
        errores = []

        for i in range(0, len(records), batch_size):
            batch = records[i:i+batch_size]
            try:
                response = requests.post(url, params=params, headers=headers, json=batch, timeout=60)
                if response.status_code in [200, 201]:
                    total_enviados += len(batch)
                else:
                    errores.append({"status": response.status_code, "response": response.text[:500]})
            except Exception as e:
                errores.append({"error": str(e)})

        if len(errores) == 0:
            log.records_sent = total_enviados
            log.end_time = datetime.now(timezone.utc)
            db.session.commit()
            return jsonify({
                "success": True, "records_sent": total_enviados,
                "records_fetched": log.records_fetched,
                "records_blocked": len(registros_bloqueados),
                "message": f"{total_enviados} registros cargados. {len(registros_bloqueados)} bloqueados."
            })
        else:
            log.records_failed = len(records) - total_enviados
            log.error_message = f"Errores en {len(errores)} lotes"
            log.end_time = datetime.now(timezone.utc)
            db.session.commit()
            return jsonify({
                "success": False, "records_sent": total_enviados,
                "records_fetched": log.records_fetched,
                "records_blocked": len(registros_bloqueados),
                "message": f"{total_enviados} de {len(records)} cargados.",
                "errors": errores
            }), 207
    except Exception as e:
        db.session.rollback()
        log.end_time = datetime.now(timezone.utc)
        log.error_message = str(e)
        log.records_failed = log.records_fetched or 0
        db.session.commit()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/auto-campaigns/<int:campaign_id>/start-wkv", methods=["POST"])
def auto_campaigns_start_wkv(campaign_id):
    from database import AutoCampaign
    from auto_campaign_executor import start_wolkvox_campaign, _get_token

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "Campaña no encontrada."}), 404

    try:
        token = _get_token(campaign)
        if not token:
            return jsonify({"success": False, "message": "No se encontró token Wolkvox."}), 400
        result = start_wolkvox_campaign(
            campaign.wolkvox_add_record_endpoint, token,
            campaign.wolkvox_campaign_id, server_name=campaign.server_name or ""
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/auto-campaigns/<int:campaign_id>/stop-wkv", methods=["POST"])
def auto_campaigns_stop_wkv(campaign_id):
    from database import AutoCampaign
    from auto_campaign_executor import _get_base_url_wolkvox, _get_token

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "Campaña no encontrada."}), 404

    try:
        token = _get_token(campaign)
        if not token:
            return jsonify({"success": False, "message": "No se encontró token Wolkvox."}), 400
        base_url = _get_base_url_wolkvox(campaign.server_name or "")
        campaign_id_wkv = str(campaign.wolkvox_campaign_id or "").strip()
        stop_url = f"{base_url}/api/v2/campaign.php?api=stop&campaign_id={campaign_id_wkv}"
        response = requests.put(stop_url, headers={"wolkvox-token": token}, timeout=60)
        if response.ok:
            return jsonify({"success": True, "message": "Campaña detenida."})
        return jsonify({"success": False, "message": f"Error HTTP {response.status_code}", "detail": response.text[:500]}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


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


# ==================== DASHBOARD ====================

@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"success": True, **get_dashboard_data()})


@app.route("/api/dashboard/refresh", methods=["POST"])
def api_dashboard_refresh():
    try:
        payload = refresh_dashboard_from_wolkvox()
        return jsonify({"success": True, **payload})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/recent_logs", methods=["GET"])
def api_recent_logs():
    return jsonify(read_recent_log_lines(50))


@app.route("/downloads/<filename>", methods=["GET"])
def download_file(filename):
    try:
        return send_from_directory(str(DOWNLOAD_FOLDER), filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": "Archivo no encontrado"}), 404


# ==================== MAIN ====================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    init_bigquery()
    app.run(debug=True, host="0.0.0.0", port=5000)