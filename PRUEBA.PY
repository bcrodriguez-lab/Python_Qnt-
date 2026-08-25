import pandas as pd
import requests
import json
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.colab import files
import unicodedata # Added for tilde removal

# ==================================================
# 🔐 CONFIGURACIÓN (NO TOCAR)
# ==================================================
API_KEY = "04ca14df6c5a47e6713cdb13e8272ab3-aa42acbb-c69f-4a29-a3f5-3ec8b2fae16d"
BASE_URL = "https://2knxjz.api.infobip.com"
SENDER_ID = "599982"
MAX_WORKERS = 3
BATCH_SIZE = 500
MAX_REINTENTOS = 2

# ==================================================
# 🧠 MAPEO DE FORMATOS INTELIGENTES
# ==================================================
def formatear_fecha(valor):
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    try:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                fecha = pd.to_datetime(valor, format=fmt)
                return fecha.strftime("%d/%m/%Y")
            except:
                continue
        fecha = pd.to_datetime(valor, dayfirst=False)
        return fecha.strftime("%d/%m/%Y")
    except:
        return str(valor).strip()

def formatear_porcentaje(valor):
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    valor = str(valor).strip().replace("%", "")
    try:
        num = float(valor.replace(",", "."))
        return f"{int(num)}%" if num == int(num) else f"{num}%"
    except:
        return valor + "%"

def formatear_moneda(valor):
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    valor = re.sub(r"[^\d]", "", str(valor))
    if valor == "":
        return ""
    return f"{int(valor):,}".replace(",", ".")

FORMATO_ESPECIAL = {
    "Fecha de Pago": formatear_fecha,
    "Fecha de Pago 2": formatear_fecha,
    "Porcentaje": formatear_porcentaje,
    "Porcentaje 2": formatear_porcentaje,
    "Valor a pagar": formatear_moneda,
    "Valor a pagar 2": formatear_moneda,
    "Cuotas": formatear_moneda,
    "Valor Oferta Esp": formatear_moneda,
    "Valor Oferta Esp 2": formatear_moneda,
}

def aplicar_formato_especial(df):
    for col, func in FORMATO_ESPECIAL.items():
        if col in df.columns:
            print(f"🪄 Formateando columna '{col}'...")
            df[col] = df[col].apply(func)
    return df

# ==================================================
# 📡 CLIENTE INFOBIP V2 (Payload personalizado)
# ==================================================
class InfobipSenderV2:
    def __init__(self, api_key, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"App {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def enviar_lote(self, lista_mensajes, reintentos=MAX_REINTENTOS):
      url = f"{self.base_url}/sms/2/text/advanced"
      payload = {
          "messages": lista_mensajes,
          "urlOptions": {
              "shortenUrl": True,
              "trackClicks": True,
              "removeProtocol": False
          }
      }

      for intento in range(1, reintentos + 1):
          try:
              resp = self.session.post(url, json=payload)
              if resp.status_code in [200, 201]:
                  data = resp.json()
                  data["_status_code"] = resp.status_code
                  return data
              else:
                  print(f"⚠️ Intento {intento}/{reintentos}: Error {resp.status_code} - {resp.text[:300]}")
                  if intento < reintentos:
                      time.sleep(2 * intento)
          except requests.exceptions.RequestException as e:
              print(f"⚠️ Intento {intento}/{reintentos}: Excepción de red: {e}")
              if intento < reintentos:
                  time.sleep(2 * intento)
      return None

# ==================================================
# 📱 LIMPIEZA DE NÚMEROS
# ==================================================
def limpiar_numero(num):
    if pd.isna(num):
        return None
    num = str(num)
    num = re.sub(r"\D", "", num)
    if num.startswith("57") and len(num) >= 12:
        return num
    if len(num) == 10 and num.startswith("3"):
        return "57" + num
    return None

def generar_link_personalizado(numero, base_url="https://qnt.com.co/llamar-qnt/"):
    return f"{base_url}?cliente={numero}"

# ==================================================
# 🧠 MANEJO DE VARIABLES
# ==================================================
def extraer_variables(texto):
    # Modified regex to be more explicit about capturing content inside delimiters
    # It captures any character that is not the closing delimiter
    # For {{...}}: captures characters that are not '}'
    # For [...]: captures characters that are not ']'
    matches = re.findall(r"\{\{([^\}]+)\}\}|\[([^\]]+)\]", texto)
    # The output of re.findall for this regex will be a list of tuples like [('VAR_NAME', ''), ('', 'VAR_NAME_2')]
    # We need to extract the non-empty string from each tuple.
    return [var_name for var_tuple in matches for var_name in var_tuple if var_name]

def remover_tildes(texto):
    # Normalize to NFD (Canonical Decomposition) which separates base characters from diacritics
    nfkd_form = unicodedata.normalize('NFKD', texto)
    # Remove all diacritics (characters with category 'Mn' are nonspacing marks)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def validar_variables(df, variables):
    columnas_df = df.columns.tolist()
    faltantes = []
    vacias = {}
    for var in variables: # var is now directly the variable name string
        if var not in columnas_df:
            faltantes.append(var)
        else:
            vacios_count = df[var].isna().sum() + (df[var] == "").sum()
            if vacios_count > 0:
                vacias[var] = vacios_count
    return faltantes, vacias

def construir_mensaje(row, plantilla, variables):
    msg = plantilla
    for var in variables: # var is now directly the variable name string
        valor = str(row[var]) if pd.notna(row[var]) and str(row[var]).strip() != "" else ""
        # Replace both {{var}} and [var] in the message
        msg = msg.replace(f"{{{{{var}}}}}", valor)
        msg = msg.replace(f"[{var}]", valor)
    msg = remover_tildes(msg) # Apply tilde removal here
    return msg.strip()

# ==================================================
# 📂 CARGA Y PREPARACIÓN DEL EXCEL
# ==================================================
def cargar_y_preparar_excel():
    print("\n📂 Sube tu archivo Excel...")
    uploaded = files.upload()

    if not uploaded:
        print("❌ No se recibió ningún archivo.")
        return None, None

    archivo = list(uploaded.keys())[0]
    print(f"📄 Archivo recibido: {archivo}")

    try:
        df = pd.read_excel(archivo, dtype=str)
    except Exception as e:
        print(f"❌ Error al leer el archivo: {e}")
        return None, None

    df.columns = [col.strip() for col in df.columns]
    print(f"✅ Archivo cargado: {len(df)} registros")
    print(f"📋 Columnas detectadas: {list(df.columns)}")

    col_tel = detectar_columna_telefono(df)
    if not col_tel:
        print("❌ No se pudo identificar la columna de teléfono.")
        return None, None

    df["Celular_limpio"] = df[col_tel].apply(limpiar_numero)
    df = df[df["Celular_limpio"].notna()].copy()
    print(f"📱 Números válidos: {len(df)}")

    # Duplicados
    antes = len(df)
    df = df.drop_duplicates(subset=["Celular_limpio"], keep="first")
    print(f"⚠️ Duplicados eliminados: {antes - len(df)}")
    print(f"📊 Registros únicos: {len(df)}")

    df = aplicar_formato_especial(df)

    if "Link" not in df.columns:
        df["Link"] = df["Celular_limpio"].apply(generar_link_personalizado)

    return df, col_tel
# ==================================================
# ✉️ PLANTILLA (SIN LÍMITE DE VARIABLES)
# ==================================================
def obtener_plantilla_con_modificacion(df):
    while True:
        print("\n✉️ Escribe la plantilla del SMS.")
        print("   Variables disponibles (usa el nombre exacto):")
        for col in df.columns:
            print(f"      {{{{ {col} }}}}")
        print()
        plantilla = input("👉 Plantilla: ").strip()
        variables = extraer_variables(plantilla)
        print(f"🧠 Variables detectadas ({len(variables)}): {variables}")
        faltantes, vacias = validar_variables(df, variables)
        if faltantes:
            print("\n❌ ERROR: Las siguientes variables NO existen en el Excel:")
            for v in faltantes:
                print(f"   - {v}")
            print("Corrige la plantilla y vuelve a intentarlo.\n")
            continue
        if vacias:
            print("\n⚠️ ADVERTENCIA: Variables con celdas vacías:")
            for k, v in vacias.items():
                print(f"   - {k}: {v} registros vacíos")
            print("   Los mensajes se enviarán sin ese valor en esos registros.\n")
        print("✅ Plantilla válida.\n")
        print("\n📨 VISTA PREVIA (primeros 3 registros):")
        print("-" * 60)
        for _, row in df.head(3).iterrows():
            num = row["Celular_limpio"]
            texto = construir_mensaje(row, plantilla, variables)
            print(f"📱 {num}:")
            print(f"   Mensaje (sin tildes): {texto}") # Explicitly indicating tildes are removed
            print(f"   Longitud: {len(texto)} caracteres")
            print("-" * 40)
        decision = input("\n¿Enviar? (SI para enviar / MODIFICAR para cambiar la plantilla): ").strip().upper()
        if decision == "SI":
            return plantilla, variables
        print("\n🔄 Volviendo a la pantalla de plantilla...\n")

# ==================================================
# 📤 ENVÍO DE LOTES (formato V2 con callbackData)
# ==================================================
def procesar_lote(cliente, lote_df, plantilla, variables, col_tel, num_lote, total_lotes):
    mensajes = []
    for _, row in lote_df.iterrows():
        texto = construir_mensaje(row, plantilla, variables)
        if "{{" in texto:
            print(f"⚠️ Variable sin reemplazar en fila {row.get('Celular_limpio', 'N/A')}: {texto[:80]}")
            continue
        if not row.get("Celular_limpio"):
            continue

        # callbackData con todos los datos de la fila
        datos_fila = {}
        for columna in lote_df.columns:
            valor = row[columna]
            if pd.notna(valor) and str(valor).strip() != "":
                datos_fila[columna] = str(valor).strip()
            else:
                datos_fila[columna] = ""

        callback_str = json.dumps(datos_fila, ensure_ascii=False)
        if len(callback_str) > 4000:
            callback_str = callback_str[:3997] + "..."

        mensajes.append({
            "from": SENDER_ID,
            "destinations": [{"to": str(row["Celular_limpio"])}],
            "text": texto,
            "callbackData": callback_str
        })

    if not mensajes:
        print(f"❌ Lote {num_lote}/{total_lotes} sin mensajes válidos.")
        return None, []

    print(f"\n📤 Enviando lote {num_lote}/{total_lotes} ({len(mensajes)} msjs)...")
    print("🔍 Primer mensaje del lote:")
    print("   " + mensajes[0]["text"][:200] + "...")

    respuesta = cliente.enviar_lote(mensajes)

    registros_detalle = []

    if respuesta:
        status_http = respuesta.get("_status_code", 200)
        bulk_id = respuesta.get("bulkId", "")
        print(f"✅ Lote {num_lote} enviado. BulkId: {bulk_id}")

        for msg in respuesta.get("messages", []):
            estado = msg.get("status", {}).get("name", "?")
            message_id = msg.get("messageId", "")
            to = msg.get("to", "")
            print(f"   📱 {to} | {estado} | ID: {message_id}")

            registros_detalle.append({
                "bulk_id": bulk_id,
                "message_id": message_id,
                "telefono": to,
                "url_corta": "",
                "hora_envio": datetime.utcnow().isoformat(),
                "respuesta_http": status_http,
                "estado": estado,
                "callback_data": ""
            })
    else:
        print(f"❌ Falló lote {num_lote} tras reintentos.")

    return respuesta, registros_detalle


# ==================================================
# 🚀 PROGRAMA PRINCIPAL
# ==================================================
def main():
    print("=" * 60)
    print("🚀 ENVÍO MASIVO SMS - INFOBIP V2 (callbackData)")
    print("=" * 60)

    if API_KEY == "TU_API_KEY_AQUÍ":
        print("❌ Configura tu API_KEY al inicio del script.")
        return None # Return None if API_KEY is not set

    df, col_tel = cargar_y_preparar_excel()
    if df is None or df.empty:
        print("❌ No hay datos válidos para enviar.")
        return None # Return None if no data

    plantilla, variables = obtener_plantilla_con_modificacion(df)
    print(f"\n⚠️ Se enviarán {len(df)} mensajes con la plantilla seleccionada.")
    cliente = InfobipSenderV2(API_KEY, BASE_URL)
    lotes = [df[i:i+BATCH_SIZE] for i in range(0, len(df), BATCH_SIZE)]
    print(f"📦 {len(lotes)} lotes (máx {BATCH_SIZE} c/u)")

    todos_registros = []
    hora_inicio = datetime.now()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                procesar_lote,
                cliente,
                lote,
                plantilla,
                variables,
                col_tel,
                i + 1,
                len(lotes)
            ): i
            for i, lote in enumerate(lotes)
        }

        for future in as_completed(futures):
            try:
                _, detalles = future.result()
                if detalles:
                    todos_registros.extend(detalles)
            except Exception as e:
                print(f"❌ Error crítico en hilo: {e}")

    # 💾 Guardar en BigQuery
    guardar_detalle_bigquery(todos_registros)

    duracion = (datetime.now() - hora_inicio).total_seconds()
    print("\n" + "=" * 60)
    print("✅ ENVÍO COMPLETADO")
    print(f"⏱️  Duración: {duracion:.1f} segundos")
    print(f"📦 Lotes procesados: {len(todos_registros)} mensajes")
    print("=" * 60)

    return todos_registros