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


def _ensure_auto_campaign_columns():
    """Agrega columnas nuevas a auto_campaign si la tabla ya existia."""
    inspector = inspect(db.engine)
    if "auto_campaign" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("auto_campaign")}
    migrations = {
        "operation": "ALTER TABLE auto_campaign ADD COLUMN operation VARCHAR(255) DEFAULT ''",
        "type": "ALTER TABLE auto_campaign ADD COLUMN type VARCHAR(255) DEFAULT ''",
        "campaign_type": "ALTER TABLE auto_campaign ADD COLUMN campaign_type VARCHAR(50) DEFAULT ''",
        "description": "ALTER TABLE auto_campaign ADD COLUMN description TEXT DEFAULT ''",
        "start_date": "ALTER TABLE auto_campaign ADD COLUMN start_date DATETIME",
        "user_name": "ALTER TABLE auto_campaign ADD COLUMN user_name VARCHAR(255) DEFAULT ''",
        "last_precount": "ALTER TABLE auto_campaign ADD COLUMN last_precount INTEGER DEFAULT 0",
        "flujo_proceso_id": "ALTER TABLE auto_campaign ADD COLUMN flujo_proceso_id VARCHAR(100) DEFAULT ''",
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
        _ensure_auto_campaign_columns()


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


class AutoCampaign(db.Model):
    __tablename__ = "auto_campaign"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    operation = db.Column(db.String(255), nullable=True, default="")
    type = db.Column(db.String(255), nullable=True, default="")
    campaign_type = db.Column(db.String(50), nullable=True, default="")
    description = db.Column(db.Text, nullable=True, default="")
    start_date = db.Column(db.DateTime, nullable=True)
    user_name = db.Column(db.String(255), nullable=True, default="")
    bigquery_query = db.Column(db.Text, nullable=False)
    wolkvox_add_record_endpoint = db.Column(db.String(500), nullable=False)
    wolkvox_delete_records_endpoint = db.Column(db.String(500), nullable=True, default="")
    wolkvox_campaign_id = db.Column(db.String(100), nullable=False)
    server_name = db.Column(db.String(255), nullable=True, default="")
    flujo_proceso_id = db.Column(db.String(100), nullable=True, default="")
    field_mapping = db.Column(db.JSON, nullable=False, default=dict)
    schedule_type = db.Column(db.String(20), nullable=False, default="manual")
    schedule_value = db.Column(db.JSON, nullable=True)
    status = db.Column(db.Boolean, nullable=False, default=True)
    running = db.Column(db.Boolean, nullable=False, default=False)
    last_run = db.Column(db.DateTime, nullable=True)
    next_run = db.Column(db.DateTime, nullable=True)
    last_precount = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class AutoCampaignExecutionLog(db.Model):
    __tablename__ = "auto_campaign_execution_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    auto_campaign_id = db.Column(
        db.Integer,
        db.ForeignKey("auto_campaign.id"),
        nullable=False,
    )
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    records_fetched = db.Column(db.Integer, nullable=False, default=0)
    records_sent = db.Column(db.Integer, nullable=False, default=0)
    records_failed = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)
    csv_file_path = db.Column(db.String(500), nullable=True)
    report_file_path = db.Column(db.String(500), nullable=True)


class CampaignExecution(db.Model):
    __tablename__ = "campaign_execution"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaign_parametrization.id"), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(50), nullable=False)  # e.g., 'running', 'success', 'failed'
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    upload_success = db.Column(db.Boolean, nullable=False, default=False)
    message = db.Column(db.Text, nullable=True)


class CampaignLog(db.Model):
    __tablename__ = "campaign_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    campaign_execution_id = db.Column(db.Integer, db.ForeignKey("campaign_execution.id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    level = db.Column(db.String(20), nullable=False)  # e.g., 'INFO', 'WARNING', 'ERROR'
    message = db.Column(db.Text, nullable=False)
