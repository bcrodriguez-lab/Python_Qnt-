
import asyncio
import aiohttp
import json
import csv
import os
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

# ============ CONFIGURACIÓN ============
BASE_URL = "2knxjz.api.infobip.com"
API_KEY  = "04ca14df6c5a47e6713cdb13e8272ab3-aa42acbb-c69f-4a29-a3f5-3ec8b2fae16d"   # ← 替换为真实 API Key

SALIDA_LOGS_JSON = "logs_hoy.json"
SALIDA_CSV       = "logs_hoy.csv"
SALIDA_HTML      = "reporte_hoy.html"
HISTORICO_CSV    = "historico.csv"

CONCURRENCIA       = 20
TAMANO_VENTANA_MIN = 30

TZ_COL = timezone(timedelta(hours=-5))
FMT    = "%Y-%m-%dT%H:%M:%S.000+0000"

URL_LOGS = f"https://{BASE_URL}/sms/3/logs"
HEADERS  = {"Authorization": f"App {API_KEY}", "Accept": "application/json"}

OPERADORES = {
    "732101": "Claro", "732102": "Movistar", "732103": "Tigo/Une",
    "732111": "Tigo",  "732123": "Movistar", "732130": "Avantel",
    "732154": "Virgin","732360": "WOM",
}

ERROR_ACTIONS = {
    "EC_DEST_ADDRESS_BLACKLISTED":    "Revisar lista DNC, respetar decisión del usuario",
    "EC_TELESERVICE_NOT_PROVISIONED": "Verificar número, sacarlo de la lista",
    "EC_SM_DELIVERY_FAILURE":         "Reintentar (puede ser temporal)",
    "EC_SYSTEM_FAILURE":              "Reintentar (sistema)",
    "EC_UNEXPECTED_DATA_VALUE":       "Revisar formato del número",
    "EC_ABSENT_SUBSCRIBER":           "Número inactivo/apagado, reintentar después",
    "EC_ABSENT_SUBSCRIBER_SM":        "Subscriptor no disponible, reintentar después",
    "EC_UNKNOWN_SUBSCRIBER":          "Número no existe, eliminar de la lista",
    "EC_QUOTA_EXCEEDED":              "Cuota excedida, revisar plan",
    "EC_INVALID_DESTINATION_ADDRESS": "Destino inválido, verificar formato",
}


# ============================================================
# 1. DESCARGA DE LOGS (SIN CAMBIOS — ventanas paralelas)
# ============================================================
async def _traer_ventana(session, sem, idx, desde_utc, hasta_utc, total):
    async with sem:
        out, cursor, pagina = [], None, 1
        while True:
            params = {
                "sentSince": desde_utc.strftime(FMT),
                "sentUntil": hasta_utc.strftime(FMT),
                "limit": 1000,
                "useCursor": "true",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                async with session.get(URL_LOGS, headers=HEADERS,
                                        params=params, timeout=120) as r:
                    if r.status != 200:
                        print(f"[V{idx:02d}/{total} p{pagina}] HTTP {r.status}")
                        break
                    data = await r.json()
            except Exception as e:
                print(f"[V{idx:02d}/{total} p{pagina}] EXC: {e}")
                break
            lote = data.get("results", [])
            out.extend(lote)
            nc = (data.get("cursor") or {}).get("nextCursor")
            print(f"[V{idx:02d}/{total} p{pagina}] +{len(lote)}")
            if not nc or not lote:
                break
            cursor = nc
            pagina += 1
        return out


async def descargar_logs_hoy():
    ahora_col  = datetime.now(TZ_COL)
    inicio_col = ahora_col.replace(hour=0, minute=0, second=0, microsecond=0)
    ahora_utc  = ahora_col.astimezone(timezone.utc)
    inicio_utc = inicio_col.astimezone(timezone.utc)

    print(f"📅 Día Colombia: {inicio_col.strftime('%Y-%m-%d %H:%M')} → {ahora_col.strftime('%H:%M')}")
    print(f"📅 En UTC API  : {inicio_utc.strftime(FMT)} → {ahora_utc.strftime(FMT)}\n")

    ventanas, t = [], inicio_utc
    while t < ahora_utc:
        fin = min(t + timedelta(minutes=TAMANO_VENTANA_MIN), ahora_utc)
        ventanas.append((t, fin))
        t = fin

    print(f"🚀 {len(ventanas)} ventanas paralelas (conc={CONCURRENCIA})\n")
    sem       = asyncio.Semaphore(CONCURRENCIA)
    connector = aiohttp.TCPConnector(limit=CONCURRENCIA * 2, ttl_dns_cache=300)
    timeout   = aiohttp.ClientTimeout(total=180)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
        tareas = [_traer_ventana(s, sem, i + 1, d, h, len(ventanas))
                  for i, (d, h) in enumerate(ventanas)]
        lotes = await asyncio.gather(*tareas)

    vistos, todos = set(), []
    for lote in lotes:
        for log in lote:
            mid = log.get("messageId")
            if mid and mid in vistos:
                continue
            if mid:
                vistos.add(mid)
            todos.append(log)
    todos.sort(key=lambda x: x.get("sentAt", ""))

    with open(SALIDA_LOGS_JSON, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {len(todos)} logs guardados en {SALIDA_LOGS_JSON}")
    return todos


# ============================================================
# 2. EXTRAER FILAS (con conversión UTC → Colombia)
# ============================================================
def extraer_filas(logs):
    filas = []
    for log in logs:
        st = log.get("status",  {}) or {}
        er = log.get("error",   {}) or {}
        pr = log.get("price",   {}) or {}
        ct = log.get("content", {}) or {}

        sent_col = done_col = None
        try:
            sent_col = datetime.strptime(
                log.get("sentAt", "")[:19], "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc).astimezone(TZ_COL)
        except Exception:
            pass
        try:
            done_col = datetime.strptime(
                log.get("doneAt", "")[:19], "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc).astimezone(TZ_COL)
        except Exception:
            pass

        delay = (done_col - sent_col).total_seconds() if (sent_col and done_col) else None
        mcc   = log.get("mccMnc", "")

        filas.append({
            "messageId":     log.get("messageId", ""),
            "bulkId":        log.get("bulkId", ""),
            "sender":        log.get("sender", ""),
            "destination":   log.get("destination", ""),
            "sentAt_utc":    log.get("sentAt", ""),
            "sentAt_col":    sent_col.strftime("%Y-%m-%d %H:%M:%S") if sent_col else "",
            "doneAt_col":    done_col.strftime("%Y-%m-%d %H:%M:%S") if done_col else "",
            "delay_sec":     round(delay, 2) if delay is not None else None,
            "mccMnc":        mcc,
            "operador":      OPERADORES.get(mcc, mcc),
            "estado":        st.get("groupName", ""),
            "nombre_estado": st.get("name", ""),
            "error":         er.get("name", ""),
            "desc_error":    er.get("description", ""),
            "accion":        ERROR_ACTIONS.get(er.get("name", ""), "—"),
            "precio":        pr.get("pricePerMessage", 0),
            "texto":         (ct.get("text") or "").replace("\n", " "),
            "hora_col":      sent_col.hour if sent_col else None,
        })
    return filas


def exportar_csv(filas, path=SALIDA_CSV):
    if not filas:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    print(f"📄 CSV: {path}")


# ============================================================
# 3. REPORTE EN CONSOLA (igual que antes)
# ============================================================
def reporte(filas):
    total = len(filas)
    if total == 0:
        print("Sin datos.")
        return

    costo_total = sum(float(f["precio"] or 0) for f in filas)
    delivered   = sum(1 for f in filas if f["estado"] == "DELIVERED")
    fallidos    = total - delivered

    print(f"\n{'='*72}")
    print(f"REPORTE DEL DÍA (HORA COLOMBIA) — {datetime.now(TZ_COL).strftime('%Y-%m-%d')}")
    print(f"{'='*72}")
    print(f"Total mensajes      : {total:,}")
    print(f"Destinos únicos     : {len(set(f['destination'] for f in filas)):,}")
    print(f"Costo total         : {costo_total:,.0f} COP")
    print(f"Tasa de entrega     : {delivered/total*100:.2f}%")

    print(f"\n── ⏱ TIEMPO DE ENTREGA POR OPERADOR ──")
    por_op = defaultdict(list)
    for f in filas:
        if f["delay_sec"] is not None and f["estado"] == "DELIVERED":
            por_op[f["operador"]].append(f["delay_sec"])

    print(f"  {'Operador':<15} {'Muestras':>10} {'P50 (s)':>10} {'P95 (s)':>10} {'>5min':>8}")
    print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")
    for op, ds in sorted(por_op.items(), key=lambda x: -len(x[1])):
        if not ds:
            continue
        ds_sorted = sorted(ds)
        p50 = ds_sorted[len(ds_sorted)//2]
        p95 = ds_sorted[min(int(len(ds_sorted)*0.95), len(ds_sorted)-1)]
        anomalies = sum(1 for d in ds if d > 300)
        print(f"  {op:<15} {len(ds):>10,} {p50:>10.2f} {p95:>10.2f} {anomalies:>8,}")

    # Tasa <5s
    entregados_rapidos = sum(1 for f in filas
                             if f["delay_sec"] is not None
                             and f["estado"] == "DELIVERED"
                             and f["delay_sec"] < 5)
    tasa_5s = (entregados_rapidos / delivered * 100) if delivered else 0
    print(f"\n── ⚡ TASA DE ENTREGA <5 SEGUNDOS ──")
    print(f"  Entregados <5s      : {entregados_rapidos:,}")
    print(f"  Tasa entrega <5s    : {tasa_5s:.2f}%")

    # Costo-eficiencia por operador
    print(f"\n── 💰 COSTO-EFICIENCIA POR OPERADOR ──")
    op_stats = defaultdict(lambda: {"total": 0, "delivered": 0, "costo": 0.0})
    for f in filas:
        s = op_stats[f["operador"]]
        s["total"] += 1
        if f["estado"] == "DELIVERED":
            s["delivered"] += 1
        s["costo"] += float(f["precio"] or 0)

    print(f"  {'Operador':<12} {'Enviados':>10} {'Entrega':>9} {'Fallo':>9} {'Costo':>14} {'$/Entrega':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*9} {'-'*9} {'-'*14} {'-'*12}")
    for op, s in sorted(op_stats.items(), key=lambda x: -x[1]["total"]):
        te = s["delivered"] / s["total"] * 100 if s["total"] else 0
        tf = 100 - te
        ce = s["costo"] / s["delivered"] if s["delivered"] else 0
        print(f"  {op:<12} {s['total']:>10,} {te:>8.2f}% {tf:>8.2f}% {s['costo']:>13,.0f} {ce:>12.2f}")

    # Errores con acción
    print(f"\n── ❌ ERRORES Y ACCIONES SUGERIDAS ──")
    errores = Counter(f["error"] for f in filas if f["error"] != "NO_ERROR")
    if not errores:
        print("  Sin errores ✅")
    else:
        for err, cnt in errores.most_common(15):
            print(f"  {err:<40} {cnt:>6,}  →  {ERROR_ACTIONS.get(err, '—')}")

    # Distribución por hora
    print(f"\n── 🕐 DISTRIBUCIÓN POR HORA (COLOMBIA) ──")
    horas = Counter(f["hora_col"] for f in filas if f["hora_col"] is not None)
    for h in sorted(horas.keys()):
        v = horas[h]
        barra = "█" * int(v / total * 50)
        print(f"  {h:02d}:00  {v:>7,}  {barra}")

    # Comparación histórica
    hoy = datetime.now(TZ_COL).strftime("%Y-%m-%d")
    fila_actual = {
        "fecha": hoy, "total": total, "delivered": delivered,
        "tasa_entrega": round(delivered/total*100, 2),
        "tasa_fallo":   round(fallidos/total*100, 2),
        "costo":        round(costo_total, 0),
        "costo_unitario": round(costo_total/delivered, 2) if delivered else 0,
    }

    historico = []
    if os.path.exists(HISTORICO_CSV):
        with open(HISTORICO_CSV, "r", encoding="utf-8-sig") as f:
            historico = list(csv.DictReader(f, delimiter=";"))
    if not any(h["fecha"] == hoy for h in historico):
        historico.append(fila_actual)
    with open(HISTORICO_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(fila_actual.keys()), delimiter=";")
        w.writeheader()
        w.writerows(historico)

    if len(historico) >= 2:
        print(f"\n── 📊 COMPARACIÓN CON DÍA ANTERIOR ──")
        ayer = historico[-2]
        print(f"  {'Indicador':<20} {ayer['fecha']:>12} {hoy:>12} {'Cambio':>12}")
        print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*12}")
        for key, label in [("total", "Enviados"), ("tasa_entrega", "Tasa entrega"),
                           ("tasa_fallo", "Tasa fallo"), ("costo", "Costo total"),
                           ("costo_unitario", "Costo unitario")]:
            va = float(ayer[key]); vh = float(fila_actual[key])
            diff = vh - va
            signo = "+" if diff > 0 else ""
            print(f"  {label:<20} {va:>12,.2f} {vh:>12,.2f} {signo}{diff:>11,.2f}")

    print(f"\n{'='*72}\n")


# ============================================================
# 4. GENERAR HTML CON DASHBOARD FILTRABLE
# ============================================================
def generar_html(filas, path=SALIDA_HTML):
    """
    HTML con:
    - Tarjetas de totales
    - Gráficas SVG de resumen
    - Tabla de bulkIds (clickeable para filtrar)
    - Tabla detallada con filtros interactivos
    - Exportación de la vista filtrada a CSV
    """
    total = len(filas)
    if total == 0:
        return

    # --- Preparar datos para el dashboard ---
    # Convertimos filas a JSON compacto para embeber en la página.
    # Acortamos el texto para que no explote el tamaño del HTML.
    datos_json = []
    for f in filas:
        datos_json.append({
            "messageId": f["messageId"],
            "bulkId":    f["bulkId"],
            "sender":    f["sender"],
            "destination": f["destination"],
            "sentAt_col":  f["sentAt_col"],
            "doneAt_col":  f["doneAt_col"],
            "delay_sec":   f["delay_sec"],
            "mccMnc":      f["mccMnc"],
            "operador":    f["operador"],
            "estado":      f["estado"],
            "nombre_estado": f["nombre_estado"],
            "error":       f["error"],
            "desc_error":  f["desc_error"],
            "accion":      f["accion"],
            "precio":      f["precio"],
            "texto":       f["texto"][:200],   # recorte para reducir tamaño
            "hora_col":    f["hora_col"],
        })
    datos_json_str = json.dumps(datos_json, ensure_ascii=False, separators=(",", ":"))

    # Resumen por bulkId para la tabla superior
    por_bulk = defaultdict(lambda: {
        "total": 0, "delivered": 0, "rejected": 0, "undeliverable": 0,
        "expired": 0, "costo": 0.0, "primera_hora": "", "operadores": Counter()
    })
    for f in filas:
        b = por_bulk[f["bulkId"]]
        b["total"] += 1
        est = f["estado"]
        if est == "DELIVERED":
            b["delivered"] += 1
        elif est == "REJECTED":
            b["rejected"] += 1
        elif est == "UNDELIVERABLE":
            b["undeliverable"] += 1
        elif est == "EXPIRED":
            b["expired"] += 1
        b["costo"] += float(f["precio"] or 0)
        if not b["primera_hora"] and f["sentAt_col"]:
            b["primera_hora"] = f["sentAt_col"]
        b["operadores"][f["operador"]] += 1

    bulk_rows = []
    for bid, s in sorted(por_bulk.items(), key=lambda x: -x[1]["total"]):
        tasa_ent = s["delivered"] / s["total"] * 100 if s["total"] else 0
        op_principal = s["operadores"].most_common(1)[0][0] if s["operadores"] else "—"
        bulk_rows.append(
            f'<tr data-bulkid="{bid}">'
            f'<td class="mono">{bid}</td>'
            f'<td class="num">{s["total"]:,}</td>'
            f'<td class="num ok">{s["delivered"]:,}</td>'
            f'<td class="num bad">{s["rejected"]:,}</td>'
            f'<td class="num warn">{s["undeliverable"] + s["expired"]:,}</td>'
            f'<td class="num">{tasa_ent:.2f}%</td>'
            f'<td class="num">${s["costo"]:,.0f}</td>'
            f'<td>{op_principal}</td>'
            f'<td><button class="btn-filtrar" data-bulkid="{bid}">Filtrar</button></td>'
            f'</tr>'
        )
    bulk_rows_str = "\n".join(bulk_rows)

    # Resumen operadores
    ops = Counter(f["operador"] for f in filas)
    errores = Counter(f["error"] for f in filas if f["error"] != "NO_ERROR")
    estados = Counter(f["estado"] for f in filas)
    delivered = estados.get("DELIVERED", 0)
    tasa_ent = delivered / total * 100 if total else 0
    costo_total = sum(float(f["precio"] or 0) for f in filas)

    # Listas para los <select> de filtros
    bulk_options = "".join(f'<option value="{b}">{b}</option>'
                           for b in sorted(por_bulk.keys()))
    ops_options   = "".join(f'<option value="{o}">{o} ({c:,})</option>'
                            for o, c in ops.most_common())
    err_options   = "".join(f'<option value="{e}">{e} ({c:,})</option>'
                            for e, c in errores.most_common())

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Dashboard SMS QNT — {datetime.now(TZ_COL).strftime('%Y-%m-%d')}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system,'Segoe UI',sans-serif; margin:0; padding:20px;
          background:#f5f6fa; color:#2c3e50; font-size:14px; }}
  h1 {{ margin-top:0; font-size:22px; }}
  h2 {{ font-size:16px; border-bottom:2px solid #3498db; padding-bottom:6px;
        margin-top:30px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
            gap:12px; margin:16px 0; }}
  .card {{ background:white; padding:14px; border-radius:8px;
           box-shadow:0 2px 6px rgba(0,0,0,0.06); }}
  .card .num {{ font-size:20px; font-weight:bold; color:#3498db; }}
  .card .lbl {{ font-size:11px; color:#7f8c8d; margin-top:3px; text-transform:uppercase; }}
  .ok {{ color:#27ae60; }} .bad {{ color:#e74c3c; }} .warn {{ color:#f39c12; }}
  .section {{ background:white; padding:18px; border-radius:8px;
              box-shadow:0 2px 6px rgba(0,0,0,0.06); margin-bottom:18px; }}
  /* Filtros */
  .filtros {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
              gap:10px; margin:14px 0; }}
  .filtros label {{ display:block; font-size:11px; color:#7f8c8d;
                    text-transform:uppercase; margin-bottom:3px; }}
  .filtros select, .filtros input {{ width:100%; padding:6px 8px; border:1px solid #ddd;
                                     border-radius:5px; font-size:13px; }}
  .filtros input[type=text] {{ width:100%; }}
  .acciones {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}
  button {{ padding:8px 14px; border:none; border-radius:5px; cursor:pointer;
            font-size:13px; font-weight:500; }}
  .btn-primary {{ background:#3498db; color:white; }}
  .btn-primary:hover {{ background:#2980b9; }}
  .btn-secondary {{ background:#ecf0f1; color:#2c3e50; }}
  .btn-secondary:hover {{ background:#d5dbdb; }}
  .btn-exportar {{ background:#27ae60; color:white; }}
  .btn-exportar:hover {{ background:#1e8449; }}
  .btn-filtrar {{ background:#3498db; color:white; padding:4px 10px;
                  font-size:11px; }}
  /* Tablas */
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th {{ background:#34495e; color:white; padding:8px 6px; text-align:left;
        font-size:11px; text-transform:uppercase; position:sticky; top:0; }}
  td {{ padding:6px; border-bottom:1px solid #ecf0f1; }}
  tbody tr:hover {{ background:#f8f9fa; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .mono {{ font-family: 'Consolas', monospace; font-size:11px; }}
  .tabla-scroll {{ max-height:600px; overflow-y:auto; border:1px solid #ecf0f1;
                   border-radius:6px; }}
  .contador {{ font-size:12px; color:#7f8c8d; margin:8px 0; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:10px;
            font-size:10px; font-weight:600; text-transform:uppercase; }}
  .b-delivered {{ background:#d5f4e6; color:#27ae60; }}
  .b-rejected {{ background:#fadbd8; color:#e74c3c; }}
  .b-undeliverable {{ background:#fdebd0; color:#f39c12; }}
  .b-expired {{ background:#eaecee; color:#7f8c8d; }}
</style>
</head>
<body>

<h1>📊 Dashboard SMS QNT — {datetime.now(TZ_COL).strftime('%Y-%m-%d')}</h1>
<p style="color:#7f8c8d; font-size:12px;">Generado: {datetime.now(TZ_COL).strftime('%H:%M:%S')} (Colombia UTC-5) — {total:,} mensajes</p>

<!-- Tarjetas de resumen -->
<div class="cards">
  <div class="card"><div class="num">{total:,}</div><div class="lbl">Total mensajes</div></div>
  <div class="card"><div class="num ok">{delivered:,}</div><div class="lbl">Entregados ({tasa_ent:.2f}%)</div></div>
  <div class="card"><div class="num bad">{estados.get('REJECTED',0):,}</div><div class="lbl">Rechazados</div></div>
  <div class="card"><div class="num warn">{estados.get('UNDELIVERABLE',0)+estados.get('EXPIRED',0):,}</div><div class="lbl">No entregados</div></div>
  <div class="card"><div class="num">${costo_total:,.0f}</div><div class="lbl">Costo total (COP)</div></div>
  <div class="card"><div class="num">{len(por_bulk)}</div><div class="lbl">BulkIds únicos</div></div>
</div>

<!-- Tabla de bulkIds -->
<div class="section">
  <h2>📦 BulkIds del día</h2>
  <p style="color:#7f8c8d; font-size:12px;">
    Haz clic en "Filtrar" para ver solo los mensajes de ese bulkId en la tabla de abajo.
  </p>
  <div class="tabla-scroll" style="max-height:400px;">
    <table>
      <thead>
        <tr>
          <th>BulkId</th><th class="num">Total</th><th class="num">Entreg.</th>
          <th class="num">Rech.</th><th class="num">No entreg.</th>
          <th class="num">Tasa ent.</th><th class="num">Costo</th>
          <th>Operador princ.</th><th></th>
        </tr>
      </thead>
      <tbody>
        {bulk_rows_str}
      </tbody>
    </table>
  </div>
</div>

<!-- Tabla detallada con filtros -->
<div class="section">
  <h2>🔍 Detalle de mensajes</h2>

  <!-- Filtros interactivos -->
  <div class="filtros">
    <div>
      <label>BulkId</label>
      <select id="f-bulkid">
        <option value="">Todos</option>
        {bulk_options}
      </select>
    </div>
    <div>
      <label>Estado</label>
      <select id="f-estado">
        <option value="">Todos</option>
        <option value="DELIVERED">DELIVERED</option>
        <option value="REJECTED">REJECTED</option>
        <option value="UNDELIVERABLE">UNDELIVERABLE</option>
        <option value="EXPIRED">EXPIRED</option>
      </select>
    </div>
    <div>
      <label>Operador</label>
      <select id="f-operador">
        <option value="">Todos</option>
        {ops_options}
      </select>
    </div>
    <div>
      <label>Error</label>
      <select id="f-error">
        <option value="">Todos</option>
        {err_options}
      </select>
    </div>
    <div>
      <label>Desde (hora)</label>
      <input type="text" id="f-desde" placeholder="ej: 08:00">
    </div>
    <div>
      <label>Hasta (hora)</label>
      <input type="text" id="f-hasta" placeholder="ej: 14:30">
    </div>
    <div>
      <label>Búsqueda en destino / texto</label>
      <input type="text" id="f-buscar" placeholder="573001234 o QNT">
    </div>
  </div>

  <div class="acciones">
    <button class="btn-primary" onclick="aplicarFiltros()">Aplicar filtros</button>
    <button class="btn-secondary" onclick="limpiarFiltros()">Limpiar</button>
    <button class="btn-exportar" onclick="exportarCSV()">⬇ Exportar vista a CSV</button>
  </div>

  <div class="contador" id="contador">Cargando…</div>

  <div class="tabla-scroll">
    <table id="tabla-detalle">
      <thead>
        <tr>
          <th>Hora COL</th>
          <th>BulkId</th>
          <th>Destino</th>
          <th>Operador</th>
          <th>Estado</th>
          <th>Error</th>
          <th>Acción sugerida</th>
          <th class="num">Precio</th>
          <th>Texto</th>
        </tr>
      </thead>
      <tbody id="tbody-detalle"></tbody>
    </table>
  </div>
</div>

<script>
// ============================================================
// DATOS EMBEBIDOS (todas las filas del día)
// ============================================================
const DATOS = {datos_json_str};

// ============================================================
// RENDERIZADO + FILTROS
// ============================================================
let datosFiltrados = DATOS.slice();

function escapeHtml(s) {{
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({{
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }}[c]));
}}

function claseBadge(estado) {{
  const map = {{
    "DELIVERED":"b-delivered", "REJECTED":"b-rejected",
    "UNDELIVERABLE":"b-undeliverable", "EXPIRED":"b-expired"
  }};
  return map[estado] || "b-expired";
}}

function renderTabla() {{
  const tbody = document.getElementById("tbody-detalle");
  const contador = document.getElementById("contador");
  const filas = datosFiltrados;

  contador.textContent = `Mostrando ${{filas.length.toLocaleString()}} de ${{DATOS.length.toLocaleString()}} mensajes`;

  // Limitar render a 5000 para no colgar el navegador
  const limite = Math.min(filas.length, 5000);
  const partes = [];
  for (let i = 0; i < limite; i++) {{
    const f = filas[i];
    partes.push(
      '<tr>' +
      '<td class="mono">' + escapeHtml(f.sentAt_col) + '</td>' +
      '<td class="mono">' + escapeHtml(f.bulkId) + '</td>' +
      '<td class="mono">' + escapeHtml(f.destination) + '</td>' +
      '<td>' + escapeHtml(f.operador) + '</td>' +
      '<td><span class="badge ' + claseBadge(f.estado) + '">' + escapeHtml(f.estado) + '</span></td>' +
      '<td class="mono" style="font-size:11px">' + escapeHtml(f.error) + '</td>' +
      '<td style="font-size:11px">' + escapeHtml(f.accion) + '</td>' +
      '<td class="num">' + (f.precio || 0) + '</td>' +
      '<td style="max-width:400px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap" title="' + escapeHtml(f.texto) + '">' + escapeHtml(f.texto) + '</td>' +
      '</tr>'
    );
  }}
  tbody.innerHTML = partes.join("");

  if (filas.length > limite) {{
    contador.textContent += ` — mostrando solo los primeros ${{limite.toLocaleString()}} (usa los filtros para acotar)`;
  }}
}}

function aplicarFiltros() {{
  const bulkid    = document.getElementById("f-bulkid").value;
  const estado    = document.getElementById("f-estado").value;
  const operador  = document.getElementById("f-operador").value;
  const error     = document.getElementById("f-error").value;
  const desde     = document.getElementById("f-desde").value.trim();
  const hasta     = document.getElementById("f-hasta").value.trim();
  const buscar    = document.getElementById("f-buscar").value.trim().toLowerCase();

  datosFiltrados = DATOS.filter(f => {{
    if (bulkid && f.bulkId !== bulkid) return false;
    if (estado && f.estado !== estado) return false;
    if (operador && f.operador !== operador) return false;
    if (error && f.error !== error) return false;

    // Hora en formato HH:MM:SS → comparar solo HH:MM
    if (desde || hasta) {{
      const hora = (f.sentAt_col || "").slice(11, 16); // "HH:MM"
      if (desde && hora < desde) return false;
      if (hasta && hora > hasta) return false;
    }}

    if (buscar) {{
      const hay = (f.destination + " " + f.texto + " " + f.bulkId).toLowerCase();
      if (!hay.includes(buscar)) return false;
    }}

    return true;
  }});

  renderTabla();
}}

function limpiarFiltros() {{
  ["f-bulkid","f-estado","f-operador","f-error","f-desde","f-hasta","f-buscar"]
    .forEach(id => {{ document.getElementById(id).value = ""; }});
  datosFiltrados = DATOS.slice();
  renderTabla();
}}

// Click en botón "Filtrar" de la tabla de bulkIds
document.querySelectorAll(".btn-filtrar").forEach(btn => {{
  btn.addEventListener("click", () => {{
    const bid = btn.getAttribute("data-bulkid");
    document.getElementById("f-bulkid").value = bid;
    aplicarFiltros();
    document.getElementById("tabla-detalle").scrollIntoView({{ behavior: "smooth" }});
  }});
}});

// Exportar la vista filtrada a CSV
function exportarCSV() {{
  const cols = ["sentAt_col","bulkId","destination","operador","estado",
                "error","accion","precio","texto"];
  const cabecera = cols.join(";");
  const lineas = datosFiltrados.map(f =>
    cols.map(c => {{
      let v = f[c] === null || f[c] === undefined ? "" : String(f[c]);
      v = v.replace(/"/g, '""').replace(/\\n/g, " ");
      return '"' + v + '"';
    }}).join(";")
  );
  const csv = "\\ufeff" + cabecera + "\\n" + lineas.join("\\n");
  const blob = new Blob([csv], {{ type: "text/csv;charset=utf-8;" }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "vista_filtrada_" + new Date().toISOString().slice(0,10) + ".csv";
  a.click();
  URL.revokeObjectURL(url);
}}

// Render inicial
renderTabla();
</script>

</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🌐 HTML: {path}")


# ============================================================
# FLUJO PRINCIPAL
# ============================================================
if __name__ == "__main__":
    t0 = datetime.now(TZ_COL)

    # 1. Descarga (sin cambios, paralela, rápida)
    logs = asyncio.run(descargar_logs_hoy())
    if not logs:
        print("Sin datos.")
        raise SystemExit

    # 2. Procesar
    filas = extraer_filas(logs)

    # 3. CSV general (igual que antes)
    exportar_csv(filas)

    # 4. Reporte en terminal (igual que antes)
    reporte(filas)

    # 5. HTML con dashboard filtrable
    generar_html(filas)

    print(f"✅ Listo. Abrí '{SALIDA_HTML}' con doble clic.")
    print(f"⏱ Total: {(datetime.now(TZ_COL)-t0).total_seconds():.1f}s")