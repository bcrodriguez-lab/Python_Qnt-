import subprocess
import pandas as pd
import pandas_gbq
from google.cloud import bigquery
from google.api_core.exceptions import NotFound
import gspread
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2 import service_account

# Ruta a tu llave JSON de Google Cloud (MUY IMPORTANTE)
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
PROJECT_ID = "capable-arbor-209819"
PATH_TO_JSON_KEY = CONFIG_DIR / "google_key.json"

# Paths de credenciales y fallback
ENV_CREDENTIAL_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GOOGLE_KEY_PATH")
CREDENTIAL_PATHS = []
if ENV_CREDENTIAL_PATH:
    CREDENTIAL_PATHS.append(Path(ENV_CREDENTIAL_PATH))
CREDENTIAL_PATHS.extend([
    PATH_TO_JSON_KEY,
    BASE_DIR / "google_key.json",
])

creds = None
client = None
selected_key_path = None
for key_path in CREDENTIAL_PATHS:
    try:
        if key_path and key_path.exists():
            selected_key_path = key_path
            creds = service_account.Credentials.from_service_account_file(str(key_path))
            client = bigquery.Client(credentials=creds, project=PROJECT_ID)
            print(f"[OK] Credenciales cargadas desde: {key_path}")
            break
    except Exception as e:
        print(f"[ERROR] Error al cargar credenciales desde {key_path}: {e}")
        selected_key_path = None
        client = None

if client is None:
    tried_paths = [str(path) for path in CREDENTIAL_PATHS]
    print("[ERROR] No se pudo inicializar el cliente de BigQuery.")
    print("        Se intentaron estas rutas de credenciales:")
    for path in tried_paths:
        print(f"        - {path}")
    if ENV_CREDENTIAL_PATH:
        print("        Usa GOOGLE_APPLICATION_CREDENTIALS o GOOGLE_KEY_PATH para indicar la ruta correcta.")
    else:
        print("        Coloca tu archivo google_key.json en config/ o en la raíz del proyecto.")
    try:
        auth_path = ENV_CREDENTIAL_PATH or str(PATH_TO_JSON_KEY)
        if ENV_CREDENTIAL_PATH or Path(auth_path).exists():
            print(f"[INFO] Intentando autenticar gcloud con: {auth_path}")
            subprocess.check_call([
                "gcloud", "auth", "activate-service-account",
                f"--key-file={auth_path}"
            ])
            print("[OK] gcloud autenticado")
            creds = service_account.Credentials.from_service_account_file(auth_path)
            client = bigquery.Client(credentials=creds, project=PROJECT_ID)
            print("[OK] Cliente BigQuery inicializado con gcloud.")
    except FileNotFoundError:
        print("[WARN] gcloud no está instalado o no está en el PATH")
        print("       La aplicación usará las credenciales del JSON si están disponibles.")
    except Exception as e:
        print(f"[WARN] Error de autenticación gcloud: {e}")

# 1. Definir Scopes y nombre de archivo de sesión
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/bigquery',
    'https://www.googleapis.com/auth/cloud-platform'
]
TOKEN_FILE = str(BASE_DIR / "config" / "token.json")  # Usaremos .json para mayor compatibilidad con las librerías modernas


def authenticate_google():
    """Autentica al usuario y gestiona el token de sesión persistente."""
    creds = None
    # Verifica si ya existe una sesión guardada
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Si no hay credenciales válidas (primera vez o expirado)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Configuración manual basada en tus parámetros de cliente OAuth2
            # Asegúrate de tener el archivo 'credentials.json' en la misma carpeta
            flow = InstalledAppFlow.from_client_secrets_file(str(BASE_DIR / "config" / "credentials.json"), SCOPES)
            creds = flow.run_local_server(port=0)

        # Guardar el token para que 'con el json sea suficiente' la próxima vez
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return creds


def get_bigquery_client():
    """Obtiene el cliente de BigQuery configurado y autenticado."""
    if client is None:
        raise RuntimeError(
            f"Cliente de BigQuery no inicializado. "
            f"Verifica que el archivo de credenciales exista en: {PATH_TO_JSON_KEY} "
            f"o define GOOGLE_APPLICATION_CREDENTIALS/GOOGLE_KEY_PATH."
        )
    return client