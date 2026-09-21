import asyncio
import aiohttp
import json
import csv
import os
import math
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

# ============ 配置 ============
BASE_URL = "2knxjz.api.infobip.com"
API_KEY  = "04ca14df6c5a47e6713cdb13e8272ab3-aa42acbb-c69f-4a29-a3f5-3ec8b2fae16d"   # ← 替换为真实 API Key

# 时区：哥伦比亚 UTC-5
TZ_COL = timezone(timedelta(hours=-5))
FMT = "%Y-%m-%dT%H:%M:%S.000+0000"
URL_LOGS   = f"https://{BASE_URL}/sms/3/logs"
URL_SEND   = f"https://{BASE_URL}/sms/3/messages"
HEADERS    = {"Authorization": f"App {API_KEY}", "Accept": "application/json"}
HEADERS_JSON = {**HEADERS, "Content-Type": "application/json"}

# 运营商映射
OPERADORES = {
    "732101": "Claro", "732102": "Movistar", "732103": "Tigo/Une",
    "732111": "Tigo", "732123": "Movistar", "732130": "Avantel",
    "732154": "Virgin", "732360": "WOM",
}

# 错误码建议动作
ERROR_ACTIONS = {
    "EC_DEST_ADDRESS_BLACKLISTED": "检查 Do Not Contact 列表，确认用户是否要求不接收",
    "EC_TELESERVICE_NOT_PROVISIONED": "检查号码有效性，建议从列表中移除",
    "EC_SM_DELIVERY_FAILURE": "可尝试重试",
    "EC_SYSTEM_FAILURE": "临时性问题，可重试",
    "EC_UNEXPECTED_DATA_VALUE": "检查号码格式",
}


# ============ 1. 发送带点击追踪的 SMS ============
async def send_sms_with_tracking(session, to_number: str, text: str,
                                  sender: str = "QNT",
                                  tracking_url: str = None):
    """
    发送单条 SMS
    开启 URL 缩短和点击追踪。
    tracking_url: 你的 webhook 地址，用于接收 CLICKED 事件。
    """
    payload = {
        "messages": [{
            "destinations": [{"to": to_number}],
            "sender": sender,
            "content": {"text": text},
        }],
        "urlOptions": {
            "shortenUrl": True,
            "trackClicks": True,
            "removeProtocol": False,
        },
    }
    if tracking_url:
        payload["urlOptions"]["trackingUrl"] = tracking_url

    async with session.post(URL_SEND, headers=HEADERS_JSON, json=payload) as r:
        data = await r.json()
        print(f"📤 Envío a {to_number}: HTTP {r.status}")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data


# ============ 2. 下载今日短信日志（并行窗口） ============
async def traer_ventana(session, sem, idx, desde_utc, hasta_utc, total):
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
                async with session.get(URL_LOGS, headers=HEADERS, params=params, timeout=120) as r:
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


async def descargar_hoy_logs():
    ahora_col = datetime.now(TZ_COL)
    inicio_col = ahora_col.replace(hour=0, minute=0, second=0, microsecond=0)
    ahora_utc = ahora_col.astimezone(timezone.utc)
    inicio_utc = inicio_col.astimezone(timezone.utc)

    print(f"📅 Día Colombia: {inicio_col.strftime('%Y-%m-%d %H:%M')} → {ahora_col.strftime('%H:%M')}")
    print(f"📅 En UTC (API): {inicio_utc.strftime(FMT)} → {ahora_utc.strftime(FMT)}\n")

    ventanas, t = [], inicio_utc
    while t < ahora_utc:
        fin = min(t + timedelta(minutes=30), ahora_utc)
        ventanas.append((t, fin))
        t = fin

    print(f"🚀 {len(ventanas)} ventanas paralelas\n")
    sem = asyncio.Semaphore(20)
    connector = aiohttp.TCPConnector(limit=40, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=180)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
        tareas = [traer_ventana(s, sem, i + 1, d, h, len(ventanas))
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

    with open("logs_hoy.json", "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {len(todos)} logs guardados en logs_hoy.json")
    return todos


# ============ 3. 数据处理（含时区转换） ============
def extraer_filas(logs):
    filas = []
    for log in logs:
        st = log.get("status", {}) or {}
        er = log.get("error", {}) or {}
        pr = log.get("price", {}) or {}
        ct = log.get("content", {}) or {}

        # sentAt UTC → Colombia
        sent_col, done_col = None, None
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

        # 延迟秒数
        delay = None
        if sent_col and done_col:
            delay = (done_col - sent_col).total_seconds()

        mcc = log.get("mccMnc", "")
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
            "precio":        pr.get("pricePerMessage", 0),
            "texto":         (ct.get("text") or "").replace("\n", " "),
            "hora_col":      sent_col.hour if sent_col else None,
        })
    return filas


# ============ 4. 导出 CSV ============
def exportar_csv(filas, path="logs_hoy.csv"):
    if not filas:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    print(f"📄 CSV: {path}")


# ============ 5. 控制台报告（含所有新增模块） ============
def reporte(filas):
    total = len(filas)
    if total == 0:
        print("Sin datos.")
        return

    costo_total = sum(float(f["precio"] or 0) for f in filas)
    delivered = sum(1 for f in filas if f["estado"] == "DELIVERED")
    fallidos = total - delivered

    print(f"\n{'='*70}")
    print(f"REPORTE DEL DÍA (HORA COLOMBIA) — {datetime.now(TZ_COL).strftime('%Y-%m-%d')}")
    print(f"{'='*70}")
    print(f"Total mensajes      : {total:,}")
    print(f"Destinos únicos     : {len(set(f['destination'] for f in filas)):,}")
    print(f"Costo total         : {costo_total:,.0f} COP")
    print(f"Tasa de entrega     : {delivered/total*100:.2f}%")

    # ---- 1. 送达时效分析 ----
    print(f"\n── ⏱ TIEMPO DE ENTREGA (por operador) ──")
    por_op_delay = defaultdict(list)
    for f in filas:
        if f["delay_sec"] is not None and f["estado"] == "DELIVERED":
            por_op_delay[f["operador"]].append(f["delay_sec"])

    print(f"  {'Operador':<15} {'P50 (s)':>10} {'P95 (s)':>10} {'Anomalías >5min':>16}")
    print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*16}")
    for op, delays in sorted(por_op_delay.items(), key=lambda x: -len(x[1])):
        if not delays:
            continue
        delays_sorted = sorted(delays)
        p50 = delays_sorted[len(delays_sorted)//2]
        p95 = delays_sorted[int(len(delays_sorted)*0.95)]
        anomalias = sum(1 for d in delays if d > 300)
        print(f"  {op:<15} {p50:>10.2f} {p95:>10.2f} {anomalias:>16,}")

    # ---- 2. 运营商成本效率 ----
    print(f"\n── 💰 COSTO-EFICIENCIA POR OPERADOR ──")
    op_stats = defaultdict(lambda: {"total": 0, "delivered": 0, "costo": 0.0})
    for f in filas:
        op = f["operador"]
        op_stats[op]["total"] += 1
        if f["estado"] == "DELIVERED":
            op_stats[op]["delivered"] += 1
        op_stats[op]["costo"] += float(f["precio"] or 0)

    print(f"  {'Operador':<15} {'Enviados':>10} {'Entrega':>9} {'Fallo':>9} {'Costo total':>14} {'Costo/Entrega':>14}")
    print(f"  {'-'*15} {'-'*10} {'-'*9} {'-'*9} {'-'*14} {'-'*14}")
    for op, s in sorted(op_stats.items(), key=lambda x: -x[1]["total"]):
        tasa_ent = s["delivered"] / s["total"] * 100 if s["total"] else 0
        tasa_fall = 100 - tasa_ent
        costo_entrega = s["costo"] / s["delivered"] if s["delivered"] else 0
        print(f"  {op:<15} {s['total']:>10,} {tasa_ent:>8.2f}% {tasa_fall:>8.2f}% {s['costo']:>13,.0f} {costo_entrega:>14.2f}")

    # ---- 3. 错误分类与建议 ----
    print(f"\n── ❌ ERRORES Y ACCIONES SUGERIDAS ──")
    errores = Counter(f["error"] for f in filas if f["error"] != "NO_ERROR")
    for err, cnt in errores.most_common(10):
        accion = ERROR_ACTIONS.get(err, "—")
        print(f"  {err:<38} {cnt:>5,}  →  {accion}")

    # ---- 4. 按小时分布 ----
    print(f"\n── 🕐 DISTRIBUCIÓN POR HORA (COLOMBIA) ──")
    horas = Counter(f["hora_col"] for f in filas if f["hora_col"] is not None)
    for h in sorted(horas.keys()):
        v = horas[h]
        barra = "█" * int(v / total * 50)
        print(f"  {h:02d}:00  {v:>7,}  {barra}")

    # ---- 5. 日环比 ----
    historico_path = "historico.csv"
    hoy = datetime.now(TZ_COL).strftime("%Y-%m-%d")
    fila_actual = {
        "fecha": hoy,
        "total": total,
        "delivered": delivered,
        "tasa_entrega": round(delivered/total*100, 2),
        "tasa_fallo": round(fallidos/total*100, 2),
        "costo": round(costo_total, 0),
        "costo_unitario": round(costo_total/delivered, 2) if delivered else 0,
    }

    # 读取已有历史
    historico = []
    if os.path.exists(historico_path):
        with open(historico_path, "r", encoding="utf-8-sig") as f:
            historico = list(csv.DictReader(f, delimiter=";"))

    # 追加今日（如果不存在）
    if not any(h["fecha"] == hoy for h in historico):
        historico.append(fila_actual)

    # 写回
    with open(historico_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(fila_actual.keys()), delimiter=";")
        w.writeheader()
        w.writerows(historico)

    if len(historico) >= 2:
        print(f"\n── 📊 COMPARACIÓN CON DÍA ANTERIOR ──")
        ayer = historico[-2]
        print(f"  {'Indicador':<20} {ayer['fecha']:>12} {hoy:>12} {'Cambio':>10}")
        print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*10}")
        for key, label in [("total", "Enviados"), ("tasa_entrega", "Tasa entrega"),
                           ("tasa_fallo", "Tasa fallo"), ("costo", "Costo total"),
                           ("costo_unitario", "Costo unitario")]:
            v_ayer = float(ayer[key])
            v_hoy = float(fila_actual[key])
            diff = v_hoy - v_ayer
            signo = "+" if diff > 0 else ""
            print(f"  {label:<20} {v_ayer:>12,.2f} {v_hoy:>12,.2f} {signo}{diff:>9,.2f}")

    print(f"\n{'='*70}\n")


# ============ 6. HTML 报告（含图表） ============
def generar_html(filas, path="reporte_hoy.html"):
    total = len(filas)
    if total == 0:
        return

    estados = Counter(f["estado"] for f in filas)
    errores = Counter(f["error"] for f in filas if f["error"] != "NO_ERROR")
    operadores = Counter(f["operador"] for f in filas)
    por_hora = Counter(f["hora_col"] for f in filas if f["hora_col"] is not None)
    costo_total = sum(float(f["precio"] or 0) for f in filas)

    def barras_svg(data, color="#3498db", ancho=800, alto_barra=28):
        if not data:
            return "<p>Sin datos</p>"
        maximo = max(data.values()) or 1
        total_local = sum(data.values())
        svg = [f'<svg width="{ancho}" height="{len(data)*(alto_barra+6)+10}" style="font-family:sans-serif;font-size:13px;">']
        for i, (k, v) in enumerate(sorted(data.items(), key=lambda x: -x[1])):
            y = i * (alto_barra + 6) + 5
            bw = int((v / maximo) * (ancho - 350))
            pct = (v / total_local) * 100
            svg.append(f'<text x="0" y="{y+19}" fill="#333">{k}</text>')
            svg.append(f'<rect x="250" y="{y}" width="{bw}" height="{alto_barra}" fill="{color}" rx="4"/>')
            svg.append(f'<text x="{260+bw}" y="{y+19}" fill="#333">{v:,} ({pct:.2f}%)</text>')
        svg.append("</svg>")
        return "".join(svg)

    def barras_hora(data):
        maximo = max(data.values()) if data else 1
        ancho, alto = 900, 300
        bw = ancho / 24
        partes = [f'<svg width="{ancho}" height="{alto+40}" style="font-family:sans-serif;">']
        for h in range(24):
            v = data.get(h, 0)
            altura = int((v / maximo) * (alto - 40)) if maximo else 0
            x = h * bw + 5
            y = alto - altura - 20
            partes.append(f'<rect x="{x}" y="{y}" width="{bw-10}" height="{altura}" fill="#3498db" rx="3"/>')
            partes.append(f'<text x="{x+bw/2-5}" y="{alto+5}" font-size="11" fill="#555">{h:02d}</text>')
            if v:
                partes.append(f'<text x="{x+bw/2-5}" y="{y-4}" font-size="10" fill="#333" text-anchor="middle">{v}</text>')
        partes.append("</svg>")
        return "".join(partes)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Reporte SMS — Hoy</title>
<style>
body {{ font-family: -apple-system,'Segoe UI',sans-serif; margin:0; padding:30px; background:#f5f6fa; color:#2c3e50; }}
h1 {{ margin-top:0; }} h2 {{ border-bottom:2px solid #3498db; padding-bottom:6px; margin-top:40px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:15px; margin:20px 0; }}
.card {{ background:white; padding:20px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.card .num {{ font-size:28px; font-weight:bold; color:#3498db; }}
.card .lbl {{ font-size:13px; color:#7f8c8d; margin-top:5px; }}
.section {{ background:white; padding:25px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.08); margin-bottom:25px; }}
.ok {{ color:#2ecc71; }} .bad {{ color:#e74c3c; }}
</style></head>
<body>
<h1>📊 Reporte SMS — {datetime.now(TZ_COL).strftime('%Y-%m-%d')} (Hora Colombia)</h1>
<p style="color:#7f8c8d">Generado: {datetime.now(TZ_COL).strftime('%H:%M:%S')} — UTC-5</p>
<div class="cards">
  <div class="card"><div class="num">{total:,}</div><div class="lbl">Total mensajes</div></div>
  <div class="card"><div class="num">{estados.get('DELIVERED',0):,}</div><div class="lbl ok">Entregados</div></div>
  <div class="card"><div class="num">{estados.get('REJECTED',0):,}</div><div class="lbl bad">Rechazados</div></div>
  <div class="card"><div class="num">${costo_total:,.0f}</div><div class="lbl">Costo total (COP)</div></div>
</div>
<div class="section"><h2>📬 Estado de los envíos</h2>{barras_svg(estados)}</div>
<div class="section"><h2>❌ Errores</h2>{barras_svg(errores, "#e74c3c") if errores else "<p>Sin errores ✅</p>"}</div>
<div class="section"><h2>🕐 Envíos por hora (Colombia)</h2>{barras_hora(por_hora)}</div>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🌐 HTML: {path}")


# ============ MAIN ============
if __name__ == "__main__":
    t0 = datetime.now(TZ_COL)

    # ---- 先发送一条带追踪的测试 SMS（可选，注释掉则只查日志） ----
    # async def test_send():
    #     connector = aiohttp.TCPConnector(limit=5)
    #     async with aiohttp.ClientSession(connector=connector) as s:
    #         await send_sms_with_tracking(
    #             s,
    #             to_number="573001234567",  # ← 替换为你的测试号码
    #             text="QNT: Prueba con clicks. Visita: https://eej.at/test123",
    #             tracking_url="https://tu-servidor.com/webhook/clicks",  # ← 替换为你的 webhook
    #         )
    # asyncio.run(test_send())

    # ---- 查询今日日志 ----
    logs = asyncio.run(descargar_hoy_logs())
    if not logs:
        print("Sin datos.")
        raise SystemExit

    filas = extraer_filas(logs)
    exportar_csv(filas)
    reporte(filas)
    generar_html(filas)

    print(f"✅ Listo. Abrí 'reporte_hoy.html' con doble clic.")
    print(f"⏱ Total: {(datetime.now(TZ_COL)-t0).total_seconds():.1f}s")