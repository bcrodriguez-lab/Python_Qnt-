# Informe: Endpoint de reportes `http://localhost:5000/reportes`

## 1) Resumen ejecutivo
Nuestro sistema permite generar y descargar reportes en formato Excel (XLSX) basado en la información retornada por Wolkvox. La UI se sirve desde `GET /reportes`, mientras que la generación/descarga real del XLSX se realiza mediante el endpoint de descarga `POST /reports/download` (parámetros: `server`, `date_ini`, `date_end`).

Adicionalmente, para el caso específico de campañas automáticas, la descarga automática de un XLSX al **eliminar** una campaña se implementa con `POST /auto-campaigns/<campaign_id>/delete-report`, asegurando que el Excel incluya el **servidor**, el **nombre de la campaña** y la **fecha/hora de finalización**.

---

## 2) Endpoint UI: `GET /reportes`
- **Ruta**: `http://localhost:5000/reportes`
- **Método**: `GET`
- **Responsabilidad**: renderizar la vista HTML con el selector de servidores y valores por defecto de rango de fechas.

En el backend:
- Carga `servidor` desde `config.json`.
- Carga lista de servidores desde `load_servers()`.
- Calcula defaults para `start` y `end` con fecha UTC actual.

Resultado:
- Se presenta al usuario una pantalla para solicitar la descarga del Excel.

---

## 3) Endpoint de descarga XLSX: `POST /reports/download`
- **Ruta**: `http://localhost:5000/reports/download`
- **Método**: `POST`
- **Content-Type esperado**: admite `request.form` o `request.get_json()`.

### 3.1 Parámetros requeridos
1. **`server`** (obligatorio)
2. **`date_ini`** (obligatorio)
3. **`date_end`** (obligatorio)

### 3.2 Flujo interno
1. **Validación**
   - Rechaza con `400` si falta alguno de los campos requeridos.
2. **Conversión de fechas**
   - Convierte `date_ini` y `date_end` hacia el formato esperado por Wolkvox (`YYYYMMDDHHMMSS`).
3. **Construcción de URL Wolkvox**
   - Intenta obtener el registro del servidor con `get_server(server)`.
   - Construye la URL hacia `/api/v2/reports_manager.php?api=cdr_1&date_ini=...&date_end=...`.
   - Si no existe en BD, arma la URL usando el patrón `https://wv{server}.wolkvox.com`.
4. **Autenticación**
   - Obtiene headers con token (usando `get_authorization_headers(server)`).
5. **Consulta a Wolkvox**
   - Ejecuta `requests.get(url, headers=headers, timeout=60)`.
6. **Normalización de respuesta**
   - Si la respuesta es lista → usa como `rows`.
   - Si es dict → intenta extraer `data` o `files`.
   - Si no → envuelve en una lista con `{raw: ...}`.
7. **Generación XLSX con formato**
   - Llama a `build_wolkvox_excel(rows=rows, filename=filename)`.
8. **Descarga**
   - Devuelve el XLSX como `send_file(..., as_attachment=True, download_name=...)`.

### 3.3 Formato del Excel
El builder `excel_report_builder.py` usa OpenPyXL para garantizar un reporte claro:
- Header estilizado (relleno + tipografía)
- Freeze panes (para mantener encabezados)
- Auto-filter en el header
- Ajuste de anchos por columna
- Alineaciones (centro en header; izquierda en datos)

**Importante**: el Excel descargado sale **ya ajustado y formateado correctamente**, listo para lectura y análisis.

---

## 4) Flujo de eliminación de campañas: descarga automática con nombre de servidor + campaña

### 4.1 Endpoint para campaña automática (descarga al eliminar)
- **Ruta**: `POST /auto-campaigns/<campaign_id>/delete-report`

### 4.2 Flujo garantizado
1. Verifica que la campaña exista.
2. Evita borrar si está en ejecución (`campaign.running`).
3. Obtiene:
   - `server_name` de la campaña
   - `campaign_name` (nombre de campaña)
4. Obtiene la **fecha/hora de finalización** desde el último log:
   - `AutoCampaignExecutionLog.end_time`
   - fallback a `campaign.last_run`
   - fallback final a `datetime.utcnow()`
5. Define ventana del reporte:
   - `date_ini` a las 00:00:00
   - `date_end` a las 23:59:59
6. Consulta Wolkvox y genera el Excel con `build_wolkvox_excel()`.
7. Crea un nombre de archivo con:
   - servidor
   - campaña
   - hora de finalización
8. **Descarga el archivo primero** y luego elimina la campaña (`delete_auto_campaign`).

### 4.3 Nombre del archivo
El archivo se descarga con un patrón:
- `reporte_<safe_server>_<safe_campaign>_finalizado_<YYYYMMDD_HHMMSS>.xlsx`

---

## 5) Qué debe asegurar el sistema en producción
- La UI/botón de eliminación debe llamar a `.../delete-report` (no solo `DELETE`).
- Mantener los logs `AutoCampaignExecutionLog` con `end_time` para que el Excel refleje correctamente el momento de finalización.
- Verificar que el builder (`build_wolkvox_excel`) se use siempre para conservar el formateo consistente.

