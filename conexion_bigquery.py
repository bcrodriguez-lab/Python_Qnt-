import subprocess
import pandas as pd
import pandas_gbq
from google.cloud import bigquery
from google.api_core.exceptions import NotFound # Importa NotFound aquí
import gspread
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from google.api_core.exceptions import NotFound # Importa NotFound aquí
from google.oauth2 import service_account

# Ruta a tu llave JSON de Google Cloud (MUY IMPORTANTE)
BASE_DIR = Path(__file__).resolve().parent
PATH_TO_JSON_KEY = str(BASE_DIR / "config" / "google_key.json")

project_id = "capable-arbor-209819"

# Cargar las credenciales desde tu archivo JSON
creds = None
client = None

try:
    creds = service_account.Credentials.from_service_account_file(PATH_TO_JSON_KEY)
    client = bigquery.Client(credentials=creds, project=project_id)
    print(f"[OK] Credenciales cargadas desde: {PATH_TO_JSON_KEY}")
except FileNotFoundError as e:
    print(f"[ERROR] Archivo de credenciales no encontrado en {PATH_TO_JSON_KEY}")
    print(f"        Detalle: {e}")
except Exception as e:
    print(f"[ERROR] Error al cargar credenciales: {e}")

# Autenticar gcloud (opcional) si el cliente JSON no se pudo cargar
if client is not None:
    print("[OK] Cliente BigQuery inicializado con credenciales del JSON")
else:
    try:
        if PATH_TO_JSON_KEY:
            print(f"[INFO] Intentando autenticar gcloud con: {PATH_TO_JSON_KEY}")
            subprocess.check_call([
                    "gcloud", "auth", "activate-service-account",
                    f"--key-file={PATH_TO_JSON_KEY}"
            ])
            print("[OK] gcloud autenticado")
    except FileNotFoundError:
        print("[WARN] gcloud no esta instalado o no esta en el PATH")
        print("       La aplicacion usara las credenciales del JSON si estan disponibles")
    except Exception as e:
        print(f"[WARN] Error de autenticacion gcloud: {e}")

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
            f"Verifica que el archivo de credenciales exista en: {PATH_TO_JSON_KEY}"
        )
    return client