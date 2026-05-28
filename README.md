# Proyecto Python14

Resumen
-------

Este repositorio contiene una aplicación Python para gestión de campañas y ejecución de APIs relacionadas. Incluye componentes para ejecutar servidores, manejar APIs, conectar con BigQuery, y procesar flujos de datos.

Requisitos
---------

- Python 3.8 o superior
- Crear y activar un entorno virtual (recomendado)
- Instalar dependencias:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Instalación y configuración
---------------------------

- Copia `config/credentials.json` y `config/google_key.json` con credenciales válidas.
- Asegúrate de tener `token.json` (autenticación local) si la aplicación lo requiere.
- Revisa `config.json` para parámetros generales.

Estructura del proyecto (resumen)
---------------------------------

- `app.py`: Punto de entrada principal para ejecutar la aplicación web/configuración.
- `backend.py` / `backend_antiguo.py`: Lógica de backend y procesos históricos.
- `api_runner.py`: Runner para ejecutar llamadas a APIs en lote.
- `apis.py` / `server_apis.py`: Definición y manejo de rutas/consumos de APIs.
- `database.py` / `conexion_bigquery.py` / `bigquery.py`: Conexión y utilidades para BigQuery y DB.
- `campaigns.py`, `campaign_execution.py`: Funcionalidad relacionada con campañas.
- `models.py`: Modelos de datos usados en la aplicación.
- `templates/` y `layouts/`: Plantillas HTML para la interfaz.
- `api_handlers/`: Controladores/handlers por API (ej. `Wolkvox_Carga_Clientes.py`, `ConsultarCampanas.py`).
- `requirements.txt`: Dependencias del proyecto.
- `sample_data.json`: Datos de ejemplo para pruebas locales.

Uso básico
---------

- Ejecutar la aplicación web (si aplica):

```bash
python app.py
```

- Ejecutar el backend o procesos de campaña:

```bash
python backend.py
```

- Correr jobs/API runner:

```bash
python api_runner.py
```

Archivos de configuración clave
------------------------------

- `config/credentials.json`: Credenciales para servicios externos.
- `config/google_key.json`: Llave de servicio para Google/BigQuery.
- `config.json`: Parámetros generales de la aplicación.
- `token.json`: Token de autenticación local (OAuth o similar).

Desarrollo y pruebas
--------------------

- Usa `sample_data.json` para pruebas unitarias manuales.
- Añade tests y ejecuta con tu runner preferido (no hay tests incluidos por defecto).

Dónde empezar a leer el código
------------------------------

- Revisa `app.py` para entender el flujo de inicio.
- Mira `api_handlers/` para ejemplos de cómo se estructuran los endpoints y cargas de clientes.
- Revisa `database.py` y `conexion_bigquery.py` para ver cómo se manejan las conexiones y consultas.

Notas y recomendaciones
----------------------

- Mantén las credenciales fuera del control de versiones.
- Usa un entorno virtual y documenta cualquier cambio en `requirements.txt`.
- Si planeas desplegar, revisa dependencias y configuraciones de producción (variables de entorno, permisos de Google Cloud, etc.).

Contacto
-------

Para dudas sobre el código, abre un issue o contacta al autor del repositorio.
# Flask CSV Upload / Download App with Scheduling

## Descripción del Proyecto

Esta es una aplicación web desarrollada en Python utilizando el framework Flask y la plantilla AdminLTE 4. Su propósito principal es permitir la carga, lectura y descarga de archivos CSV, junto con funcionalidades avanzadas de programación para la ejecución automática de tareas relacionadas con APIs externas y procesamiento de datos.

La aplicación está diseñada para integrarse con sistemas externos (como Wolkvox y BigQuery) mediante la programación de:
- Cargas de archivos CSV a APIs específicas en fechas/horas determinadas
- Consumo periódico de endpoints API configurados
- Ejecución de consultas programadas a APIs remotas con almacenamiento de resultados

Incluye una interfaz web intuitiva para gestionar todas estas funcionalidades mediante paneles de configuración.

## Arquitectura Técnica

El proyecto sigue una arquitectura modular basada en componentes Flask con separación clara de responsabilidades:

### Componentes Principales

1. **Capa de Aplicación (`app.py`)**:
   - Punto de entrada principal que inicializa la aplicación Flask
   - Define todas las rutas HTTP y endpoints de la API
   - Maneja la lógica de presentación y coordinación entre módulos
   - Integra el programador de tareas (APScheduler) para ejecuciones automáticas

2. **Backend (`backend.py`)**:
   - Contiene la lógica central de la aplicación
   - Configura la base de datos SQLite mediante SQLAlchemy
   - Inicializa el programador de tareas (APScheduler)
   - Define funciones auxiliares para logging, manejo de configuración y ejecución de tareas
   - Gestiona la conexión global a BigQuery

3. **Módulos Especializados**:
   - `campaigns.py`: Gestión de campañas (CRUD) en la base de datos
   - `servers.py`: Configuración y gestión de servidores externos
   - `apis.py`: Administración de endpoints API externos
   - `flujos_proceso.py`: Manejo de flujos de proceso Wolkvox
   - `general_params.py`: Parámetros configurables de la aplicación
   - `invocation_utils.py`: Utilidades para invocar APIs externas
   - `bigquery.py`: Integración con Google BigQuery
   - `conexion_bigquery.py`: Cliente de conexión a BigQuery
   - `server_apis.py`: Matriz de asignación servidor-API
   - `dashboard.py`: Lógica para el panel de control principal
   - `api_runner.py`: Ejecución de handlers de API específicos
   - `database.py`: Definición de modelos SQLAlchemy

### Flujo de Trabajo

1. Al iniciar la aplicación:
   - Se crea la base de datos SQLite si no existe
   - Se inicializa la conexión a BigQuery
   - Se cargan todas las configuraciones desde archivos JSON y base de datos
   - Se programa el job principal que se ejecuta cada minuto para:
     * Verificar y ejecutar cargas CSV programadas
     * Consumir endpoints API configurados
     * Ejecutar consultas programadas a APIs remotas

2. Interfaz de Usuario:
   - **Dashboard (`/`)**: Vista general con métricas y actividades recientes
   - **Config CSV (`/config-csv`)**: Gestión de cargas CSV programadas
   - **Config APIs (`/config-apis`)**: Configuración de endpoints API externos
   - **Config Servidores (`/config-servers`)**: Administración de servidores externos
   - **Config Flujos (`/config-flujos-proceso`)**: Manejo de flujos de proceso Wolkvox
   - **Config BigQuery (`/config-bigquery`)**: Gestión de campañas y consultas a BigQuery
   - **Config General (`/config-general`)**: Parámetros de funcionamiento de la aplicación

## Desglose de Archivos Importantes

### Archivos Core
- `app.py`: Punto de entrada y definiión de rutas web
- `backend.py`: Lógica central, configuración de BD y scheduler
- `database.py`: Modelos SQLAlchemy (Campaña, Servidor, API, etc.)

### Módulos de Configuración
- `servers.py`: CRUD de servidores externos
- `apis.py`: Gestión de endpoints API externos
- `flujos_proceso.py`: Administración de flujos de proceso
- `general_params.py`: Parámetros configurables (intervalos, límites)
- `config.json`: Almacenamiento de tokens y configuraciones sensibles

### Módulos de Integración
- `bigquery.py` y `conexion_bigquery.py`: Integración con Google BigQuery
- `invocation_utils.py`: Utilidades para llamadas a APIs externas
- `api_runner.py`: Ejecución específica de handlers de API (Wolkvox_Carga_Clientes, etc.)

### Módulos de Lógica de Negocio
- `campaigns.py`: Gestión completa de campañas (creación, lectura, actualización, eliminación)
- `dashboard.py`: Lógica para el panel de control y métricas
- `server_apis.py`: Matriz de asignación entre servidores y APIs

### Recursos Estáticos
- `templates/`: Plantillas HTML usando AdminLTE 4
- `static/`: Assets CSS, JavaScript e imágenes
- `uploads/`: Directorio para almacenar CSV subidos
- `downloads/`: Directorio para respuestas de consultas programadas

### Otros Archivos Relevantes
- `requirements.txt`: Dependencias de Python
- `execution_log.txt`: Registro de ejecuciones automáticas (rotado automáticamente)
- `app.db`: Base de datos SQLite
- `sample_data.json`: Ejemplo de estructura de datos

## Conceptos Clave y Dependencias

### Dependencias Principales
- **Flask**: Framework web para Python
- **Flask-SQLAlchemy**: ORM para manejo de base de datos
- **APScheduler**: Programador de tareas para ejecuciones periódicas
- **Requests**: Biblioteca para hacer peticiones HTTP
- **Google Cloud BigQuery**: Cliente para integración con BigQuery
- **Pandas**: Utilizado en algunos helpers para procesamiento de datos (implícito en algunos módulos)
- **AdminLTE 4**: Plantilla HTML/CSS/JS para la interfaz de usuario (incluida en templates/)

### Conceptos Esenciales para Desarrolladores

1. **Programador de Tareas (APScheduler)**:
   - El job principal se ejecuta cada minuto (`backend.py`)
   - Tres tipos de tareas programadas:
     * Cargas CSV a APIs en fechas/horas específicas
     * Consumo de endpoints API configurados (cada minuto)
     * Consultas programadas a APIs remotas con frecuencia personalizada

2. **Manejo de Configuración**:
   - Configuración general en `config.json` y tabla `general_parameters` en BD
   - Los cambios en configuración requieren recargar trabajos programados
   - Tokens de autenticación se manejan de forma centralizada

3. **Integración con APIs Externas**:
   - Todas las peticiones incluyen el header `wolkvox-token`
   - Utiliza un sistema de handlers específicos para diferentes tipos de API
   - Los handlers se encuentran en `api_handlers/` y son invocados mediante `api_runner.py`

4. **Flujo de Datos**:
   - CSV subidos → almacenados en `uploads/` → procesados según programación
   - Respuestas de APIs → convertidas a CSV → almacenadas en `downloads/`
   - Logs de ejecución → `execution_log.txt` (rotado a 3000 líneas)

5. **Base de Datos**:
   - SQLite (`app.db`) para almacenar configuraciones persistentes
   - Tablas principales: Campañas, Servidores, APIs, Flujos de Proceso, Parámetros Generales
   - Relaciones entre entidades mediante claves foráneas

6. **Extensibilidad**:
   - Nuevos handlers de API se agregan en `api_handlers/` y se registran en `api_runner.py`
   - Nuevos tipos de configuración se añaden mediante módulos similares a los existentes
   - La arquitectura permite agregar nuevos tipos de tareas programadas fácilmente

### Buenas Prácticas Implementadas

- **Separación de Responsabilidades**: Cada módulo tiene un propósito bien definido
- **Manejo Centralizado de Errores**: Logging consistente mediante el módulo `logging`
- **Seguridad**: Tokens no se exponen en URLs, se envían como headers
- **Escalabilidad**: Diseño modular que facilita la adición de nuevas funcionalidades
- **Mantenibilidad**: Código organizado con nombres descriptivos y comentarios explicativos

## Instrucciones de Desarrollo

1. **Clonar el repositorio**
2. **Crear entorno virtual** (recomendado):
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```
3. **Instalar dependencias**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Ejecutar la aplicación**:
   ```bash
   python app.py
   ```
5. **Acceder en el navegador**: `http://127.0.0.1:5000/`

### Notas de Desarrollo
- El modo debug está activado por defecto (`debug=True` en `app.py`)
- Para producción, desactivar debug y usar un servidor WSGI como Gunicorn
- Los cambios en la base de datos requieren migraciones manuales (actualmente usando `db.create_all()`)
- Los archivos de configuración sensible (`config.json`, `google_key.json`, etc.) no están versionados

## Licencia

Este proyecto es de uso interno y propietario. Todos los derechos reservados.
