# Guía Rápida: Agregar Nuevas Funcionalidades al Sistema

## 📌 Resumen Ejecutivo

Este documento explica el **patrón estándar** para agregar nuevas funcionalidades al sistema Wolkvox Contact Center. Todos los módulos siguen una arquitectura consistente que facilita la extensión.

---

## 🎯 Patrón General de Integración

Cada nueva funcionalidad sigue estos **4 pasos principales**:

### **PASO 1: Agregar Enlace al Sidebar**
### **PASO 2: Crear Ruta Flask (Backend)**
### **PASO 3: Crear Plantilla HTML (Frontend)**
### **PASO 4: (Opcional) Agregar Modelo de Base de Datos**

---

## 📋 Paso 1: Agregar al Sidebar

**Archivo a modificar:** `templates/layouts/adminlte.html`

### **Estructura HTML**

```html
<!-- Crear una nueva sección o agregar a existente -->
<li class="nav-header">NUEVA SECCIÓN</li>

<!-- Agregar item de menú -->
<li class="nav-item">
    <a href="/ruta-nueva" class="nav-link {% if current_endpoint == 'nombre_endpoint' %}active{% endif %}">
        <i class="nav-icon fas fa-icono"></i>
        <p>Nombre Visible en Menú</p>
    </a>
</li>
```

### **Explicación de Elementos**

| Elemento | Descripción |
|----------|-------------|
| `nav-header` | Crea sección de título (INICIO, PARAMETRIZACIÓN, etc.) |
| `nav-item` | Item individual del menú |
| `href="/ruta"` | Ruta que maneja Flask (debe coincidir con `@app.route`) |
| `{% if current_endpoint == '...' %}active{% endif %}` | Marca el link como activo cuando es la página actual |
| `fas fa-icono` | Icono Font Awesome (ver tabla abajo) |
| `<p>Texto</p>` | Etiqueta visible en el menú |

### **Tabla de Iconos Font Awesome Útiles**

| Icono | Código | Caso de Uso |
|-------|--------|-----------|
| 📊 Gráfico | `fa-chart-bar` | Reportes y estadísticas |
| 📥 Excel | `fa-file-excel` | Descargas XLSX |
| 💾 Base de datos | `fa-database` | Campañas y almacenamiento |
| ⚙️ Configuración | `fa-cog` | Parámetros |
| 🖥️ Servidor | `fa-server` | Servidores externos |
| 🔗 Plug | `fa-plug` | APIs y conexiones |
| 📤 Upload | `fa-upload` | Carga de archivos |
| 📥 Download | `fa-download` | Descarga de datos |
| 🔄 Flujo | `fa-project-diagram` | Procesos y flujos |
| 🏠 Inicio | `fa-home` | Dashboard |
| 📋 Tabla | `fa-table` | Listados |
| ✓ Éxito | `fa-check` | Estados completados |

### **Ejemplo: Agregar "Auditoría"**

```html
<li class="nav-header">MONITOREO</li>
<li class="nav-item">
    <a href="/auditoria" class="nav-link {% if current_endpoint == 'auditoria' %}active{% endif %}">
        <i class="nav-icon fas fa-history"></i>
        <p>Auditoría de cambios</p>
    </a>
</li>
```

---

## 🐍 Paso 2: Crear Ruta Flask

**Archivo a modificar:** `app.py`

### **Estructura Básica**

```python
from flask import render_template, request, jsonify

# GET: Mostrar página
@app.route("/nueva-funcionalidad")
def nueva_funcionalidad():
    """Docstring describiendo la función."""
    
    # Cargar datos necesarios
    datos = cargar_datos_importantes()
    
    # Renderizar template con contexto
    return render_template("nueva_funcionalidad.html", datos=datos)


# POST: Procesar datos
@app.route("/nueva-funcionalidad", methods=["POST"])
def nueva_funcionalidad_post():
    """Procesar datos enviados por formulario."""
    
    # Obtener datos
    datos = request.get_json()  # para JSON
    # O: datos = request.form  # para formularios tradicionales
    
    try:
        # Procesar
        resultado = procesar_datos(datos)
        
        # Responder
        return jsonify({"status": "success", "data": resultado}), 200
    
    except Exception as e:
        logger.error(f"Error procesando: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
```

### **Detalles Importantes**

1. **El nombre de la función Flask debe coincidir con `current_endpoint` en el sidebar**
   ```html
   <!-- En sidebar -->
   {% if current_endpoint == 'nueva_funcionalidad' %}active{% endif %}
   
   <!-- En app.py -->
   def nueva_funcionalidad():  # ← Mismo nombre
   ```

2. **Importar las dependencias necesarias al inicio de `app.py`:**
   ```python
   from tu_modulo import tu_funcion
   ```

3. **Usar decoradores para validación (si aplica):**
   ```python
   @app.route("/admin-only")
   @login_required  # Si tienen sistema de autenticación
   def solo_admin():
       pass
   ```

### **Ejemplo Completo: Ruta de Auditoría**

```python
@app.route("/auditoria")
def auditoria():
    """Mostrar log de cambios de sistema."""
    
    # Obtener parámetro de paginación
    page = request.args.get('page', 1, type=int)
    
    # Consultar base de datos
    logs = db.session.query(AuditLog).order_by(
        AuditLog.timestamp.desc()
    ).paginate(page=page, per_page=50)
    
    return render_template("auditoria.html", logs=logs)


@app.route("/auditoria/export", methods=["POST"])
def auditoria_export():
    """Exportar logs a CSV."""
    
    fecha_inicio = request.json['fecha_inicio']
    fecha_fin = request.json['fecha_fin']
    
    logs = db.session.query(AuditLog).filter(
        AuditLog.timestamp.between(fecha_inicio, fecha_fin)
    ).all()
    
    # Crear CSV (pseudo-código)
    csv_buffer = crear_csv(logs)
    
    return send_file(csv_buffer, 
                    mimetype='text/csv',
                    as_attachment=True,
                    download_name='auditoria.csv')
```

---

## 🎨 Paso 3: Crear Plantilla HTML

**Archivo a crear:** `templates/nueva_funcionalidad.html`

### **Estructura Base**

```html
{% extends "layouts/adminlte.html" %}

{% block title %}Título de la Página{% endblock %}

{% block page_title %}Título Principal{% endblock %}

{% block page_subtitle %}<small class="text-muted">Subtítulo descriptivo</small>{% endblock %}

{% block breadcrumb %}
<li class="breadcrumb-item"><a href="/">Inicio</a></li>
<li class="breadcrumb-item active">Elemento Actual</li>
{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-12">
        <div class="card card-outline card-primary">
            <div class="card-header">
                <h3 class="card-title"><i class="fas fa-icono"></i> Encabezado</h3>
            </div>
            <div class="card-body">
                <!-- Contenido aquí -->
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
// JavaScript personalizado aquí
</script>
{% endblock %}
```

### **Componentes AdminLTE Comunes**

#### **Card (Tarjeta)**
```html
<div class="card card-outline card-primary">
    <div class="card-header">
        <h3 class="card-title">Título</h3>
    </div>
    <div class="card-body">
        Contenido
    </div>
</div>
```

#### **Form (Formulario)**
```html
<form id="miForm" method="post">
    <div class="form-group">
        <label for="campo">Campo:</label>
        <input type="text" id="campo" name="campo" class="form-control">
    </div>
    <button type="submit" class="btn btn-primary">Enviar</button>
</form>
```

#### **Table (Tabla)**
```html
<table class="table table-striped table-hover">
    <thead>
        <tr>
            <th>Columna 1</th>
            <th>Columna 2</th>
            <th>Acciones</th>
        </tr>
    </thead>
    <tbody>
        {% for item in items %}
        <tr>
            <td>{{ item.col1 }}</td>
            <td>{{ item.col2 }}</td>
            <td>
                <a href="#" class="btn btn-sm btn-info">Editar</a>
                <button class="btn btn-sm btn-danger">Eliminar</button>
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

#### **Alert (Alerta)**
```html
<div class="alert alert-success">✓ Operación exitosa</div>
<div class="alert alert-danger">✗ Error al procesar</div>
<div class="alert alert-info">ℹ Información</div>
<div class="alert alert-warning">⚠ Advertencia</div>
```

#### **Button (Botones)**
```html
<button class="btn btn-primary">Primario</button>
<button class="btn btn-success">Éxito</button>
<button class="btn btn-danger">Peligro</button>
<button class="btn btn-warning">Advertencia</button>
<button class="btn btn-info">Info</button>
```

### **Ejemplo Completo: Template de Auditoría**

```html
{% extends "layouts/adminlte.html" %}

{% block title %}Auditoría{% endblock %}
{% block page_title %}Auditoría de Sistema{% endblock %}
{% block page_subtitle %}<small class="text-muted">Historial de cambios</small>{% endblock %}

{% block breadcrumb %}
<li class="breadcrumb-item"><a href="/">Inicio</a></li>
<li class="breadcrumb-item active">Auditoría</li>
{% endblock %}

{% block content %}
<div class="row mb-3">
    <div class="col-md-12">
        <div class="card card-outline card-primary">
            <div class="card-header">
                <h3 class="card-title"><i class="fas fa-history"></i> Filtros</h3>
            </div>
            <div class="card-body">
                <form class="form-inline" id="filterForm">
                    <div class="form-group mr-2">
                        <label for="dateStart">Desde:</label>
                        <input type="date" id="dateStart" class="form-control ml-2">
                    </div>
                    <div class="form-group mr-2">
                        <label for="dateEnd">Hasta:</label>
                        <input type="date" id="dateEnd" class="form-control ml-2">
                    </div>
                    <button type="submit" class="btn btn-primary">Filtrar</button>
                    <button type="button" class="btn btn-secondary ml-2" onclick="document.getElementById('filterForm').reset()">Limpiar</button>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="row">
    <div class="col-md-12">
        <div class="card">
            <div class="card-header">
                <h3 class="card-title">Registros de Auditoría</h3>
            </div>
            <div class="card-body">
                <table class="table table-striped table-hover">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Usuario</th>
                            <th>Acción</th>
                            <th>Entidad</th>
                            <th>Cambios</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in logs.items %}
                        <tr>
                            <td>{{ log.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                            <td>{{ log.user }}</td>
                            <td><span class="badge badge-info">{{ log.action }}</span></td>
                            <td>{{ log.entity_type }}</td>
                            <td>
                                <button class="btn btn-sm btn-info" onclick="showDetails('{{ log.id }}')">Ver</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<!-- Paginación -->
<div class="row mt-3">
    <div class="col-md-12">
        <nav>
            <ul class="pagination">
                {% if logs.has_prev %}
                <li class="page-item">
                    <a class="page-link" href="?page=1">Primera</a>
                </li>
                <li class="page-item">
                    <a class="page-link" href="?page={{ logs.prev_num }}">Anterior</a>
                </li>
                {% endif %}
                
                <li class="page-item active">
                    <span class="page-link">{{ logs.page }}</span>
                </li>
                
                {% if logs.has_next %}
                <li class="page-item">
                    <a class="page-link" href="?page={{ logs.next_num }}">Siguiente</a>
                </li>
                <li class="page-item">
                    <a class="page-link" href="?page={{ logs.pages }}">Última</a>
                </li>
                {% endif %}
            </ul>
        </nav>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
function showDetails(logId) {
    alert('Mostrar detalles del log: ' + logId);
}
</script>
{% endblock %}
```

---

## 💾 Paso 4 (Opcional): Agregar Modelo de Base de Datos

**Archivo a modificar:** `database.py`

### **Crear Modelo SQLAlchemy**

```python
from datetime import datetime
from backend import db

class MiEntidad(db.Model):
    __tablename__ = "mi_entidad"
    
    # Columnas básicas
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.Text)
    valor = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relaciones (si aplica)
    servidor_id = db.Column(db.Integer, db.ForeignKey('servidor.id'))
    servidor = db.relationship('Servidor', backref='mi_entidad')
    
    def __repr__(self):
        return f"<MiEntidad {self.name}>"
    
    def to_dict(self):
        """Serializar a diccionario para JSON."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
```

### **Tipos de Columnas Comunes**

| Tipo | Descripción | Ejemplo |
|------|-------------|---------|
| `Integer` | Número entero | `db.Column(db.Integer)` |
| `String(n)` | Texto con límite | `db.Column(db.String(255))` |
| `Text` | Texto largo sin límite | `db.Column(db.Text)` |
| `Float` | Número decimal | `db.Column(db.Float)` |
| `Boolean` | Verdadero/Falso | `db.Column(db.Boolean)` |
| `DateTime` | Fecha y hora | `db.Column(db.DateTime)` |
| `Date` | Solo fecha | `db.Column(db.Date)` |
| `JSON` | Datos JSON | `db.Column(db.JSON)` |

### **Crear Tabla en Base de Datos**

Después de definir el modelo:

```python
# En bash/terminal
python

>>> from backend import app, db
>>> from database import MiEntidad
>>> with app.app_context():
...     db.create_all()
...     print("Tabla creada")
```

O más fácil en `app.py` (primera línea después de importes):
```python
with app.app_context():
    db.create_all()
```

---

## 🧪 Ejemplo Completo: Agregar "Gestión de Emails"

Seguimos todos los pasos:

### **1️⃣ Sidebar**
```html
<li class="nav-header">COMUNICACIÓN</li>
<li class="nav-item">
    <a href="/emails" class="nav-link {% if current_endpoint == 'emails' %}active{% endif %}">
        <i class="nav-icon fas fa-envelope"></i>
        <p>Gestión de Emails</p>
    </a>
</li>
```

### **2️⃣ Ruta Flask**
```python
@app.route("/emails")
def emails():
    """Mostrar listado de emails configurados."""
    emails_list = db.session.query(EmailTemplate).all()
    return render_template("emails.html", emails=emails_list)

@app.route("/emails", methods=["POST"])
def emails_create():
    """Crear nuevo email template."""
    data = request.get_json()
    
    email = EmailTemplate(
        name=data['name'],
        subject=data['subject'],
        body=data['body']
    )
    
    db.session.add(email)
    db.session.commit()
    
    logger.info(f"Email creado: {email.name}")
    return jsonify({"status": "success", "id": email.id}), 201
```

### **3️⃣ Template**
```html
{% extends "layouts/adminlte.html" %}
{% block title %}Emails{% endblock %}
{% block page_title %}Gestión de Email Templates{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h3 class="card-title">Plantillas</h3>
            </div>
            <div class="card-body">
                <div id="emailList">
                    {% for email in emails %}
                    <div class="alert alert-info">
                        <strong>{{ email.name }}</strong>
                        <button class="btn btn-sm btn-danger float-right" onclick="deleteEmail({{ email.id }})">Eliminar</button>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h3 class="card-title">Crear Nueva</h3>
            </div>
            <div class="card-body">
                <form id="emailForm">
                    <div class="form-group">
                        <label>Nombre</label>
                        <input type="text" name="name" class="form-control" required>
                    </div>
                    <div class="form-group">
                        <label>Asunto</label>
                        <input type="text" name="subject" class="form-control" required>
                    </div>
                    <div class="form-group">
                        <label>Cuerpo</label>
                        <textarea name="body" class="form-control" rows="5" required></textarea>
                    </div>
                    <button type="submit" class="btn btn-success">Guardar</button>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
document.getElementById('emailForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData);
    
    const resp = await fetch('/emails', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });
    
    if(resp.ok) {
        alert('Email creado');
        location.reload();
    }
});

function deleteEmail(id) {
    // Implementar delete
}
</script>
{% endblock %}
```

### **4️⃣ Modelo BD**
```python
class EmailTemplate(db.Model):
    __tablename__ = "email_template"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<EmailTemplate {self.name}>"
```

---

## 🎯 Checklist Final

Antes de dar por finalizada una nueva funcionalidad:

- [ ] ¿Está el enlace agregado al sidebar?
- [ ] ¿Coincide el `endpoint` con el nombre de la función Flask?
- [ ] ¿Existe la ruta Flask con decorador `@app.route`?
- [ ] ¿Existe el archivo template correspondiente?
- [ ] ¿Se hereda correctamente de `layouts/adminlte.html`?
- [ ] ¿Si necesita BD, está el modelo en `database.py`?
- [ ] ¿Se ejecutó `db.create_all()`?
- [ ] ¿Se probó en navegador (http://127.0.0.1:5000/nueva-ruta)?
- [ ] ¿El log muestra mensajes de éxito?
- [ ] ¿Se agregó logging con `logger.info()` o `logger.error()`?

---

## 📚 Referencias Rápidas

### **Imports Comunes en app.py**
```python
from flask import render_template, request, jsonify, send_file
from backend import app, db, logger
from datetime import datetime
import csv, json
```

### **Template Utilities**
```html
<!-- Loop -->
{% for item in items %}{{ item.name }}{% endfor %}

<!-- Condicional -->
{% if condition %}...{% endif %}

<!-- Formato de fecha -->
{{ date_obj.strftime('%Y-%m-%d') }}

<!-- Expresión ternaria -->
{{ 'Sí' if condicion else 'No' }}

<!-- JSON en JavaScript -->
<script>
const data = {{ mi_variable | tojson }};
</script>
```

### **Métodos Útiles de BD**
```python
# Crear
modelo = Clase(campo1=valor1)
db.session.add(modelo)
db.session.commit()

# Leer
items = db.session.query(Clase).all()
item = db.session.query(Clase).filter_by(id=1).first()

# Actualizar
item.campo = nuevo_valor
db.session.commit()

# Eliminar
db.session.delete(item)
db.session.commit()
```

---

**¡Ahora estás listo para extender el sistema! 🚀**
