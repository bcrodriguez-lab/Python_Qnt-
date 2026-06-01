from datetime import datetime, date
from flask import jsonify, render_template, request, send_from_directory, send_file
import io
import csv
from openpyxl import Workbook
import json
import requests
import re

from backend import (
    app,
    db,
    scheduler,
    execute_pending_tasks,
    LOG_FILE,
    get_authorization_headers,
    load_config,
    CONFIG,
    logger,
    log_gui_action,
    read_recent_log_lines,
    cleanup_old_log_files,
    reschedule_campaign_check_job,
    reschedule_console_message_job,
    #convert_json_to_csv,
    DOWNLOAD_FOLDER,
)
from general_params import load_general_parameters, save_general_parameters
from conexion_bigquery import get_bigquery_client
from bigquery import escribir_resultados_campana, count_query_results, sync_campaigns_to_bigquery
from campaigns import (
    list_campaigns,
    get_campaign,
    get_all_campaigns,
    create_campaign,
    update_campaign,
    delete_campaign,
    has_pending_campaigns_for_server,
    TIPO_CAMPANA_OPTIONS,
    TIPO_CAMPANA_CON_FLUJO,
    validate_tipo_campana,
)
from servers import load_servers, save_server, get_server, delete_server, get_server_url_prefix
from flujos_proceso import (
    load_flujos_proceso,
    get_flujo,
    save_flujo,
    delete_flujo,
    list_flujos_by_server,
    validate_flujo_for_campaign,
)
from apis import (
    load_apis,
    save_api,
    get_api,
    delete_api,
    ensure_demo_api,
    is_system_api,
    HTTP_METHODS,
    validate_http_metodo,
    normalize_http_metodo,
)
from api_runner import (
    list_handler_files,
    invoke_handler,
    extract_url_placeholders,
    build_request_body_preview,
    build_request_headers_preview,
    build_request_url_preview,
)
from servers import get_config_servidor_default
from server_apis import load_assignment_matrix, set_server_api_active
from dashboard import get_dashboard_data, refresh_dashboard_from_wolkvox
from auto_campaigns import (
    create_auto_campaign,
    delete_auto_campaign,
    get_auto_campaign,
    list_auto_campaigns,
    list_execution_logs,
    parse_auto_campaign_id,
    update_auto_campaign,
)
from auto_campaign_executor import (
    is_auto_campaign_running,
    request_stop_auto_campaign,
    start_auto_campaign_async,
)

# Crear conexión global a BigQuery al iniciar la aplicación
bq_client = None


@app.context_processor
def inject_admin_ui():
    from flask import request
    return {"current_endpoint": request.endpoint or ""}


def init_bigquery():
    """Inicializar la conexión a BigQuery"""
    global bq_client
    try:
        bq_client = get_bigquery_client()
        logger.info("Conexion a BigQuery establecida exitosamente")
    except Exception as e:
        logger.error(f"Error al conectar con BigQuery: {e}")
        bq_client = None


@app.route("/")
def index():
    """Tablero principal con resumen y campañas."""
    stats = get_dashboard_data()
    return render_template("index.html", **stats)

@app.route('/reports')
def reports_index():
    """Página simple con formulario para descargar reportes de llamadas (XLSX)."""

    default_server = get_config_servidor_default() or ""

    today = date.today()

    default_start = today.strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")

    return render_template(
        "report_download.html",
        default_server=default_server,
        default_start=default_start,
        default_end=default_end
    )


@app.route('/reports/download', methods=['POST'])
def reports_download():
    """Genera un XLSX con los resultados del endpoint Wolkvox."""

    data = request.form or request.get_json() or {}

    server = (data.get('server') or '').strip()
    date_ini = (data.get('date_ini') or '').strip()
    date_end = (data.get('date_end') or '').strip()

    # =========================================================
    # Convertir fechas a formato Wolkvox
    # =========================================================

    def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:

        if not s:
            return ''

        try:

            if 'T' in s or len(s) > 10:

                dt = datetime.fromisoformat(s)

            else:

                d = date.fromisoformat(s)

                if is_end:

                    dt = datetime(
                        d.year,
                        d.month,
                        d.day,
                        23,
                        59,
                        59
                    )

                else:

                    dt = datetime(
                        d.year,
                        d.month,
                        d.day,
                        0,
                        0,
                        0
                    )

            return dt.strftime('%Y%m%d%H%M%S')

        except Exception:

            try:

                dt = datetime.strptime(s, '%Y-%m-%d')

                if is_end:

                    dt = datetime(
                        dt.year,
                        dt.month,
                        dt.day,
                        23,
                        59,
                        59
                    )

                return dt.strftime('%Y%m%d%H%M%S')

            except Exception:

                return s

    date_ini_ts = _to_wolkvox_ts(date_ini, is_end=False)
    date_end_ts = _to_wolkvox_ts(date_end, is_end=True)

    # =========================================================
    # Validaciones
    # =========================================================

    if not date_ini or not date_end:

        return jsonify({
            'success': False,
            'message': 'Se requieren date_ini y date_end.'
        }), 400

    if not server:

        return jsonify({
            'success': False,
            'message': 'El parámetro server es obligatorio.'
        }), 400

    # =========================================================
    # Construcción URL Wolkvox
    # Construcción URL Wolkvox
    url = None
    try:
        srv = get_server(server)
    except Exception:
        srv = None

    if srv:
        prefix = (srv.get('url') or '').strip().rstrip('/')
        if prefix.lower().startswith('http'):
            base_url = prefix
        else:
            base_url = f"https://wv{prefix}.wolkvox.com"
        url = (
            f"{base_url}/api/v2/reports_manager.php"
            f"?api=cdr_1"
            f"&date_ini={date_ini_ts}"
            f"&date_end={date_end_ts}"
        )
    else:
        if server.lower().startswith('http'):
            base_url = server.rstrip('/')
            url = (
                f"{base_url}/api/v2/reports_manager.php"
                f"?api=cdr_1"
                f"&date_ini={date_ini_ts}"
                f"&date_end={date_end_ts}"
            )
        else:
            url = (
                f"https://wv{server}.wolkvox.com"
                f"/api/v2/reports_manager.php"
                f"?api=cdr_1"
                f"&date_ini={date_ini_ts}"
                f"&date_end={date_end_ts}"
            )

    # =========================================================
    # Headers
    # =========================================================

    try:
        headers = get_authorization_headers(server) or {}
    except Exception:
        headers = {}

    # =========================================================
    # Request a Wolkvox
    # =========================================================

    try:

        resp = requests.get(
            url,
            headers=headers,
            timeout=60
        )

    except Exception as e:

        return jsonify({
            'success': False,
            'message': f'Error al conectar con Wolkvox: {e}'
        }), 500

    # =========================================================
    # Validar respuesta
    # =========================================================

    if resp.status_code != 200:

        return jsonify({
            'success': False,
            'message': f'Wolkvox devolvió {resp.status_code}',
            'text': resp.text[:500]
        }), 502

    # =========================================================
    # Procesar respuesta
    # =========================================================

    rows = []
    headers_row = []

    try:

        data_json = resp.json()

        if isinstance(data_json, list):

            rows = data_json

        elif isinstance(data_json, dict):

            if 'data' in data_json and isinstance(data_json['data'], list):

                rows = data_json['data']

            elif 'files' in data_json and isinstance(data_json['files'], list):

                rows = data_json['files']

            else:

                rows = [data_json]

    except Exception:

        text = resp.text

        try:

            reader = csv.reader(io.StringIO(text))

            csv_rows = list(reader)

            if csv_rows:

                headers_row = csv_rows[0]

                rows = [
                    dict(zip(headers_row, r))
                    for r in csv_rows[1:]
                ]

        except Exception:

            rows = [{
                'raw': resp.text
            }]

    # =========================================================
    # Obtener columnas
    # =========================================================

    all_keys = []

    for r in rows:

        if isinstance(r, dict):

            for k in r.keys():

                if k not in all_keys:

                    all_keys.append(k)

    # =========================================================
    # Crear Excel
    # =========================================================

    wb = Workbook()

    ws = wb.active

    ws.title = 'report'

    # Cabecera
    ws.append(all_keys or ['raw'])

    # Datos
    for r in rows:

        if isinstance(r, dict):

            row = [r.get(k, '') for k in all_keys]

        else:

            row = [str(r)]

        ws.append(row)

    # =========================================================
    # Guardar archivo
    # =========================================================

    bio = io.BytesIO()

    wb.save(bio)

    bio.seek(0)

    # Nombre de archivo solicitado: "1. Detalle de las llamadas.YYYYMMDD-<Servidor>.xlsx"
    today_str = datetime.utcnow().strftime('%Y%m%d')
    safe_server = (server or 'server').strip()
    # Reemplazar caracteres no seguros por guión bajo
    safe_server = re.sub(r'[^0-9A-Za-z._-]', '_', safe_server)
    filename = f"1. Detalle de las llamadas.{today_str}-{safe_server}.xlsx"

    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route("/api/invoke", methods=["POST"])
def invoke_api():
    """Endpoint para invocar una API y retornar resultado"""
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "Se requiere 'url'"}), 400
    
    try:
        log_gui_action("API INVOKE", url=url)
        headers = get_authorization_headers()
        response = requests.get(url, headers=headers, timeout=10)        
        
        if response.status_code == 200:
            try:
                response_data = response.json()
                # Generar nombre del archivo CSV
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"response_contestadas_{timestamp}.csv"
                
                # Convertir a CSV
                csv_path = convert_json_to_csv(response_data, csv_filename)
                
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": csv_filename if csv_path else None,
                    "csv_path": csv_path
                }
                log_gui_action("API INVOKE OK", csv=csv_filename)
            except:
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": None,
                    "data": response.text[:500]
                }
                log_gui_action("API INVOKE OK", formato="no JSON")
        else:
            result = {
                "success": False,
                "status": response.status_code,
                "url": url,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"Error {response.status_code}"
            }
            log_gui_action("API INVOKE fallo", status=response.status_code)
        
        return jsonify(result), 200
        
    except requests.Timeout:
        error_msg = "Timeout - La solicitud excedió el tiempo límite"
        log_gui_action("API INVOKE error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    except Exception as e:
        error_msg = str(e)
        log_gui_action("API INVOKE error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    
@app.route("/api/invokeWhatsapp", methods=["POST"])
def invoke_api_whatsapp():
    """Endpoint para invocar una API Whatsapp y retornar resultado"""
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"error": "Se requiere 'url'"}), 400
    
    try:
        log_gui_action("API WHATSAPP", url=url)
        headers = get_authorization_headers()
        response = requests.get(url, headers=headers, timeout=10)                       

        if response.status_code == 200:
            try:                
                response_data = response.json()
                # Generar nombre del archivo CSV
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"response_whatsapp_{timestamp}.csv"
                
                # Convertir a CSV
                csv_path = convert_json_to_csv(response_data, csv_filename)
                
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": csv_filename if csv_path else None,
                    "csv_path": csv_path
                }
                log_gui_action("API WHATSAPP OK", csv=csv_filename)
            except:
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": None,
                    "data": response.text[:500]
                }
                log_gui_action("API WHATSAPP OK", formato="no JSON")
        else:
            result = {
                "success": False,
                "status": response.status_code,
                "url": url,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"Error {response.status_code}"
            }
            log_gui_action("API WHATSAPP fallo", status=response.status_code)
        
        return jsonify(result), 200
        
    except requests.Timeout:
        error_msg = "Timeout - La solicitud excedió el tiempo límite"
        log_gui_action("API WHATSAPP error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    except Exception as e:
        error_msg = str(e)
        log_gui_action("API WHATSAPP error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    
@app.route("/api/invokeNoContestadas", methods=["POST"])
def invoke_api_no_contestadas():
    """Endpoint para invocar una API de llamadas no contestadas y retornar resultado"""
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"error": "Se requiere 'url'"}), 400
    
    try:
        log_gui_action("API NO CONTESTADAS", url=url)
        headers = get_authorization_headers()
        response = requests.get(url, headers=headers, timeout=10)                       

        if response.status_code == 200:
            try:                
                response_data = response.json()
                # Generar nombre del archivo CSV
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"response_no_contestadas_{timestamp}.csv"
                
                # Convertir a CSV
                csv_path = convert_json_to_csv(response_data, csv_filename)
                
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": csv_filename if csv_path else None,
                    "csv_path": csv_path
                }
                log_gui_action("API NO CONTESTADAS OK", csv=csv_filename)
            except:
                result = {
                    "success": True,
                    "status": 200,
                    "url": url,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": "exito",
                    "csv_file": None,
                    "data": response.text[:500]
                }
                log_gui_action("API NO CONTESTADAS OK", formato="no JSON")
        else:
            result = {
                "success": False,
                "status": response.status_code,
                "url": url,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"Error {response.status_code}"
            }
            log_gui_action("API NO CONTESTADAS fallo", status=response.status_code)
        
        return jsonify(result), 200
        
    except requests.Timeout:
        error_msg = "Timeout - La solicitud excedió el tiempo límite"
        log_gui_action("API NO CONTESTADAS error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    except Exception as e:
        error_msg = str(e)
        log_gui_action("API NO CONTESTADAS error", detalle=error_msg)
        return jsonify({
            "success": False,
            "status": 0,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": error_msg
        }), 500
    
@app.route("/bd/invokeBigquery", methods=["POST"])
def guardar_bigquery():
    """Endpoint genérico para BigQuery que recuerda usar la página de configuración."""
    return jsonify({
        "success": False,
        "message": "Use la página /config-bigquery para configurar campañas y probar queries."
    }), 400


def _parse_campaign_payload(data):
    """Valida y normaliza datos de campaña desde JSON."""
    nombre = (data.get("nombre") or "").strip()
    operacion = (data.get("operacion") or "").strip()
    tipo = (data.get("tipo") or "").strip()
    tipo_campana_raw = (data.get("tipo_campana") or "").strip()
    fecha_inicio = (data.get("fecha_inicio") or "").strip()
    consulta = (data.get("consulta") or "").strip()
    descripcion = (data.get("descripcion") or "").strip()
    servidor = (data.get("server") or data.get("servidor") or "").strip()
    usuario = (data.get("usuario") or "").strip()

    if not nombre:
        return None, ("El nombre de campaña es obligatorio.", 400)
    if not operacion:
        return None, ("La operación es obligatoria.", 400)
    if not tipo:
        return None, ("El tipo es obligatorio.", 400)
    tipo_campana = validate_tipo_campana(tipo_campana_raw)
    if not tipo_campana:
        return None, (
            "El tipo de campaña es obligatorio. Opciones: Email, Llamada, SMS, WhatsApp.",
            400,
        )
    if not fecha_inicio:
        return None, ("La fecha de inicio es obligatoria.", 400)
    if not consulta:
        return None, ("La consulta SQL es obligatoria.", 400)
    if not usuario:
        return None, ("El usuario es obligatorio.", 400)

    try:
        fecha_inicio_dt = datetime.fromisoformat(fecha_inicio)
    except ValueError:
        return None, ("Formato de fecha inválido. Use YYYY-MM-DDTHH:MM.", 400)

    flujo_proceso_id_raw = (data.get("flujo_proceso_id") or data.get("campaign_id") or "").strip()
    flujo_proceso_id, flujo_error = validate_flujo_for_campaign(
        flujo_proceso_id_raw, servidor, tipo_campana
    )
    if flujo_error:
        return None, (flujo_error, 400)

    return {
        "nombre": nombre,
        "operacion": operacion,
        "tipo": tipo,
        "tipo_campana": tipo_campana,
        "flujo_proceso_id": flujo_proceso_id or "",
        "fecha_inicio": fecha_inicio_dt,
        "consulta": consulta,
        "descripcion": descripcion,
        "usuario": usuario,
        "servidor": servidor,
    }, None


@app.route("/config-bigquery")
def config_bigquery():
    """Página de configuración de campañas (SQLite)."""
    campaigns = []
    error = None
    today = date.today()
    default_start = today.strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")

    start_date = request.args.get("start_date", default_start)
    end_date = request.args.get("end_date", default_end)
    search = request.args.get("search", "").strip()
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", 10))
        if page_size < 1:
            page_size = 10
    except ValueError:
        page_size = 10

    page_size = min(max(page_size, 5), 50)

    # Cargar servidores para el dropdown
    servers_result = load_servers()
    servers = servers_result.get('servers', []) if servers_result.get('success') else []
    options = servers_result.get('options', {}) if servers_result.get('success') else {}
    operaciones = options.get('operaciones', [])
    tipos = options.get('tipos', [])
    usuarios = options.get('usuarios', [])

    server = request.args.get('server', '').strip()
    operacion_sel = request.args.get('operacion', '').strip()
    tipo_sel = request.args.get('tipo', '').strip()
    usuario_sel = request.args.get('usuario', '').strip()

    result = list_campaigns(
        start_date=start_date,
        end_date=end_date,
        search=search,
        server=server or None,
        operacion=operacion_sel or None,
        tipo=tipo_sel or None,
        usuario=usuario_sel or None,
        page=page,
        page_size=page_size,
    )
    if result.get("success"):
        campaigns = result.get("campaigns", [])
        total = result.get("total", 0)
        total_pages = result.get("total_pages", 1)
    else:
        error = result.get("message")
        total = 0
        total_pages = 1

    return render_template(
        "config_bigquery.html",
        campaigns=campaigns,
        error=error,
        start_date=start_date,
        end_date=end_date,
        search=search,
        server=server,
        servers=servers,
        operaciones=operaciones,
        tipos=tipos,
        tipos_campana=TIPO_CAMPANA_OPTIONS,
        tipos_campana_con_flujo=TIPO_CAMPANA_CON_FLUJO,
        usuarios=usuarios,
        operacion_selected=operacion_sel,
        tipo_selected=tipo_sel,
        usuario_selected=usuario_sel,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@app.route("/config-bigquery/save", methods=["POST"])
def save_bigquery_campaign():
    data = request.get_json() or {}
    payload, error = _parse_campaign_payload(data)
    if error:
        return jsonify({"success": False, "message": error[0]}), error[1]

    save_result = create_campaign(payload)
    if not save_result.get("success"):
        return jsonify({"success": False, "message": save_result.get("message", "Error guardando campaña.")}), 500

    campaign = save_result.get("campaign") or {}
    log_gui_action(
        "Crear campaña",
        id=campaign.get("id"),
        nombre=payload.get("nombre"),
    )

    return jsonify({
        "success": True,
        "message": save_result.get("message"),
        "campaign": save_result.get("campaign"),
    })

@app.route("/config-bigquery/get", methods=["POST"])
def get_bigquery_campaign():
    data = request.get_json() or {}
    campaign_id = parse_campaign_id(data.get("id"))

    if campaign_id is None:
        return jsonify({"success": False, "message": "El id de campaña es obligatorio y debe ser numérico."}), 400

    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "No se encontró la campaña."}), 404

    return jsonify({"success": True, "campaign": campaign})

@app.route("/config-bigquery/update", methods=["POST"])
def update_bigquery_campaign():
    data = request.get_json() or {}
    campaign_id = parse_campaign_id(data.get("id"))

    if campaign_id is None:
        return jsonify({"success": False, "message": "El id de campaña es obligatorio y debe ser numérico."}), 400

    payload, error = _parse_campaign_payload(data)
    if error:
        return jsonify({"success": False, "message": error[0]}), error[1]

    existing = get_campaign(campaign_id)
    if not existing:
        return jsonify({"success": False, "message": "No se encontró la campaña."}), 404

    try:
        current_start = datetime.fromisoformat(existing.get("fecha_inicio"))
    except Exception:
        return jsonify({"success": False, "message": "No se pudo validar la fecha de la campaña existente."}), 500

    if current_start < datetime.now():
        return jsonify({"success": False, "message": "No se puede modificar una campaña cuya fecha de inicio ya es anterior al momento actual."}), 400

    update_result = update_campaign(campaign_id, payload)
    if not update_result.get("success"):
        return jsonify({"success": False, "message": update_result.get("message", "Error actualizando campaña.")}), 500

    log_gui_action("Actualizar campaña", id=campaign_id, nombre=payload.get("nombre"))

    return jsonify({
        "success": True,
        "message": update_result.get("message", "Campaña actualizada correctamente."),
        "campaign": update_result.get("campaign"),
    })

@app.route("/config-bigquery/delete", methods=["POST"])
def delete_bigquery_campaign():
    data = request.get_json() or {}
    campaign_id = parse_campaign_id(data.get("id"))
    if campaign_id is None:
        return jsonify({"success": False, "message": "El id de campaña es obligatorio y debe ser numérico."}), 400

    delete_result = delete_campaign(campaign_id)
    if not delete_result.get("success"):
        log_gui_action("Eliminar campaña fallo", id=campaign_id, mensaje=delete_result.get("message"))
        return jsonify({"success": False, "message": delete_result.get("message", "Error borrando la campaña.")}), 500

    log_gui_action("Eliminar campaña", id=campaign_id)

    return jsonify({
        "success": True,
        "message": delete_result.get("message", "Campaña eliminada correctamente."),
    })


@app.route("/config-bigquery/sync", methods=["POST"])
def sync_bigquery_campaigns():
    """Sincroniza todas las campañas de SQLite hacia BigQuery."""
    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar el cliente de BigQuery."}), 500

    campaigns = get_all_campaigns()
    sync_result = sync_campaigns_to_bigquery(bq_client, campaigns)
    if not sync_result.get("success"):
        log_gui_action("Sincronizar campañas fallo", mensaje=sync_result.get("message"))
        return jsonify({"success": False, "message": sync_result.get("message", "Error sincronizando.")}), 500

    log_gui_action(
        "Sincronizar campañas",
        filas=sync_result.get("rows_written", 0),
    )

    return jsonify({
        "success": True,
        "message": sync_result.get("message"),
        "rows_written": sync_result.get("rows_written", 0),
    })

@app.route("/config-bigquery/test-count", methods=["POST"])
def test_bigquery_query():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()

    if not query:
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400

    if bq_client is None:
        init_bigquery()
        if bq_client is None:
            return jsonify({"success": False, "message": "No se pudo inicializar el cliente de BigQuery."}), 500

    result = count_query_results(bq_client, query)
    if result.get("success"):
        campaign_id = parse_campaign_id(data.get("campaign_id") or data.get("id"))
        if campaign_id is not None:
            from database import Campaign, db
            campaign = Campaign.query.get(campaign_id)
            if campaign:
                campaign.total_clientes = int(result.get("total") or 0)
                db.session.commit()
                result["total_clientes_saved"] = True
        log_gui_action("Probar conteo BigQuery", total=result.get("total"), campaign_id=campaign_id)
        return jsonify(result)
    log_gui_action("Probar conteo BigQuery fallo", mensaje=result.get("message"))
    return jsonify(result), 400


@app.route("/auto-campaigns", methods=["GET"])
def auto_campaigns_index():
    campaigns = list_auto_campaigns()
    return render_template("auto_campaigns/index.html", campaigns=campaigns)


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


@app.route("/auto-campaigns/new", methods=["GET"])
def auto_campaigns_new():
    return render_template("auto_campaigns/form.html", **_auto_campaign_form_context())


@app.route("/auto-campaigns", methods=["POST"])
def auto_campaigns_create():
    data = request.get_json(silent=True) or request.form.to_dict()
    result = create_auto_campaign(data)
    if not result.get("success"):
        return jsonify(result), 400
    log_gui_action("Crear campaña automática", id=result.get("campaign", {}).get("id"))
    return jsonify(result)


@app.route("/auto-campaigns/test-count", methods=["POST"])
def auto_campaigns_test_count():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "message": "La consulta SQL es obligatoria."}), 400

    global bq_client
    if bq_client is None:
        init_bigquery()
    if bq_client is None:
        return jsonify({"success": False, "message": "No se pudo inicializar el cliente de BigQuery."}), 500

    result = count_query_results(bq_client, query)
    if not result.get("success"):
        log_gui_action("Preconteo campaña automática fallo", mensaje=result.get("message"))
        return jsonify(result), 400

    campaign_id = parse_auto_campaign_id(data.get("campaign_id") or data.get("id"))
    if campaign_id is not None:
        from database import AutoCampaign

        campaign = AutoCampaign.query.get(campaign_id)
        if campaign:
            campaign.last_precount = int(result.get("total") or 0)
            db.session.commit()
            result["last_precount_saved"] = True

    log_gui_action("Preconteo campaña automática", total=result.get("total"), campaign_id=campaign_id)
    return jsonify(result)


@app.route("/auto-campaigns/<int:campaign_id>", methods=["GET"])
def auto_campaigns_detail(campaign_id):
    campaign = get_auto_campaign(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "No se encontró la campaña automática."}), 404
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
    log_gui_action("Actualizar campaña automática", id=campaign_id)
    return jsonify(result)


@app.route("/auto-campaigns/<int:campaign_id>", methods=["DELETE"])
def auto_campaigns_delete(campaign_id):
    result = delete_auto_campaign(campaign_id)
    if not result.get("success"):
        return jsonify(result), 400
    log_gui_action("Eliminar campaña automática", id=campaign_id)
    return jsonify(result)


@app.route("/auto-campaigns/<int:campaign_id>/run", methods=["POST"])
def auto_campaigns_run(campaign_id):
    if is_auto_campaign_running(campaign_id):
        return jsonify({"success": False, "message": "La campaña ya está en ejecución."}), 409
    started = start_auto_campaign_async(campaign_id, app)
    if not started:
        return jsonify({"success": False, "message": "La campaña ya está en ejecución."}), 409
    log_gui_action("Ejecutar campaña automática", id=campaign_id)
    return jsonify({"success": True, "message": "Ejecución iniciada en segundo plano."})


@app.route("/auto-campaigns/<int:campaign_id>/stop", methods=["POST"])
def auto_campaigns_stop(campaign_id):
    stopped = request_stop_auto_campaign(campaign_id)
    if not stopped:
        return jsonify({"success": False, "message": "La campaña no está en ejecución en este proceso."}), 404
    log_gui_action("Detener campaña automática", id=campaign_id)
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
        return jsonify({"success": False, "message": "No hay informes para esta campaña."}), 404

    payload = {
        "execution_id": log.id,
        "auto_campaign_id": log.auto_campaign_id,
        "start_time": log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else "",
        "end_time": log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else "",
        "records_fetched": log.records_fetched,
        "records_sent": log.records_sent,
        "records_failed": log.records_failed,
        "error_message": log.error_message or "",
        "csv_file_path": log.csv_file_path or "",
        "report_file_path": log.report_file_path or "",
    }
    bio = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(
        bio,
        as_attachment=True,
        download_name=f"auto_campaign_{campaign_id}_execution_{log.id}.json",
        mimetype="application/json",
    )


@app.route("/auto-campaigns/<int:campaign_id>/records", methods=["DELETE"])
def auto_campaigns_delete_records(campaign_id):
    from database import AutoCampaign, AutoCampaignExecutionLog

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "No se encontró la campaña automática."}), 404

    remote_result = None
    endpoint = (campaign.wolkvox_delete_records_endpoint or "").strip()
    if endpoint:
        token = (get_authorization_headers(campaign.server_name or None) or {}).get("wolkvox-token", "")
        url = endpoint.replace("{{campaign_id}}", campaign.wolkvox_campaign_id)
        if "{{servidor}}" in url or "{{server}}" in url:
            server_value = campaign.server_name or ""
            server = get_server(campaign.server_name) if campaign.server_name else None
            if server:
                server_value = (server.get("url") or "").rstrip("/")
                if server_value and not server_value.startswith(("http://", "https://")):
                    server_value = f"https://wv{server_value}.wolkvox.com"
            url = url.replace("{{servidor}}", server_value).replace("{{server}}", server_value)
        try:
            response = requests.delete(url, headers={"wolkvox-token": token} if token else {}, timeout=60)
            remote_result = {"status": response.status_code, "ok": response.ok, "text": response.text[:1000]}
            if not response.ok:
                return jsonify({
                    "success": False,
                    "message": "Wolkvox respondió error al borrar registros.",
                    "remote": remote_result,
                }), 502
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 502

    deleted_logs = AutoCampaignExecutionLog.query.filter_by(auto_campaign_id=campaign_id).delete()
    db.session.commit()
    log_gui_action("Borrar registros campaña automática", id=campaign_id, logs=deleted_logs)
    return jsonify({
        "success": True,
        "message": "Registros remotos borrados si había endpoint configurado; logs locales eliminados.",
        "deleted_logs": deleted_logs,
        "remote": remote_result,
    })


@app.route("/auto-campaigns/<int:campaign_id>/reset", methods=["POST"])
def auto_campaigns_reset(campaign_id):
    from auto_campaigns import calculate_next_run
    from database import AutoCampaign

    campaign = AutoCampaign.query.get(campaign_id)
    if not campaign:
        return jsonify({"success": False, "message": "No se encontró la campaña automática."}), 404
    if campaign.running:
        return jsonify({"success": False, "message": "Detenga la campaña antes de reiniciar el ciclo."}), 409
    campaign.next_run = calculate_next_run(campaign.schedule_type, campaign.schedule_value)
    campaign.last_run = None
    db.session.commit()
    log_gui_action("Reiniciar ciclo campaña automática", id=campaign_id)
    return jsonify({
        "success": True,
        "message": "Ciclo reiniciado.",
        "next_run": campaign.next_run.strftime("%Y-%m-%d %H:%M:%S") if campaign.next_run else "",
    })


@app.route('/config-general')
def config_general():
    """Parámetros generales (intervalo de revisión de campañas, etc.)."""
    result = load_general_parameters()
    parameters = result.get("parameters", {})
    limits = result.get("limits", {})
    error = None if result.get("success") else result.get("message")
    return render_template(
        "config_general.html",
        parameters=parameters,
        limits=limits,
        error=error,
    )


@app.route('/config-general/save', methods=['POST'])
def config_general_save():
    data = request.get_json() or {}
    result = save_general_parameters(data)
    if not result.get("success"):
        return jsonify(result), 400

    load_config()
    interval = reschedule_campaign_check_job(
        result["parameters"]["campaign_check_interval_seconds"]
    )
    console_interval = reschedule_console_message_job(
        result["parameters"]["console_message_interval_seconds"]
    )
    result["scheduler_interval_seconds"] = interval
    result["console_message_interval_seconds"] = console_interval
    result["logs_deleted"] = cleanup_old_log_files()
    log_gui_action(
        "Guardar parámetros generales",
        intervalo_campanas=interval,
        intervalo_consola=console_interval,
    )
    return jsonify(result)


@app.route("/config-flujos-proceso")
def config_flujos_proceso():
    """Página de flujos de proceso Wolkvox."""
    result = load_flujos_proceso()
    flujos = []
    servers = []
    error = None
    servers_result = load_servers()
    servers = (
        servers_result.get("servers", [])
        if servers_result.get("success")
        else []
    )
    if result.get("success"):
        flujos = result.get("flujos", [])
    else:
        error = result.get("message")
    return render_template(
        "config_flujos_proceso.html",
        flujos=flujos,
        servers=servers,
        error=error,
    )


@app.route("/config-flujos-proceso/get", methods=["POST"])
def config_flujos_proceso_get():
    data = request.get_json() or {}
    flujo_id = (data.get("id") or "").strip()
    if not flujo_id:
        return jsonify({"success": False, "message": "El id es obligatorio."}), 400
    flujo = get_flujo(flujo_id)
    if not flujo:
        return jsonify({"success": False, "message": "No se encontró el flujo."}), 404
    return jsonify({"success": True, "flujo": flujo})


@app.route("/config-flujos-proceso/save", methods=["POST"])
def config_flujos_proceso_save():
    data = request.get_json() or {}
    flujo_id = (data.get("id") or "").strip()
    nombre = (data.get("nombre") or "").strip()
    servidor = (data.get("servidor") or "").strip()
    original_id = (data.get("original_id") or "").strip()
    if not flujo_id or not nombre or not servidor:
        return jsonify({
            "success": False,
            "message": "Id, nombre y servidor son obligatorios.",
        }), 400
    result = save_flujo(flujo_id, nombre, servidor, original_id=original_id or None)
    if result.get("success"):
        log_gui_action("Guardar flujo de proceso", id=flujo_id, nombre=nombre)
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
        log_gui_action("Eliminar flujo de proceso", id=flujo_id)
        return jsonify(result)
    return jsonify(result), 400


@app.route("/config-bigquery/flujos-proceso", methods=["GET"])
def config_bigquery_flujos_proceso():
    """Lista flujos para dropdown de campaña (filtrado por servidor)."""
    server = (request.args.get("server") or request.args.get("servidor") or "").strip()
    flujos = list_flujos_by_server(server)
    return jsonify({"success": True, "flujos": flujos})


@app.route('/config-servers')
def config_servers():
    """Página para listar y configurar servidores."""
    result = load_servers()
    servers = []
    error = None
    if result.get('success'):
        servers = result.get('servers', [])
    else:
        error = result.get('message')
    return render_template('config_servers.html', servers=servers, error=error)


@app.route('/config-servers/get', methods=['POST'])
def config_servers_get():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre del servidor es obligatorio.'}), 400

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
        return jsonify({'success': False, 'message': 'El nombre y la URL son obligatorios.'}), 400
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
        return jsonify({'success': False, 'message': 'El nombre del servidor es obligatorio.'}), 400
    result = delete_server(name)
    if result.get('success'):
        log_gui_action("Eliminar servidor", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


@app.route('/config-apis')
def config_apis():
    """Página para listar y configurar APIs."""
    ensure_demo_api()
    result = load_apis()
    apis = []
    error = None
    if result.get('success'):
        apis = result.get('apis', [])
    else:
        error = result.get('message')
    return render_template(
        'config_apis.html',
        apis=apis,
        error=error,
        handler_files=list_handler_files(),
        http_methods=HTTP_METHODS,
    )


@app.route('/config-apis/get', methods=['POST'])
def config_apis_get():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre de la API es obligatorio.'}), 400

    if is_system_api(name):
        return jsonify({
            'success': False,
            'message': 'La API DEMO es de referencia del sistema y no se puede modificar.',
        }), 403

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
        return jsonify({'success': False, 'message': 'La frecuencia de ejecución es obligatoria.'}), 400
    try:
        frecuencia = int(frecuencia)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'La frecuencia debe ser un número entero.'}), 400
    if frecuencia < 1:
        return jsonify({'success': False, 'message': 'La frecuencia debe ser al menos 1 minuto.'}), 400

    archivo = (data.get('archivo') or '').strip()
    metodo_raw = (data.get('metodo') or '').strip()

    if not name or not url:
        return jsonify({'success': False, 'message': 'El nombre y la URL son obligatorios.'}), 400
    if not archivo or not metodo_raw:
        return jsonify({'success': False, 'message': 'El archivo y el método son obligatorios.'}), 400

    metodo_check = validate_http_metodo(metodo_raw)
    if not metodo_check.get('success'):
        return jsonify(metodo_check), 400
    metodo = normalize_http_metodo(metodo_raw)

    result = save_api(
        name, url, descripcion, frecuencia,
        archivo, metodo,
        original_name=original_name or None,
    )
    if result.get('success'):
        log_gui_action("Guardar API", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


def _parse_api_test_payload(raw) -> tuple[dict | None, str | None]:
    if raw is None:
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}, None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"JSON de parámetros inválido: {exc}"
        if not isinstance(parsed, dict):
            return None, "Los parámetros deben ser un objeto JSON."
        return parsed, None
    return None, "Formato de parámetros no válido."


def _resolve_api_for_test(data: dict) -> tuple[dict | None, str | None]:
    """API a probar: borrador del formulario o registro guardado por nombre."""
    draft = data.get("draft")
    if isinstance(draft, dict):
        url = (draft.get("url") or "").strip()
        archivo = (draft.get("archivo") or "").strip()
        metodo_raw = (draft.get("metodo") or "").strip()
        if url and archivo and metodo_raw:
            metodo = normalize_http_metodo(metodo_raw)
            if not metodo:
                return None, "Método HTTP inválido en el formulario."
            return {
                "name": (draft.get("name") or data.get("name") or "").strip(),
                "url": url,
                "archivo": archivo,
                "metodo": metodo,
            }, None

    name = (data.get("name") or "").strip()
    if not name:
        return None, "Complete URL, archivo y método en el formulario para probar."
    api = get_api(name)
    if not api:
        return None, "No se encontró la API. Guarde primero o complete el formulario."
    return api, None


def _default_api_test_payload(api: dict) -> dict:
    load_config()
    from backend import CONFIG

    template: dict = {}
    url = api.get("url") or ""
    for key in extract_url_placeholders(url):
        if key == "servidor":
            template[key] = get_config_servidor_default()
        else:
            template[key] = ""
    token = CONFIG.get("wolkvox-token") or ""
    if token:
        template["wolkvox-token"] = token
    if (api.get("archivo") or "").strip() == "Wolkvox_Carga_Clientes":
        from api_handlers.Wolkvox_Carga_Clientes import load_ejemplo_datos_clientes_csv

        template["datos_clientes"] = load_ejemplo_datos_clientes_csv()
    archivo_api = (api.get("archivo") or "").strip()
    if archivo_api in ("Wolkvox_Carga_Clientes", "PararCampana", "BorrarClientesCampana"):
        template.setdefault("campaign_id", "")
    return template


@app.route('/config-apis/test-template', methods=['POST'])
def config_apis_test_template():
    """Plantilla JSON de parámetros para prueba manual según la API."""
    data = request.get_json() or {}
    api, error = _resolve_api_for_test(data)
    if error:
        return jsonify({"success": False, "message": error}), 400

    template = _default_api_test_payload(api)
    return jsonify({
        "success": True,
        "api": api,
        "url_placeholders": extract_url_placeholders(api.get("url") or ""),
        "template": template,
    })


@app.route('/config-apis/preview-body', methods=['POST'])
def config_apis_preview_body():
    """Previsualiza el body HTTP sin llamar al servicio externo."""
    data = request.get_json() or {}
    payload, error = _parse_api_test_payload(data.get("payload"))
    if error:
        return jsonify({"success": False, "message": error}), 400

    api, error = _resolve_api_for_test(data)
    if error:
        return jsonify({"success": False, "message": error}), 400

    try:
        body = build_request_body_preview(
            api.get("archivo") or "",
            payload or {},
            api.get("url") or "",
            api,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if isinstance(body, dict) and body.get("_error"):
        return jsonify({"success": False, "message": body["_error"]}), 400

    request_headers = build_request_headers_preview(
        api.get("archivo") or "",
        payload or {},
        api,
    )
    try:
        request_url = build_request_url_preview(
            api.get("url") or "",
            payload or {},
            api,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify({
        "success": True,
        "request_body": body,
        "request_headers": request_headers,
        "request_url": request_url,
    })


@app.route('/config-apis/test', methods=['POST'])
def config_apis_test():
    """Ejecuta una prueba manual del handler registrado."""
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

    try:
        request_body = build_request_body_preview(
            archivo, payload or {}, api.get("url") or "", api
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if isinstance(request_body, dict) and request_body.get("_error"):
        return jsonify({"success": False, "message": request_body["_error"]}), 400

    payload_dict = payload or {}
    request_headers = build_request_headers_preview(archivo, payload_dict, api)
    try:
        preview_url = build_request_url_preview(api.get("url") or "", payload_dict, api)
    except ValueError as exc:
        preview_url = ""
    result = invoke_handler(archivo, metodo, api, payload_dict)
    if not request_headers and isinstance(result.get("request_headers"), dict):
        request_headers = result.get("request_headers")
    log_gui_action(
        "Probar API",
        nombre=api.get("name") or "borrador",
        exito=bool(result.get("success")),
        http=result.get("status"),
    )

    return jsonify({
        "success": bool(result.get("success")),
        "message": result.get("message", ""),
        "request_body": request_body,
        "request_headers": request_headers,
        "request_url": result.get("url") or preview_url,
        "result": result,
    })


@app.route('/config-apis/delete', methods=['POST'])
def config_apis_delete():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'El nombre de la API es obligatorio.'}), 400
    result = delete_api(name)
    if result.get('success'):
        log_gui_action("Eliminar API", nombre=name)
        return jsonify(result)
    return jsonify(result), 400


@app.route('/config-server-apis')
def config_server_apis():
    """Matriz servidor × API (activar/desactivar por checkbox)."""
    ensure_demo_api()
    result = load_assignment_matrix()
    servers = []
    apis = []
    assignments = {}
    error = None
    if result.get('success'):
        servers = result.get('servers', [])
        apis = result.get('apis', [])
        assignments = result.get('assignments', {})
    else:
        error = result.get('message')
    return render_template(
        'config_server_apis.html',
        servers=servers,
        apis=apis,
        assignments=assignments,
        error=error,
    )


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
        log_gui_action(
            "Asignar API a servidor",
            servidor=server,
            api=api,
            activo=active,
        )
        return jsonify(result)
    return jsonify(result), 400


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """Datos del tablero (campañas de hoy y métricas agregadas)."""
    return jsonify({"success": True, **get_dashboard_data()})


@app.route("/api/dashboard/refresh", methods=["POST"])
def api_dashboard_refresh():
    """Consulta Wolkvox y devuelve el tablero con métricas actualizadas."""
    try:
        payload = refresh_dashboard_from_wolkvox()
        log_gui_action(
            "Actualizar tablero campañas",
            actualizadas=payload.get("wolkvox_refreshed", 0),
        )
        return jsonify({"success": True, **payload})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/recent_logs", methods=["GET"])
def api_recent_logs():
    """Obtener últimos logs"""
    return jsonify(read_recent_log_lines(50))


@app.route("/downloads/<filename>", methods=["GET"])
def download_file(filename):
    """Descargar archivo CSV desde la carpeta downloads"""
    try:
        return send_from_directory(str(DOWNLOAD_FOLDER), filename, as_attachment=True)
    except Exception as e:
        logger.error(f"Error descargando {filename}: {e}")
        return jsonify({"error": "Archivo no encontrado"}), 404


@app.route("/reportes")
def reportes():
    load_config()
    servidor = CONFIG.get("servidor", "")
    wolkvox_token = CONFIG.get("wolkvox-token", "")
    # Obtener lista de servidores para el selector
    from servers import load_servers
    servers_result = load_servers()
    servers = servers_result.get('servers', []) if servers_result.get('success') else []

    today = datetime.utcnow().date()
    default_start = today.isoformat()
    default_end = today.isoformat()

    return render_template(
        "reportes.html",
        servidor=servidor,
        wolkvox_token=wolkvox_token,
        servers=servers,
        default_start=default_start,
        default_end=default_end,
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    # Inicializar conexión a BigQuery
    init_bigquery()
    # El job de campañas (cada 5 min) se registra en backend.py al importar el modulo.
    app.run(debug=True, host="0.0.0.0", port=5000)
