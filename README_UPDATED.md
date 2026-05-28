# Wolkvox Contact Center - Sistema de Gestión de Campañas y APIs

## 📋 Descripción General

Sistema integral desarrollado en **Python con Flask** para la gestión centralizada de campañas de contact center, programación de tareas, integración con APIs externas y análisis de datos mediante BigQuery.

### 🎯 Objetivo Principal

Proporcionar una plataforma web robusta que permita:
- **Gestionar campañas** de contact center con programación automática
- **Configurar y ejecutar** consumo periódico de APIs externas
- **Cargar archivos CSV** y procesarlos según calendarios definidos
- **Integrar con BigQuery** para análisis avanzado de datos
- **Monitorear ejecuciones** de tareas programadas en tiempo real
- **Generar reportes XLSX** desde datos de Wolkvox

---

## 🏗️ Arquitectura del Sistema

```
┌─────────────────────────────────────────────────┐
│        FRONTEND (Flask + AdminLTE 4)            │
│  - Dashboard | Configuraciones | Reportes      │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│      CAPA DE APLICACIÓN (app.py)                │
│  - Rutas HTTP | Endpoints REST                 │
└──────────────────┬──────────────────────────────┘
                   │
      ┌────────────┼────────────┐
      │            │            │
┌─────▼──┐  ┌─────▼──┐  ┌─────▼──┐
│Backend │  │Database│  │BigQuery│
│(Lógica)│  │(SQLite)│  │(GCP)   │
└────────┘  └────────┘  └────────┘
      │            │            │
      └────────────┼────────────┘
                   │
    ┌──────────────▼──────────────┐
    │  APScheduler (Task Scheduler)│
    │  - Jobs cada minuto         │
    │  - Ejecuciones automáticas  │
    └─────────────────────────────┘
```

---

## 📁 Estructura de Archivos

### **Archivos Core**
| Archivo | Descripción |
|---------|-------------|
| `app.py` | Punto de entrada, define todas las rutas y endpoints Flask |
| `backend.py` | Lógica central, configuración de BD, scheduler APScheduler |
| `database.py` | Modelos SQLAlchemy (Campaña, Servidor, API, Flujo, Parámetros) |

### **Módulos de Configuración**
| Archivo | Descripción |
|---------|-------------|
| `servers.py` | CRUD para servidores externos (create, read, update, delete) |
| `apis.py` | Administración de endpoints API configurables |
| `flujos_proceso.py` | Gestión de flujos de proceso Wolkvox |
| `general_params.py` | Parámetros globales configurables de la aplicación |
| `server_apis.py` | Matriz de asignación servidor-API |

### **Módulos de Integración**
| Archivo | Descripción |
|---------|-------------|
| `bigquery.py` | Lógica de integración con Google BigQuery |
| `conexion_bigquery.py` | Cliente de conexión a BigQuery |
| `invocation_utils.py` | Utilidades para llamadas HTTP a APIs externas |
| `api_runner.py` | Orquestador de handlers de API específicos |

### **Módulos de Negocio**
| Archivo | Descripción |
|---------|-------------|
| `campaigns.py` | CRUD completo de campañas |
| `campaign_execution.py` | Lógica de ejecución de campañas programadas |
| `dashboard.py` | Lógica de métricas y panel de control |

### **Directorio de Handlers**
| Ubicación | Descripción |
|-----------|-------------|
| `api_handlers/` | Handlers específicos para cada tipo de API (Wolkvox, etc.) |
| `api_handlers/Wolkvox_Carga_Clientes.py` | Handler para cargar clientes en Wolkvox |
| `api_handlers/ConsultarCampanas.py` | Handler para consultar campañas |

### **Recursos Front-End**
| Ubicación | Descripción |
|-----------|-------------|
| `templates/layouts/adminlte.html` | Layout maestro con sidebar y navbar |
| `templates/index.html` | Dashboard principal |
| `templates/config_*.html` | Vistas de configuración (general, APIs, servidores, etc.) |
| `templates/reportes.html` | Vista para descarga de reportes XLSX |
| `templates/static/` | CSS, JavaScript, imágenes (AdminLTE) |

### **Almacenamiento**
| Ubicación | Descripción |
|-----------|-------------|
| `uploads/` | CSV subidos para procesamiento |
| `downloads/` | Reportes XLSX generados y respuestas de APIs |
| `app.db` | Base de datos SQLite (configuraciones persistentes) |

### **Configuración**
| Archivo | Descripción |
|---------|-------------|
| `config.json` | Tokens, URLs, credenciales (NO versionado) |
| `config/credentials.json` | Credenciales de servicios externos |
| `config/google_key.json` | Llave de servicio para Google Cloud |
| `token.json` | Token de autenticación OAuth local |
| `requirements.txt` | Dependencias de Python |

---

## 🚀 Funcionalidades del Sistema

### **1. Dashboard Principal** (`/`)
- Vista general del sistema
- Métricas de campañas activas
- Resumen de tareas programadas
- Información de servidores conectados
- Logs recientes de ejecuciones

### **2. Gestión de Campañas** (`/config-bigquery`)
- Crear, editar, eliminar campañas
- Programar ejecuciones por fecha/hora
- Asociar servidores y flujos de proceso
- Sincronización con BigQuery
- Estado de campañas (activa, pausada, completada)

### **3. Configuración de Servidores** (`/config-servers`)
- Registrar servidores externos (Wolkvox, etc.)
- Definir URLs base y credenciales
- Gestionar múltiples instancias
- Verificar conectividad

### **4. Administración de APIs** (`/config-apis`)
- Crear endpoints personalizados
- Configurar métodos HTTP (GET, POST, PUT, DELETE)
- Definir headers y autenticación
- Reutilizar entre campañas
- Sistema de demo APIs para pruebas

### **5. Configuración de Flujos de Proceso** (`/config-flujos-proceso`)
- Crear flujos de proceso Wolkvox
- Asociar con campañas específicas
- Definir parámetros y configuraciones
- Validación automática de compatibilidad

### **6. Matriz de Asignación APIs-Servidores** (`/config-server-apis`)
- Vincular APIs con servidores específicos
- Activar/desactivar combinaciones
- Matriz de validación automática
- Control de acceso por servidor

### **7. Parámetros Generales** (`/config-general`)
- Intervalo de revisión de tareas (minutos)
- Límites de reintentos
- Configuración de logging
- Tokens y credenciales
- Timeouts y ajustes de rendimiento

### **8. Descargar Reportes** (`/reportes`)
- Generar reportes XLSX desde Wolkvox
- Seleccionar servidor y rango de fechas
- Exportación automática de datos
- Disponible en `downloads/`

---

## 🔄 Flujo de Trabajo Automático

### **Programador de Tareas (APScheduler)**

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

### **Ejemplo: Ciclo de Ejecución de Campaña**

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

## 🛠️ Guía de Desarrollo

### **Instalación**

```bash
# 1. Clonar repositorio
git clone <repo_url>
cd Python14

# 2. Crear entorno virtual
python -m venv venv
venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
cp config/example_google_key.json config/google_key.json
# Editar con credenciales reales

# 5. Ejecutar aplicación
python app.py
```

**Acceder en navegador:** `http://127.0.0.1:5000/`

### **Modo Desarrollo**

- Debug habilitado por defecto en `app.py`
- Auto-recarga de cambios en templates
- Logs detallados en consola y `execution_log.txt`
- Base de datos SQLite local (`app.db`)

### **Despliegue en Producción**

```bash
# Usar servidor WSGI como Gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# O usar uWSGI
pip install uwsgi
uwsgi --http :5000 --wsgi-file app.py --callable app
```

**Configuración recomendada:**
- Desactivar `debug=True`
- Usar variables de entorno para credenciales
- Configurar proxy inverso (Nginx/Apache)
- Implementar HTTPS/SSL
- Backups automáticos de `app.db`

---

## 📋 Conceptos Clave

### **APScheduler (Programador de Tareas)**
- Ejecuta jobs periódicos (cada minuto)
- Tres tipos de tareas: cargas CSV, consumo APIs, consultas programadas
- Reinicio automático si la aplicación falla
- Logs detallados de cada ejecución

### **SQLAlchemy ORM**
- Modelos de datos en `database.py`
- Relaciones entre entidades mediante claves foráneas
- Migraciones manuales (usar `db.create_all()`)
- Soporte para SQLite, PostgreSQL, MySQL

### **BigQuery Integration**
- Cliente autenticado mediante `google_key.json`
- Sincronización bidireccional de campañas
- Consultas programadas con almacenamiento de resultados
- Manejo centralizado en `conexion_bigquery.py`

### **Sistema de Handlers**
- Nuevos handlers se crean en `api_handlers/`
- Se registran automáticamente en `api_runner.py`
- Reciben parámetros de campaña y servidor
- Retornan respuesta serializable (JSON/CSV)

### **Logging y Monitoreo**
- `execution_log.txt`: Registro rotativo de 3000 líneas
- Console output: Información en tiempo real
- Base de datos: Histórico de campañas y ejecuciones
- Alertas: Disponibles para configurar por error

---

## 🔌 Integración con Sistemas Externos

### **Wolkvox**
- Header `wolkvox-token` en todas las requests
- Endpoints específicos para cargas de clientes
- Consultas de campañas activas
- Exportación de reportes
- **Ruta:** `/reports/download`

### **Google BigQuery**
- Credenciales JSON en `config/google_key.json`
- Dataset: Configurable en `general_params`
- Tablas automáticas para campañas
- Análisis y reportes avanzados
- **Ruta:** `/config-bigquery`

### **APIs Personalizadas**
- Soporte para cualquier endpoint HTTP
- Métodos: GET, POST, PUT, DELETE, PATCH
- Headers personalizados
- Autenticación flexible
- **Ruta:** `/config-apis`

---

## 📊 Modelo de Datos

### **Tabla: Campañas**
```sql
- id (PK)
- name
- description
- tipo_campana (enum)
- servidor_id (FK)
- flujo_id (FK)
- scheduled_time
- status (pending/in_progress/completed/failed)
- created_at
- updated_at
```

### **Tabla: Servidores**
```sql
- id (PK)
- name
- url
- api_key
- is_active
- created_at
```

### **Tabla: APIs**
```sql
- id (PK)
- name
- endpoint
- http_method
- headers (JSON)
- is_system_api
- created_at
```

### **Tabla: Flujos de Proceso**
```sql
- id (PK)
- name
- servidor_id (FK)
- configuration (JSON)
- created_at
```

### **Tabla: Parámetros Generales**
```sql
- parameter_name (PK)
- parameter_value
- updated_at
```

---

## 🔐 Seguridad

### **Buenas Prácticas Implementadas**
✅ Tokens NO expuestos en URLs (se envían como headers)
✅ Credenciales en archivos NO versionados
✅ Validación de inputs en backend
✅ CORS configurado según necesidad
✅ SQL Injection prevention mediante ORM
✅ Contraseñas hasheadas (si aplica)

### **Recomendaciones para Producción**
- [ ] Implementar autenticación de usuarios (OAuth/JWT)
- [ ] Configurar rate limiting en API endpoints
- [ ] Agregar HTTPS/SSL
- [ ] Implementar logs de auditoría
- [ ] Backups automatizados de BD
- [ ] Monitoreo y alertas de errores
- [ ] Validación de certificados para APIs externas

---

## 📝 Cómo Agregar Nuevas Funcionalidades

### **1. Agregar Nueva Opción al Sidebar**

**Archivo:** `templates/layouts/adminlte.html`

```html
<li class="nav-header">NUEVA SECCIÓN</li>
<li class="nav-item">
    <a href="/nueva-ruta" class="nav-link {% if current_endpoint == 'nueva_funcion' %}active{% endif %}">
        <i class="nav-icon fas fa-icono"></i>
        <p>Nombre Visible</p>
    </a>
</li>
```

**Iconos útiles:**
- Reportes: `fa-file-excel`, `fa-chart-bar`, `fa-download`
- Configuración: `fa-cog`, `fa-sliders-h`, `fa-gear`
- Dashboard: `fa-tachometer-alt`, `fa-home`
- Datos: `fa-database`, `fa-table`, `fa-chart-line`
- Acciones: `fa-play`, `fa-stop`, `fa-refresh`, `fa-upload`

### **2. Crear Nueva Ruta en Flask**

**Archivo:** `app.py`

```python
@app.route("/nueva-ruta")
def nueva_funcion():
    """Descripción de la nueva función."""
    data = cargar_datos()
    return render_template("nueva_template.html", data=data)

@app.route("/nueva-ruta", methods=["POST"])
def nueva_funcion_post():
    """Procesar datos POST."""
    datos = request.get_json()
    resultado = procesar(datos)
    return jsonify(resultado)
```

### **3. Crear Nueva Vista HTML**

**Archivo:** `templates/nueva_template.html`

```html
{% extends "layouts/adminlte.html" %}

{% block title %}Nuevo Módulo{% endblock %}
{% block page_title %}Nuevo Módulo{% endblock %}
{% block breadcrumb %}<li class="breadcrumb-item active">Nuevo</li>{% endblock %}

{% block content %}
<div class="card card-outline card-primary">
  <div class="card-header">
    <h3 class="card-title"><i class="fas fa-icono"></i> Título</h3>
  </div>
  <div class="card-body">
    <!-- Contenido aquí -->
  </div>
</div>
{% endblock %}
```

### **4. Agregar Modelo de Base de Datos** (si necesita almacenamiento)

**Archivo:** `database.py`

```python
class NuevaEntidad(db.Model):
    __tablename__ = "nueva_entidad"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<NuevaEntidad {self.name}>"
```

Luego ejecutar:
```python
db.create_all()
```

### **5. Agregar Nuevo Handler de API** (si requiere consumir APIs)

**Archivo:** `api_handlers/MiHandler.py`

```python
def execute_mi_handler(servidor_config, parametros_campana):
    """
    Ejecuta acción específica con servidor externo.
    
    Args:
        servidor_config: Dict con URL, credenciales del servidor
        parametros_campana: Dict con parámetros de la campaña
    
    Returns:
        Dict con resultado {'status': 'success|error', 'data': ...}
    """
    try:
        url = servidor_config['url'] + '/mi-endpoint'
        headers = {
            'wolkvox-token': servidor_config['api_key'],
            'Content-Type': 'application/json'
        }
        
        response = requests.post(url, json=parametros_campana, headers=headers, timeout=30)
        response.raise_for_status()
        
        return {'status': 'success', 'data': response.json()}
    
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
```

Registrar en `api_runner.py`:
```python
from api_handlers.MiHandler import execute_mi_handler

HANDLERS = {
    'mi_handler': execute_mi_handler,
    # ... otros handlers
}
```

### **6. Agregar Job Programado** (si requiere ejecución periódica)

**Archivo:** `backend.py`

```python
def mi_job_periodico():
    """Job que se ejecuta cada minuto."""
    try:
        datos = hacer_algo()
        logger.info(f"Job completado: {datos}")
    except Exception as e:
        logger.error(f"Error en job: {e}")

# Agregar al scheduler en init_scheduler()
def init_scheduler():
    # ... jobs existentes ...
    
    scheduler.add_job(
        mi_job_periodico,
        'interval',
        minutes=1,
        id='mi_job',
        replace_existing=True
    )
```

---

## 🐛 Troubleshooting

### **Problema: Base de datos corrupta**
```bash
# Eliminar y recrear
rm app.db
python app.py
```

### **Problema: Scheduler no ejecuta tareas**
```python
# Verificar en backend.py que scheduler está iniciado
logger.info(f"Scheduler jobs: {scheduler.get_jobs()}")
```

### **Problema: BigQuery connection error**
```python
# Verificar credenciales y archivo google_key.json
from conexion_bigquery import get_bigquery_client
try:
    client = get_bigquery_client()
    print("Conexión OK")
except Exception as e:
    print(f"Error: {e}")
```

### **Problema: APIs no responden**
- Verificar URL del servidor
- Revisar headers (especialmente `wolkvox-token`)
- Validar método HTTP (GET/POST/etc)
- Revisar execution_log.txt para detalles

---

## 📚 Dependencias Principales

```
Flask==2.3.2
Flask-SQLAlchemy==3.0.5
APScheduler==3.10.4
Requests==2.31.0
google-cloud-bigquery==3.11.1
pandas==1.5.3
openpyxl==3.10.6
python-dotenv==1.0.0
```

Instalar: `pip install -r requirements.txt`

---

## 📞 Contacto y Soporte

- **Repositorio:** [GitHub URL]
- **Issues:** Crear issue en repositorio
- **Documentación:** Ver `ESTRUCTURA_APLICACION.md`

---

## 📄 Licencia

Este proyecto es **propietario y de uso interno**. Todos los derechos reservados.

---

**Última actualización:** Mayo 2026
**Versión:** 1.0
**Autor:** Equipo de Desarrollo
