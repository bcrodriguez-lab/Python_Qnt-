# Estructura de la aplicación Wolkvox Contact Center

Aplicación **Flask** para contact center **Wolkvox**: invoca APIs externas, guarda resultados en CSV/BigQuery, parametriza **campañas** (SQLite), **servidores**, **APIs** y su **activación por servidor**. La interfaz usa **AdminLTE 3** (plantillas Jinja2).

## Visión general

```
┌─────────────────────────────────────────────────────────────────┐
│  templates/  (HTML + AdminLTE)                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  app.py  — Rutas HTTP, arranque (python app.py)                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  backend.py  — Instancia Flask, scheduler, logs, invocación API  │
└─────┬───────────────┬────────────────┬──────────────────────────┘
      │               │                │
      ▼               ▼                ▼
 campaigns.py    servers.py       apis.py / server_apis.py
 dashboard.py    api_runner.py    bigquery.py
      │               │                │
      ▼               ▼                ▼
   app.db        config.json      BigQuery + api_handlers/
```

---

## Arranque y núcleo

| Archivo | Rol |
|---------|-----|
| **`app.py`** | Punto de entrada principal (`python app.py`). Define casi todas las **rutas HTTP**, importa módulos de negocio y arranca Flask en el puerto 5000. |
| **`backend.py`** | Crea la instancia **`app`**, configura SQLite (`app.db`), carpetas `uploads/` y `downloads/`, logging en `execution_log.txt`, carga `config.json`, scheduler APScheduler y funciones de invocación de APIs / CSV / tareas programadas. |

`app.py` importa `app` desde `backend.py`: el motor Flask vive en `backend.py` y las rutas se registran en `app.py`.

---

## Capa de datos

| Archivo / recurso | Rol |
|-------------------|-----|
| **`database.py`** | SQLAlchemy: modelos `Campaign`, `ScheduledCSV`, `APIEndpoint`, `ScheduledQuery` y migraciones ligeras de columnas (`servidor`, `api`, métricas de clientes). |
| **`app.db`** | Base SQLite: campañas en `campaign_parametrization` y tablas legacy del scheduler. |
| **`config.json`** | Configuración JSON: token Wolkvox, **servidores**, **APIs**, matriz **`server_apis`**, listas (operaciones, tipos, usuarios). |
| **`models.py`** | Vacío (reservado o legacy). |

---

## Módulos de negocio (Python)

| Módulo | Responsabilidad |
|--------|-----------------|
| **`campaigns.py`** | CRUD de campañas en SQLite; validación de campañas pendientes por servidor/API; conversión a diccionarios para la UI. |
| **`servers.py`** | CRUD de servidores en `config.json`; renombra referencias en campañas; bloquea borrado si hay campañas futuras. |
| **`apis.py`** | CRUD de APIs en `config.json`; API DEMO de sistema (no editable/borrable); métodos HTTP permitidos (GET, POST, PUT, PATCH, DELETE). |
| **`server_apis.py`** | Matriz servidor × API (activo/inactivo) en `config.json`; sincroniza al renombrar/borrar servidores o APIs. |
| **`dashboard.py`** | Agregados para el tablero: conteo de servidores, campañas totales/programadas, progreso de clientes llamados/contactados. |
| **`api_runner.py`** | Carga dinámica de `api_handlers/<archivo>.py` y ejecuta la función del método (`post`, `get`, etc.). |
| **`bigquery.py`** | Escritura de resultados de llamadas, conteo de consultas SELECT, sincronización de campañas a tabla BQ. |
| **`conexion_bigquery.py`** | Cliente Google BigQuery (credenciales en `config/google_key.json`). |

---

## Handlers de API (código ejecutable)

| Ruta | Rol |
|------|-----|
| **`api_handlers/`** | Un archivo `.py` por integración. |
| **`api_handlers/DEMO.py`** | Plantilla POST de referencia; no se debe borrar la parametrización **API DEMO**. |
| **`api_handlers/__init__.py`** | Marca el paquete Python. |

Cada API en `config.json` apunta a `archivo` + `metodo`. `api_runner` invoca:

`api_handlers.<archivo>.<metodo>(api_config, payload)`

---

## Interfaz web (`templates/`)

| Plantilla | Ruta | Contenido |
|-----------|------|-----------|
| **`layouts/adminlte.html`** | Base | Menú lateral (Tablero, Campañas BQ, Servidores, APIs, APIs×Servidor), navbar, breadcrumbs, bloques `content` / `extra_js`. |
| **`index.html`** | `/` | Tablero: 3 indicadores, tabla por campaña, log en vivo. |
| **`config_bigquery.html`** | `/config-bigquery` | Parametrización de campañas, filtros, sync a BQ, probar conteo SQL. |
| **`config_servers.html`** | `/config-servers` | Alta/edición/borrado de servidores. |
| **`config_apis.html`** | `/config-apis` | Registro de APIs (archivo, método HTTP, URL, frecuencia). |
| **`config_server_apis.html`** | `/config-server-apis` | Tabla con checkboxes servidor × API. |

---

## Rutas principales (`app.py`)

| Ruta | Descripción |
|------|-------------|
| **`/`** | Tablero (`dashboard.get_dashboard_data()`). |
| **`/api/invoke`**, **`/api/invokeWhatsapp`**, **`/api/invokeNoContestadas`**, etc. | Invocación manual de APIs Wolkvox → CSV en `downloads/` y opcionalmente BigQuery. |
| **`/config-bigquery`** | CRUD campañas + sync/test-count BigQuery. |
| **`/config-servers`** | Parametrización de servidores. |
| **`/config-apis`** | Parametrización de APIs. |
| **`/config-server-apis`** | Activar/desactivar API por servidor. |
| **`/api/recent_logs`** | Últimas líneas de `execution_log.txt` (tablero). |
| **`/downloads/<archivo>`** | Descarga de CSV generados. |

---

## Configuración y credenciales

| Ruta | Rol |
|------|-----|
| **`config.json`** | Datos operativos editables desde la app (evitar subir secretos reales a repositorios públicos). |
| **`config/google_key.json`** | Service account para BigQuery. |
| **`config/credentials.json`**, **`token.json`** | OAuth / tokens auxiliares. |
| **`requirements.txt`** | Dependencias Python (Flask, SQLAlchemy, requests, google-cloud-bigquery, APScheduler, etc.). |

---

## Archivos generados y auxiliares

| Ruta | Rol |
|------|-----|
| **`downloads/`** | CSV (y otros) generados al invocar APIs. |
| **`uploads/`** | Subidas programadas (scheduler). |
| **`execution_log.txt`** | Log de ejecución mostrado en el tablero. |
| **`script_sql.sql`** | Esquema de referencia para tablas BigQuery. |
| **`migrate_add_campaign_columns.py`**, **`inspect_db.py`** | Utilidades de migración/inspección SQLite. |
| **`sample_data.json`**, **`README.md`** | Datos de ejemplo y documentación del proyecto. |

---

## Flujo de persistencia

1. **Campañas** → SQLite (`app.db`) → opcional sync masivo a BigQuery.
2. **Servidores, APIs, activación por servidor** → `config.json` (clave `server_apis` para la matriz).
3. **Resultados de llamadas** → `downloads/*.csv` + tabla BigQuery `resultado_campana_llamada`.
4. **Ejecución de una API parametrizada** → `api_runner` + módulo en `api_handlers/`.

---

## Ejecución

```bash
python app.py
```

Abrir: **http://127.0.0.1:5000/**

Al cargar el módulo de APIs se ejecuta `ensure_demo_api()` para registrar **API DEMO** si no existe.

---

## Árbol de archivos (resumen)

```
Python14/
├── app.py                    # Entrada y rutas
├── backend.py                # Flask, scheduler, utilidades core
├── database.py               # Modelos SQLite
├── campaigns.py
├── servers.py
├── apis.py
├── server_apis.py
├── dashboard.py
├── api_runner.py
├── bigquery.py
├── conexion_bigquery.py
├── config.json
├── app.db
├── requirements.txt
├── api_handlers/
│   ├── __init__.py
│   └── DEMO.py
├── templates/
│   ├── layouts/adminlte.html
│   ├── index.html
│   ├── config_bigquery.html
│   ├── config_servers.html
│   ├── config_apis.html
│   └── config_server_apis.html
├── config/                   # Credenciales Google
├── downloads/                # CSV generados
└── uploads/                  # Archivos subidos
```
