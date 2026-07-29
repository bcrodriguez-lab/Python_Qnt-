# Python_Qnt- — Wolkvox Contact Center & BigQuery Integration

Sistema integral desarrollado en **Python con Flask** para la gestión centralizada de campañas de contact center, programación de tareas, integración con APIs externas (Wolkvox) y análisis de datos mediante **Google BigQuery**.

---

## 📋 Tabla de Contenidos

1. [Descripción General](#descripción-general)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Estructura de Archivos](#estructura-de-archivos)
4. [Archivos Core](#archivos-core)
5. [Módulos de Configuración](#módulos-de-configuración)
6. [Módulos de Integración BigQuery](#módulos-de-integración-bigquery)
7. [Módulos de Negocio](#módulos-de-negocio)
8. [Handlers de API](#handlers-de-api)
9. [Servicios](#servicios)
10. [Interfaz Web](#interfaz-web)
11. [Rutas Principales](#rutas-principales)
12. [Flujo de Trabajo Automático](#flujo-de-trabajo-automático)
13. [Configuración y Credenciales](#configuración-y-credenciales)
14. [Instalación y Ejecución](#instalación-y-ejecución)
15. [Conceptos Clave](#conceptos-clave)

---

## Descripción General

### Objetivo Principal

Proporcionar una plataforma web robusta que permita:

- **Gestionar campañas** de contact center con programación automática
- **Configurar y ejecutar** consumo periódico de APIs externas (Wolkvox)
- **Cargar archivos CSV** y procesarlos según calendarios definidos
- **Integrar con BigQuery** para análisis avanzado de datos
- **Monitorear ejecuciones** de tareas programadas en tiempo real
- **Generar reportes XLSX** desde datos de Wolkvox

### Tecnologías

| Tecnología | Uso |
|------------|-----|
| Flask | Framework web (rutas HTTP, endpoints REST) |
| Flask-SQLAlchemy | ORM para base de datos SQLite |
| APScheduler | Programador de tareas (jobs cada minuto) |
| Google BigQuery | Almacén de datos y análisis |
| google-cloud-bigquery | Cliente Python para BigQuery |
| pandas | Manipulación y transformación de datos |
| requests | Llamadas HTTP a APIs externas |
| openpyxl | Lectura/escritura de archivos Excel |
| AdminLTE 4 | Interfaz de usuario (frontend) |

---

## Arquitectura del Sistema

```
┌─────────────────────────────────────────────────┐
│        FRONTEND (Flask + AdminLTE 4)           │
│  Dashboard | Configuraciones | Reportes        │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│      CAPA DE APLICACIÓN (app.py)                │
│  Rutas HTTP | Endpoints REST                    │
└──────────────────┬──────────────────────────────┘
                   │
       ┌───────────┼───────────┐
       │           │           │
┌─────▼──┐  ┌─────▼──┐  ┌─────▼──┐
│Backend │  │Database│  │BigQuery│
│(Lógica)│  │(SQLite)│  │(GCP)   │
└────────┘  └────────┘  └────────┘
       │           │           │
       └───────────┼───────────┘
                   │
     ┌─────────────▼─────────────┐
     │  APScheduler (Task Scheduler)│
     │  - Jobs cada minuto         │
     │  - Ejecuciones automáticas  │
     └─────────────────────────────┘
```

---

## Estructura de Archivos

```
Python_Qnt-/
├── app.py                          # Punto de entrada, rutas Flask
├── backend.py                      # Instancia Flask, scheduler, logs, invocación API
├── database.py                     # Modelos SQLAlchemy (Campaña, Servidor, API, Flujo)
├── models.py                       # Vacío (reservado o legacy)
├── config.py                       # Configuración central de descargas
├── config.json                     # Tokens, servidores, APIs, matriz server_apis
├── app.db                          # Base de datos SQLite
├── requirements.txt                # Dependencias de Python
├── script_sql.sql                  # Esquema de referencia para tablas BigQuery
├── sample_data.json                # Datos de ejemplo
│
├── bigquery.py                     # Lógica de integración con BigQuery (CRUD, consultas)
├── bigquery_processor.py           # Procesamiento principal de CDR y embudo (v10.10)
├── bigquery_cdr_processor.py       # Pipeline CDR desde Wolkvox API → BigQuery
├── bigquery_upload.py              # Subida genérica de JSON/CSV a BigQuery
├── bigquery_utils.py               # Utilidades de transformación y subida a BigQuery
├── conexion_bigquery.py            # Cliente de conexión y autenticación a BigQuery
├── subir_cdr_a_bigquery.py         # Variante del procesador de CDR (v8.4 con campaign_id desde Excel)
│
├── campaigns.py                    # CRUD de campañas
├── campaign_execution.py           # Lógica de ejecución de campañas programadas
├── auto_campaigns.py               # Campañas automáticas
├── auto_campaign_executor.py       # Executor de campañas automáticas
├── servers.py                      # CRUD de servidores en config.json
├── apis.py                         # CRUD de APIs en config.json
├── server_apis.py                  # Matriz servidor × API
├── flujos_proceso.py               # Gestión de flujos de proceso Wolkvox
├── general_params.py               # Parámetros globales
├── dashboard.py                    # Métricas y panel de control
├── api_runner.py                   # Orquestador de handlers de API
├── api_handlers/                   # Handlers específicos para cada tipo de API
│   ├── __init__.py
│   ├── DEMO.py                     # Plantilla POST de referencia
│   ├── Wolkvox_Carga_Clientes.py   # Handler para cargar clientes en Wolkvox
│   ├── ConsultarCampanas.py        # Handler para consultar campañas
│   ├── BorrarClientesCampana.py    # Handler para borrar clientes de campaña
│   ├── PararCampana.py             # Handler para detener campaña
│   └── wolkvox_utils.py            # Utilidades para Wolkvox
│
├── services/                       # Capa de servicios
│   ├── campaign_engine.py          # Motor de campañas con ejecución BigQuery
│   ├── query_validator.py          # Validación de consultas y normalización de columnas
│   ├── wolkvox_service.py          # Servicio de integración con Wolkvox
│   └── csv_service.py              # Servicio para guardar DataFrames como CSV
│
├── api_handlers/                   # Handlers de API (código ejecutable)
│   ├── __init__.py
│   └── DEMO.py
│
├── templates/                      # Plantillas Jinja2 (AdminLTE)
│   ├── layouts/adminlte.html       # Layout maestro con sidebar y navbar
│   ├── index.html                  # Dashboard principal
│   ├── config_bigquery.html        # Parametrización de campañas + sync BigQuery
│   ├── config_servers.html         # Alta/edición/borrado de servidores
│   ├── config_apis.html            # Registro de APIs
│   ├── config_server_apis.html     # Matriz servidor × API
│   ├── config_flujos_proceso.html  # Configuración de flujos de proceso
│   ├── config_general.html         # Parámetros generales
│   └── reportes.html               # Vista para descarga de reportes XLSX
│
├── static/                         # CSS, JavaScript, imágenes (AdminLTE)
├── downloads/                      # Reportes XLSX generados y respuestas de APIs
├── uploads/                        # Archivos subidos para procesamiento
├── config/                         # Credenciales (google_key.json, credentials.json, token.json)
├── instance/                       # Instancia Flask (secret key, etc.)
├── execution_log.txt               # Log de ejecución del sistema
└── README.md                       # Este archivo
```

---

## Archivos Core

| Archivo | Rol |
|---------|-----|
| **`app.py`** | Punto de entrada principal. Define casi todas las rutas HTTP, importa módulos de negocio y arranca Flask en el puerto 5000. |
| **`backend.py`** | Crea la instancia `app`, configura SQLite (`app.db`), carpetas `uploads/` y `downloads/`, logging en `execution_log.txt`, carga `config.json`, scheduler APScheduler y funciones de invocación de APIs / CSV / tareas programadas. |
| **`database.py`** | SQLAlchemy: modelos `Campaign`, `ScheduledCSV`, `APIEndpoint`, `ScheduledQuery` y migraciones ligeras de columnas (`servidor`, `api`, métricas de clientes). |
| **`config.py`** | Configuración central para descargas automáticas: modo de descarga (`hoy`, `rango`, `fecha_específica`), horarios de ejecución, activación/desactivación de descargas CDR/AMD. |
| **`config.json`** | Configuración JSON: token Wolkvox, servidores, APIs, matriz `server_apis`, listas (operaciones, tipos, usuarios). |
| **`requirements.txt`** | Dependencias: Flask, Flask-SQLAlchemy, APScheduler, requests, openpyxl. |

---

## Módulos de Configuración

| Archivo | Descripción |
|---------|-------------|
| **`servers.py`** | CRUD para servidores externos (create, read, update, delete) en `config.json`. Renombra referencias en campañas; bloquea borrado si hay campañas futuras. |
| **`apis.py`** | Administración de endpoints API configurables. CRUD completo, API DEMO de sistema (no editable/borrable), métodos HTTP permitidos (GET, POST, PUT, PATCH, DELETE). |
| **`flujos_proceso.py`** | Gestión de flujos de proceso Wolkvox. Crear, editar, eliminar flujos; asociar con campañas específicas. |
| **`general_params.py`** | Parámetros globales configurables de la aplicación. |
| **`server_apis.py`** | Matriz servidor × API (activo/inactivo) en `config.json`; sincroniza al renombrar/borrar servidores o APIs. |

---

## Módulos de Integración BigQuery

### `conexion_bigquery.py` — Conexión y autenticación

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`get_bigquery_client()`** | Retorna el cliente BigQuery autenticado. Usa service account JSON (`config/google_key.json`) o Google Cloud ADC (`gcloud`). Lanza `RuntimeError` si no se puede inicializar. |
| **`authenticate_google()`** | Autenticación OAuth2 con token persistente en `config/token.json`. Maneja refresh de tokens expirados. |

### `bigquery.py` — Operaciones CRUD y consultas

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`escribir_resultados_campana(client, registros)`** | Escribe registros de campaña en `capable-arbor-209819.volkvox2.resultado_campana_llamada`. Elimina registros del día actual y hace append de los nuevos. Convierte tipos de datos (INTEGER, FLOAT, TIMESTAMP) y maneja formatos de fecha. |
| **`fetch_select_query_rows(client, query, max_rows=50000)`** | Ejecuta un SELECT en BigQuery, normaliza nombres de columnas automáticamente (acepta variaciones como `NOMBRE`, `customer_name`, etc.) y retorna filas como `list[dict]`. Solo permite consultas SELECT/WITH. |
| **`validate_query_columns(client, query, field_mapping)`** | Valida que una consulta SELECT devuelva columnas compatibles con el mapeo de campaña. Retorna advertencia si no hay coincidencia exacta pero la consulta es válida. |
| **`count_query_results(client, query)`** | Ejecuta un COUNT sobre una consulta SELECT. Envuelve automáticamente si la consulta no contiene `COUNT(*)`. |
| **`sync_campaigns_to_bigquery(client, campaigns)`** | Sincroniza SQLite → BigQuery: trunca la tabla `Temporal_Robot_Campañas` y reescribe con el contenido actual de SQLite. |

### `bigquery_processor.py` — Procesamiento principal de CDR y embudo (v10.10)

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`procesar_y_actualizar_bigquery()`** | Pipeline principal: lee CDR de Excel, procesa datos, enriquece columnas y sube a `Embudo_Consolidado` y `Embudo_Positivo_Robot_TEST`. |
| **`procesar_datos()`** | Transforma y limpia los datos del CDR (tipificaciones, clasificación, enriquecimiento). |
| **`leer_cdr_excel(fecha)`** | Lee archivos Excel de CDR desde la carpeta de resultados en Drive. |
| **`leer_campanas_excel(fecha)`** | Lee archivos Excel de campañas para obtener `campaign_id` y `customer_id`. |
| **`consultar_auxiliares(client)`** | Consulta tablas auxiliares en BigQuery para enriquecer datos. |
| **`calcular_grupo_operador()`** | Clasifica operadores en grupos (Digital, RBK, Montos Altos, Satélites). |
| **`enriquecer_columnas_bq()`** | Agrega columnas calculadas al DataFrame antes de la subida. |
| **`_convertir_tipos_bq()`** | Convierte tipos de datos para compatibilidad con BigQuery. |
| **`subir_a_embudo_consolidado()`** | Sube el DataFrame procesado a `Temporal.Embudo_Consolidado`. |
| **`subir_a_embudo_positivo()`** | Sube los contactos positivos a `Temporal.Embudo_Positivo_Robot_TEST`. |
| **`_retry_bq_operation()`** | Ejecuta operaciones BigQuery con reintentos exponenciales (max 3 intentos). |
| **`_run_query()`** / **`_run_dml()`** / **`_run_ddl()`** | Helpers de ejecución de queries SQL en BigQuery. |

### `bigquery_cdr_processor.py` — Pipeline CDR desde Wolkvox API → BigQuery

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`process_cdr_to_bigquery(campaign, log)`** | Pipeline completo: obtiene CDR de la API Wolkvox, normaliza columnas, transforma y sube a `Temporal.Robots_Temporal`. |
| **`fetch_cdr_from_wolkvox(campaign, log)`** | Obtiene datos CDR desde múltiples endpoints de la API Wolkvox (prueba `cdr_1`, `campaign_1` con diferentes formatos de fecha). Retorna la primera respuesta con datos. |
| **`_extract_rows_from_response(data_json)`** | Extrae filas de datos de la respuesta JSON del CDR (maneja listas, dicts con clave `data`, etc.). |
| **`normalize_cdr_columns(rows)`** | Normaliza nombres de columnas del CDR al esquema estándar. |
| **`classify_operador_grupo()`** | Clasifica operadores en grupos según el mapeo definido. |
| **`_fetch_reference_data()`** | Carga datos de referencia para enriquecimiento. |
| **`transform_cdr_to_temp_table(rows)`** | Transforma los datos brutos al esquema de la tabla temporal `Robots_Temporal`. |
| **`upload_to_temp_table(df)`** | Sube el DataFrame transformado a `Temporal.Robots_Temporal` en BigQuery. |

### `bigquery_upload.py` — Subida genérica de JSON/CSV a BigQuery

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`subir_json_a_bigquery(data_json, servidor, fecha, tipo_reporte)`** | Función principal: procesa JSON, agrega metadatos (`servidor`, `fecha_descarga`, `tipo_reporte`, `fecha_procesamiento`) y sube a `Embudo_Consolidado` o `Embudo_Consolidado_AMD`. Elimina registros anteriores del mismo servidor/fecha/tipo antes de subir. |
| **`subir_cdr_a_bigquery(df, servidor, fecha)`** | Sube un DataFrame de CDR a `Temporal.Embudo_Consolidado`. |
| **`subir_amd_a_bigquery(df, servidor, fecha)`** | Sube un DataFrame de AMD a `Temporal.Embudo_Consolidado_AMD`. |
| **`verificar_tablas()`** | Verifica que las tablas `Embudo_Consolidado` y `Embudo_Consolidado_AMD` existan en BigQuery. |
| **`crear_tablas_si_no_existen()`** | Crea las tablas necesarias si no existen, con schema base (servidor, fecha_descarga, tipo_reporte, fecha_procesamiento). |

### `bigquery_utils.py` — Utilidades de transformación y subida

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`obtener_credenciales()`** | Obtiene credenciales para BigQuery desde `config/google_key.json` o Google Auth Default (ADC). |
| **`obtener_esquema_tabla()`** | Obtiene las columnas de la tabla destino `Temporal.Temporal_Robot_Campañas` desde BigQuery. |
| **`crear_dataframe_crudo(data, servidor, fecha, columnas_tabla)`** | Mapea campos del JSON crudo al esquema de la tabla destino. Convierte tipos (int, str, fechas). Maneja columnas sin origen con valores por defecto. |
| **`subir_a_bigquery(df)`** | Sube un DataFrame a BigQuery con `WRITE_APPEND`. Verifica que las columnas del DataFrame existan en la tabla. |
| **`subir_json_a_bigquery(data, servidor, fecha, tipo_reporte)`** | Función principal: crea DataFrame desde JSON y lo sube a `Temporal.Temporal_Robot_Campañas`. |

### `subir_cdr_a_bigquery.py` — Variante del procesador de CDR (v8.4)

Funciones principales:

| Función | Descripción |
|---------|-------------|
| **`procesar_y_actualizar_bigquery()`** | Pipeline principal: similar a `bigquery_processor.py` pero con match de `campaign_id` desde Excel de campañas (usa `Contacto__c = customer_id` para el join). |
| **`consultar_cdr()`** | Consulta datos CDR desde BigQuery. |
| **`consultar_auxiliares()`** | Consulta tablas auxiliares para enriquecimiento. |
| **`calcular_grupo_operador()`** | Clasifica operadores en grupos. |
| **`procesar_datos()`** | Transforma y limpia datos del CDR. |
| **`leer_campanas_excel(fecha)`** | Lee archivos de campañas para obtener `campaign_id`. |
| **`subir_a_embudo_positivo()`** | Sube contactos positivos a la tabla de positivos. |
| **`_retry_bq_operation()`** / **`_run_query()`** / **`_run_dml()`** | Helpers de ejecución con reintentos. |

---

## Módulos de Negocio

| Archivo | Descripción |
|---------|-------------|
| **`campaigns.py`** | CRUD de campañas en SQLite; validación de campañas pendientes por servidor/API; conversión a diccionarios para la UI. |
| **`campaign_execution.py`** | Lógica de ejecución de campañas programadas. Orquesta el flujo completo: carga de clientes desde BigQuery, invocación de APIs Wolkvox, carga de clientes en campaña. |
| **`auto_campaigns.py`** | Definición de campañas automáticas y sus configuraciones. |
| **`auto_campaign_executor.py`** | Executor de campañas automáticas. Incluye `fetch_data_from_bigquery()` para obtener datos desde BigQuery y `_normalize_bigquery_value()` para normalizar valores. |
| **`dashboard.py`** | Agregados para el tablero: conteo de servidores, campañas totales/programadas, progreso de clientes llamados/contactados. |
| **`api_runner.py`** | Carga dinámica de `api_handlers/<archivo>.py` y ejecuta la función del método (`post`, `get`, etc.). |

---

## Handlers de API

Los handlers se ubican en `api_handlers/` y son cargados dinámicamente por `api_runner.py`. Cada handler expone funciones para cada método HTTP configurado.

| Handler | Descripción |
|---------|-------------|
| **`DEMO.py`** | Plantilla POST de referencia. No se debe borrar la parametrización **API DEMO**. |
| **`Wolkvox_Carga_Clientes.py`** | Handler para cargar clientes en Wolkvox mediante la API `campaign.php?api=add_record`. |
| **`ConsultarCampanas.py`** | Handler para consultar campañas existentes en Wolkvox. |
| **`BorrarClientesCampana.py`** | Handler para borrar clientes de una campaña específica. |
| **`PararCampana.py`** | Handler para detener/pausar una campaña en Wolkvox. |
| **`wolkvox_utils.py`** | Utilidades compartidas para integración con Wolkvox (formato de fechas, headers de autenticación, etc.). |

---

## Servicios

| Archivo | Descripción |
|---------|-------------|
| **`services/campaign_engine.py`** | Motor de campañas con ejecución BigQuery. Funciones: `execute_bigquery_query()`, `generate_csv_from_results()`, `upload_to_wolkvox()`, `run_automatic_campaign_workflow()`, `run_all_pending_campaigns()`. |
| **`services/query_validator.py`** | Validación de consultas y normalización de columnas. Funciones: `validate_and_normalize(rows)` (acepta múltiples variaciones de nombres de columnas), `normalize_row()`, `normalize_rows()`, `validate_query_results()`, `build_alias_map()`, `map_column_name()`. |
| **`services/wolkvox_service.py`** | Servicio de integración con Wolkvox. Funciones: `_get_api_config()`, `_get_server_url()`, `upload_csv_to_campaign()`. |
| **`services/csv_service.py`** | Servicio para guardar DataFrames como CSV. Función: `save_dataframe_to_csv()`. |

---

## Interfaz Web (Templates)

| Plantilla | Ruta | Contenido |
|-----------|------|-----------|
| `layouts/adminlte.html` | Base | Menú lateral (Tablero, Campañas BQ, Servidores, APIs, APIs×Servidor), navbar, breadcrumbs, bloques `content` / `extra_js`. |
| `index.html` | `/` | Tablero: 3 indicadores, tabla por campaña, log en vivo. |
| `config_bigquery.html` | `/config-bigquery` | Parametrización de campañas, filtros, sync a BQ, probar conteo SQL. |
| `config_servers.html` | `/config-servers` | Alta/edición/borrado de servidores. |
| `config_apis.html` | `/config-apis` | Registro de APIs (archivo, método HTTP, URL, frecuencia). |
| `config_server_apis.html` | `/config-server-apis` | Tabla con checkboxes servidor × API. |
| `config_flujos_proceso.html` | `/config-flujos-proceso` | Gestión de flujos de proceso. |
| `config_general.html` | `/config-general` | Parámetros generales. |
| `reportes.html` | `/reportes` | Vista para descarga de reportes XLSX. |

---

## Rutas Principales (`app.py`)

| Ruta | Descripción |
|------|-------------|
| **`/`** | Tablero principal (`dashboard.get_dashboard_data()`). |
| **`/api/invoke`** | Invocación manual de APIs Wolkvox → CSV en `downloads/` y opcionalmente BigQuery. |
| **`/api/invokeWhatsapp`** | Invocación de API de WhatsApp. |
| **`/api/invokeNoContestadas`** | Invocación de API de llamadas no contestadas. |
| **`/config-bigquery`** | CRUD campañas + sync/test-count BigQuery. |
| **`/config-servers`** | CRUD servidores. |
| **`/config-apis`** | CRUD APIs. |
| **`/config-server-apis`** | Matriz servidor × API. |
| **`/config-flujos-proceso`** | Gestión de flujos de proceso. |
| **`/config-general`** | Parámetros generales. |
| **`/reportes`** | Generación y descarga de reportes XLSX. |
| **`/api/recent_logs`** | Últimas líneas de `execution_log.txt` (tablero). |
| **`/downloads/<archivo>`** | Descarga de CSV generados. |

---

## Flujo de Trabajo Automático

### Programador de Tareas (APScheduler)

El sistema ejecuta un **job principal cada minuto** que realiza:

```
CADA MINUTO:
├─ Revisar campañas programadas
│  ├─ Si fecha/hora coincide → Ejecutar
│  └─ Registrar resultado en log
│
├─ Consumir APIs configuradas
│  ├─ Validar servidor disponible
│  ├─ Ejecutar request HTTP
│  └─ Almacenar respuesta en CSV
│
└─ Ejecutar consultas programadas
   ├─ Validar intervalo de ejecución
   ├─ Llamar BigQuery
   └─ Guardar resultados en downloads/
```

### Ciclo de Ejecución de Campaña

```
1. Usuario programa campaña para 2026-05-27 14:00
2. Backend registra en BD con status="pending"
3. Job scheduler verifica cada minuto
4. A las 14:00: Marca como "in_progress"
5. Obtiene handler específico (ej: Wolkvox_Carga_Clientes)
6. Ejecuta POST con parámetros de campaña
7. Captura respuesta (éxito/error)
8. Marca como "completed" o "failed"
9. Registra en execution_log.txt
10. Disponible para descarga o consulta
```

---

## Configuración y Credenciales

| Ruta | Rol |
|------|-----|
| **`config.json`** | Datos operativos editables desde la app (servidores, APIs, tokens). |
| **`config/google_key.json`** | Service account para BigQuery (proyecto `capable-arbor-209819`). |
| **`config/credentials.json`**, **`token.json`** | Credenciales OAuth y tokens de sesión para Google Sheets/Drive. |
| **`requirements.txt`** | Dependencias Python. |

### Tablas BigQuery utilizadas

| Proyecto | Dataset | Tabla | Uso |
|----------|---------|-------|-----|
| `capable-arbor-209819` | `volkvox2` | `resultado_campana_llamada` | Resultados de campañas de llamadas |
| `capable-arbor-209819` | `Operacion_Analitica` | `parametrizacion_campanas` | Sincronización de campañas desde SQLite |
| `capable-arbor-209819` | `Temporal` | `Embudo_Consolidado` | Datos consolidados de CDR (CDR + AMD) |
| `capable-arbor-209819` | `Temporal` | `Embudo_Consolidado_AMD` | Datos AMD consolidados |
| `capable-arbor-209819` | `Temporal` | `Embudo_Positivo_Robot_TEST` | Contactos positivos identificados por robot |
| `capable-arbor-209819` | `Temporal` | `Temporal_Robot_Campañas` | Datos crudos de campañas desde JSON |
| `capable-arbor-209819` | `Temporal` | `Robots_Temporal` | CDR procesado desde Wolkvox API |

---

## Instalación y Ejecución

### Requisitos

- Python 3.10+
- Google Cloud SDK (`gcloud`) instalado y autenticado
- Archivo de credenciales `config/google_key.json`

### Instalación

```bash
# 1. Clonar repositorio
git clone <repo_url>
cd Python_Qnt-

# 2. Crear entorno virtual
python -m venv venv
venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
# Colocar google_key.json en config/
# Configurar config.json con servidores y tokens

# 5. Ejecutar aplicación
python app.py
```

**Acceder en navegador:** `http://127.0.0.1:5000/`

### Modo Desarrollo

- Debug habilitado por defecto en `app.py`
- Auto-recarga de cambios en templates
- Logs detallados en consola y `execution_log.txt`
- Base de datos SQLite local (`app.db`)

### Despliegue en Producción

```bash
# Usar servidor WSGI como Gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 backend:app
```

**Configuración recomendada:**
- Desactivar `debug=True`
- Usar variables de entorno para credenciales
- Configurar proxy inverso (Nginx/Apache)
- Implementar HTTPS/SSL
- Backups automáticos de `app.db`

---

## Conceptos Clave

### APScheduler (Programador de Tareas)

- Ejecuta jobs periódicos (cada minuto)
- Tres tipos de tareas: cargas CSV, consumo APIs, consultas programadas
- Reinicio automático si la aplicación falla
- Logs detallados de cada ejecución

### SQLAlchemy ORM

- Modelos de datos en `database.py`
- Relaciones entre entidades mediante claves foráneas
- Migraciones manuales (usar `db.create_all()`)
- Soporte para SQLite (producción actual), PostgreSQL, MySQL

### BigQuery Integration

- Cliente autenticado mediante `config/google_key.json` (service account) o `gcloud` ADC
- Sincronización bidireccional de campañas (SQLite ↔ BigQuery)
- Consultas programadas con almacenamiento de resultados
- Manejo centralizado en `conexion_bigquery.py` y `bigquery.py`
- Dos pipelines de procesamiento: `bigquery_processor.py` (v10.10) y `subir_cdr_a_bigquery.py` (v8.4)
- Pipeline CDR desde Wolkvox API: `bigquery_cdr_processor.py`

### Sistema de Handlers

- Nuevos handlers se crean en `api_handlers/`
- Se registran automáticamente en `api_runner.py`
- Reciben parámetros de campaña y servidor
- Retornan respuesta serializable (JSON/CSV)

### Logging y Monitoreo

- `execution_log.txt`: Registro rotativo de ejecuciones mostrado en el tablero
- Console output: Información en tiempo real
- Base de datos: Histórico de campañas y ejecuciones
- Alertas: Disponibles para configurar por error

---

## Scripts de Utilidad

| Script | Descripción |
|--------|-------------|
| **`ejecutar_pipeline.py`** | Ejecuta el pipeline de procesamiento BigQuery de forma manual. |
| **`download_auto.py`** | Descarga automática de reportes CDR desde Wolkvox. |
| **`download_campaign_detail.py`** | Descarga detalles de campañas desde Wolkvox. |
| **`download_reports.py`** | Descarga reportes generales. |
| **`download_amd_con_nombres.py`** | Descarga reportes AMD con nombres de contacto. |
| **`inspect_db.py`** | Inspección de la base de datos SQLite. |
| **`migrate_add_campaign_columns.py`** | Migración de columnas en la base de datos. |
| **`excel_report_builder.py`** | Construcción de reportes Excel. |
| **`test_bigquery.py`** | Script de prueba para verificar conexión y tablas BigQuery. |

---

## Variables de Entorno

| Variable | Descripción |
|----------|-------------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta al archivo de credenciales de servicio de Google Cloud. |
| `GOOGLE_KEY_PATH` | Ruta alternativa al archivo de clave JSON para BigQuery. |

---

## Notas Importantes

- `config.json` contiene credenciales y tokens — **no versionar** en repositorios públicos.
- `config/google_key.json` es el archivo de service account para BigQuery — mantenerlo seguro.
- El proyecto usa el proyecto GCP `capable-arbor-209819` como ID de proyecto BigQuery.
- Las tablas temporales en BigQuery (`Temporal.*`) se usan para datos de procesamiento intermedio.
- Las tablas permanentes (`volkvox2.resultado_campana_llamada`, `Operacion_Analitica.parametrizacion_campanas`) almacenan datos históricos.
- `models.py` está vacío y es reservado para uso futuro con SQLAlchemy.
