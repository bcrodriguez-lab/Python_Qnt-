from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

db = SQLAlchemy()


def _ensure_campaign_columns():
    """Agrega columnas nuevas a campaign_parametrization si la tabla ya existía."""
    inspector = inspect(db.engine)
    if "campaign_parametrization" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("campaign_parametrization")}
    migrations = {
        "servidor": "ALTER TABLE campaign_parametrization ADD COLUMN servidor VARCHAR(255) DEFAULT ''",
        "api": "ALTER TABLE campaign_parametrization ADD COLUMN api VARCHAR(255) DEFAULT ''",
        "total_clientes": "ALTER TABLE campaign_parametrization ADD COLUMN total_clientes INTEGER DEFAULT 0",
        "clientes_llamados": "ALTER TABLE campaign_parametrization ADD COLUMN clientes_llamados INTEGER DEFAULT 0",
        "clientes_contactados": "ALTER TABLE campaign_parametrization ADD COLUMN clientes_contactados INTEGER DEFAULT 0",
        "ejecutada": "ALTER TABLE campaign_parametrization ADD COLUMN ejecutada INTEGER DEFAULT 0",
        "tipo_campana": "ALTER TABLE campaign_parametrization ADD COLUMN tipo_campana VARCHAR(50) DEFAULT ''",
        "flujo_proceso_id": "ALTER TABLE campaign_parametrization ADD COLUMN flujo_proceso_id VARCHAR(100) DEFAULT ''",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            db.session.execute(text(ddl))
    db.session.commit()


def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _ensure_campaign_columns()


class ScheduledCSV(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    api_url = db.Column(db.String(500), nullable=False)
    scheduled_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(50), default="pending")


class APIEndpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    frequency_minutes = db.Column(db.Integer, nullable=False, default=0)


class ScheduledQuery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    api_url = db.Column(db.String(500), nullable=False)
    frequency_minutes = db.Column(db.Integer, nullable=False)
    last_run = db.Column(db.DateTime, default=None)
    last_status = db.Column(db.String(50), default=None)  # EXEC or FAILED
    last_error = db.Column(db.String(500), default=None)
    last_execution_time = db.Column(db.DateTime, default=None)


class Campaign(db.Model):
    __tablename__ = "campaign_parametrization"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre = db.Column(db.String(255), nullable=False)
    descripcion = db.Column(db.Text, nullable=True, default="")
    operacion = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.String(255), nullable=False)
    tipo_campana = db.Column(db.String(50), nullable=False, default="")
    flujo_proceso_id = db.Column(db.String(100), nullable=True, default="")
    fecha_inicio = db.Column(db.DateTime, nullable=False)
    consulta = db.Column(db.Text, nullable=False)
    usuario = db.Column(db.String(255), nullable=False)
    servidor = db.Column(db.String(255), nullable=True, default="")
    api = db.Column(db.String(255), nullable=True, default="")
    total_clientes = db.Column(db.Integer, nullable=False, default=0)
    clientes_llamados = db.Column(db.Integer, nullable=False, default=0)
    clientes_contactados = db.Column(db.Integer, nullable=False, default=0)
    ejecutada = db.Column(db.Boolean, nullable=False, default=False)
