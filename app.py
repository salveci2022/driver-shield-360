"""
DRIVER-SHIELD-360 PREMIUM v3.0
Sistema de Segurança para Motoristas
Correções: DMS persistente, proteção API, checklist, histórico viagens
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, send_file
from datetime import datetime, timezone, timedelta
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from functools import wraps
import json, os, urllib.request, threading, time

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "driver-shield-360-premium-2026")

BR_TZ = timezone(timedelta(hours=-3))

DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
CONTACTS_FILE   = os.path.join(DATA_DIR, "contacts.json")
ALERTS_FILE     = os.path.join(DATA_DIR, "alerts.json")
VIAGEM_FILE     = os.path.join(DATA_DIR, "viagem.json")
HISTORICO_FILE  = os.path.join(DATA_DIR, "historico_viagens.json")
TOKENS_FILE     = os.path.join(DATA_DIR, "tokens.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════
# CONFIGURAÇÕES
# ══════════════════════════════════════════
ZAPI_INSTANCE   = os.environ.get("ZAPI_INSTANCE", "")
ZAPI_TOKEN      = os.environ.get("ZAPI_TOKEN", "")
ZAPI_CLIENT_TKN = os.environ.get("ZAPI_CLIENT_TOKEN", "")
PAINEL_SENHA    = os.environ.get("PAINEL_SENHA", "shield360")
API_SECRET      = os.environ.get("API_SECRET", "ds360-api-2026")  # token para proteger /api/panic

# ══════════════════════════════════════════
# PERSISTÊNCIA
# ══════════════════════════════════════════
def _load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_contacts():  return _load_json(CONTACTS_FILE, [])
def save_contacts(c): _save_json(CONTACTS_FILE, c)
def load_alerts():    return _load_json(ALERTS_FILE, [])
def save_alerts(a):   _save_json(ALERTS_FILE, a)
def load_viagem():    return _load_json(VIAGEM_FILE, {"ativa": False})
def save_viagem(v):   _save_json(VIAGEM_FILE, v)
def load_historico(): return _load_json(HISTORICO_FILE, [])
def save_historico(h): _save_json(HISTORICO_FILE, h)

def now_br_str():
    return datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")

def now_ts():
    return datetime.now(timezone.utc).timestamp()

# ══════════════════════════════════════════
# PROTEÇÃO DA API — token simples
# ══════════════════════════════════════════
def api_auth_required(f):
    """Protege endpoints /api/ contra chamadas externas não autorizadas."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Shield-Token") or (
            request.get_json(force=True, silent=True) or {}
        ).get("_token")
        if token != API_SECRET:
            return jsonify({"ok": False, "erro": "Não autorizado"}), 401
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════
# WHATSAPP Z-API
# ══════════════════════════════════════════
def enviar_whatsapp(numero, mensagem):
    if not ZAPI_INSTANCE or not ZAPI_TOKEN or not numero:
        print(f"[ZAPI] Ignorado — não configurado ou número vazio ({numero})")
        return False
    try:
        numero_limpo = "".join(filter(str.isdigit, numero))
        url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}/send-text"
        payload = json.dumps({"phone": numero_limpo, "message": mensagem}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Client-Token": ZAPI_CLIENT_TKN,
        }
        req  = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode("utf-8")
        print(f"[ZAPI] ✅ Enviado para {numero_limpo}: {body}")
        return True
    except Exception as e:
        print(f"[ZAPI] ❌ Erro ao enviar para {numero}: {e}")
        return False

def notificar_contatos(alerta, prefixo="🚨 ALERTA"):
    contacts = load_contacts()
    motorista = alerta.get("motorista", "Motorista")
    ocorrencia = alerta.get("ocorrencia", "Emergência")
    ts = alerta.get("ts", "")
    lat = alerta.get("lat")
    lng = alerta.get("lng")

    maps_link = f"\n📍 *Localização:* https://www.google.com/maps?q={lat},{lng}&z=16" if lat and lng \
                else "\n📍 _Localização não disponível_"

    msg = (
        f"{prefixo} *DRIVER-SHIELD-360*\n\n"
        f"🚕 Motorista: *{motorista}*\n"
        f"⚠️ Ocorrência: *{ocorrencia}*\n"
        f"⏰ Horário: {ts}"
        f"{maps_link}\n\n"
        f"Acesse o painel: https://driver-shield-360.onrender.com/painel/login"
    )
    for c in contacts:
        numero = c.get("telefone", "")
        if numero:
            enviar_whatsapp(numero, msg)

# ══════════════════════════════════════════
# DEAD MAN'S SWITCH — PERSISTENTE
# Usa timestamp de expiração salvo em disco.
# Ao acordar do hibernate, verifica se expirou.
# ══════════════════════════════════════════
_dms_timer = None

def _disparar_dms():
    viagem = load_viagem()
    if not viagem.get("ativa"):
        return
    alerta = {
        "ts":         now_br_str(),
        "motorista":  viagem.get("motorista", "Motorista"),
        "ocorrencia": "⏰ ALERTA AUTOMÁTICO — Motorista não confirmou chegada",
        "lat":        viagem.get("lat_inicio"),
        "lng":        viagem.get("lng_inicio"),
        "status":     "Ativo",
        "tipo":       "dead_mans_switch"
    }
    alerts = load_alerts()
    alerts.append(alerta)
    save_alerts(alerts[-500:])
    save_viagem({"ativa": False, "ultimo_disparo": now_br_str()})
    notificar_contatos(alerta, prefixo="⏰ ALERTA AUTOMÁTICO")
    print(f"[DMS] Disparado: {alerta['motorista']}")

def iniciar_dms(segundos, motorista, lat=None, lng=None):
    global _dms_timer
    cancelar_dms()
    expira_em = now_ts() + segundos
    viagem = {
        "ativa":       True,
        "motorista":   motorista,
        "inicio":      now_ts(),
        "duracao":     segundos,
        "expira_em":   expira_em,   # ← persistido em disco
        "lat_inicio":  lat,
        "lng_inicio":  lng
    }
    save_viagem(viagem)
    _dms_timer = threading.Timer(segundos, _disparar_dms)
    _dms_timer.daemon = True
    _dms_timer.start()
    print(f"[DMS] Timer iniciado: {segundos}s — expira {datetime.fromtimestamp(expira_em, BR_TZ).strftime('%H:%M:%S')}")

def cancelar_dms():
    global _dms_timer
    if _dms_timer:
        _dms_timer.cancel()
        _dms_timer = None

def verificar_dms_ao_iniciar():
    """Ao reiniciar o servidor, checa se havia viagem ativa e se já expirou."""
    viagem = load_viagem()
    if not viagem.get("ativa"):
        return
    expira_em = viagem.get("expira_em", 0)
    restante = expira_em - now_ts()
    if restante <= 0:
        print("[DMS] Viagem expirada durante hibernate — disparando agora")
        _disparar_dms()
    else:
        print(f"[DMS] Retomando timer: {int(restante)}s restantes")
        global _dms_timer
        _dms_timer = threading.Timer(restante, _disparar_dms)
        _dms_timer.daemon = True
        _dms_timer.start()

# ══════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("painel_logado"):
            return redirect(url_for("painel_login"))
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════
# ROTAS PRINCIPAIS
# ══════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/motorista")
def motorista():
    contacts = load_contacts()
    return render_template("motorista.html", contacts=contacts)

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    contacts = load_contacts()
    if request.method == "POST":
        acao = request.form.get("acao", "add")
        if acao == "delete":
            idx = request.form.get("idx")
            try:
                i = int(idx)
                if 0 <= i < len(contacts):
                    contacts.pop(i)
                    save_contacts(contacts)
                    flash("Contato removido.", "ok")
            except Exception:
                flash("Não foi possível remover.", "erro")
            return redirect(url_for("cadastro"))
        nome     = (request.form.get("nome") or "").strip()
        telefone = (request.form.get("telefone") or "").strip()
        relacao  = (request.form.get("relacao") or "Contato").strip()
        if not nome or not telefone:
            flash("Preencha nome e telefone.", "erro")
            return redirect(url_for("cadastro"))
        if len(contacts) >= 5:
            flash("Limite atingido: máximo 5 contatos.", "erro")
            return redirect(url_for("cadastro"))
        if any(c.get("telefone") == telefone for c in contacts):
            flash("Esse telefone já está cadastrado.", "erro")
            return redirect(url_for("cadastro"))
        contacts.append({"nome": nome, "telefone": telefone, "relacao": relacao})
        save_contacts(contacts)
        flash("Contato cadastrado com sucesso!", "ok")
        return redirect(url_for("cadastro"))
    return render_template("cadastro.html", contacts=contacts)

@app.route("/checklist")
def checklist():
    return render_template("checklist.html")

@app.route("/historico")
def historico_viagens():
    hist = list(reversed(load_historico()))
    return render_template("historico.html", viagens=hist)

# ── Painel ──────────────────────────────
@app.route("/painel/login", methods=["GET", "POST"])
def painel_login():
    if request.method == "POST":
        senha = request.form.get("senha", "")
        if senha == PAINEL_SENHA:
            session["painel_logado"] = True
            return redirect(url_for("painel"))
        flash("Senha incorreta.", "erro")
    return render_template("painel_login.html")

@app.route("/painel/logout")
def painel_logout():
    session.pop("painel_logado", None)
    return redirect(url_for("painel_login"))

@app.route("/painel")
@login_required
def painel():
    return render_template("painel.html")

@app.route("/relatorio")
@login_required
def relatorio():
    alerts = list(reversed(load_alerts()))
    return render_template("relatorio.html", alerts=alerts)

@app.route("/ia")
def ia_seguranca():
    return render_template("ia_seguranca.html")

@app.route("/login")
def login():
    return redirect(url_for("painel_login"))

@app.route("/pessoa_sair")
def pessoa_sair():
    return render_template("pessoa_sair.html")

# ══════════════════════════════════════════
# PWA
# ══════════════════════════════════════════
@app.route("/manifest.json")
def manifest():
    m = {
        "name": "Driver Shield 360",
        "short_name": "Shield 360",
        "description": "Segurança para motoristas de aplicativo",
        "start_url": "/motorista",
        "display": "standalone",
        "background_color": "#0a0e1a",
        "theme_color": "#0088ff",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    from flask import Response
    return Response(json.dumps(m), mimetype="application/json")

@app.route("/sw.js")
def service_worker():
    sw = """
const CACHE = 'shield360-v3';
const URLS = ['/motorista', '/cadastro', '/checklist'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(URLS)));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
});
self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).catch(() => r))
  );
});
"""
    from flask import Response
    return Response(sw, mimetype="application/javascript")

# ══════════════════════════════════════════
# RELATÓRIO PDF
# ══════════════════════════════════════════
@app.route("/relatorio.pdf")
@login_required
def relatorio_pdf():
    alerts = list(reversed(load_alerts()))
    buffer = BytesIO()
    pdf = pdf_canvas.Canvas(buffer, pagesize=A4)
    larg, alt = A4

    pdf.setTitle("Relatório — Driver Shield 360")
    pdf.setFillColorRGB(0.04, 0.05, 0.10)
    pdf.rect(0, 0, larg, alt, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 0.53, 1)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(50, alt - 55, "DRIVER-SHIELD-360 PREMIUM v3.0")
    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, alt - 75, f"Relatório de Ocorrências — Gerado em: {now_br_str()}")
    pdf.setFillColorRGB(0, 0.53, 1)
    pdf.rect(50, alt - 85, larg - 100, 2, fill=1, stroke=0)

    y = alt - 115
    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de ocorrências: {len(alerts)}")
    y -= 25

    for i, a in enumerate(alerts):
        if y < 80:
            pdf.showPage()
            pdf.setFillColorRGB(0.04, 0.05, 0.10)
            pdf.rect(0, 0, larg, alt, fill=1, stroke=0)
            y = alt - 50
        tipo = a.get("tipo", "")
        cor = (1, 0.4, 0) if tipo == "dead_mans_switch" else (0, 0.53, 1)
        pdf.setFillColorRGB(0.1, 0.15, 0.25)
        pdf.rect(48, y - 12, larg - 96, 60, fill=1, stroke=0)
        pdf.setFillColorRGB(*cor)
        pdf.rect(48, y + 46, larg - 96, 2, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(58, y + 32, f"#{len(alerts)-i}  {a['ts']}  —  {a['ocorrencia'][:60]}")
        pdf.setFillColorRGB(0.7, 0.8, 0.9)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(58, y + 16, f"Motorista: {a['motorista']}   |   Status: {a.get('status','—')}")
        loc = f"Lat: {a['lat']}  Lng: {a['lng']}" if a.get('lat') and a.get('lng') else "Localização não disponível"
        pdf.drawString(58, y + 2, f"Local: {loc}")
        y -= 78

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
        download_name=f"driver_shield_360_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S')}.pdf",
        mimetype="application/pdf")

# ══════════════════════════════════════════
# APIs — PROTEGIDAS
# ══════════════════════════════════════════
@app.route("/api/contacts")
def api_contacts():
    # Pública — motorista precisa carregar sem token
    return jsonify(load_contacts())

@app.route("/api/token")
def api_token():
    """Retorna o token da API para o frontend autenticado."""
    return jsonify({"token": API_SECRET})

@app.route("/api/panic", methods=["POST"])
@api_auth_required
def api_panic():
    data = request.get_json(force=True, silent=True) or {}
    alerta = {
        "ts":         now_br_str(),
        "motorista":  (data.get("motorista") or "Motorista").strip(),
        "ocorrencia": (data.get("ocorrencia") or "Emergência").strip(),
        "lat":        data.get("lat"),
        "lng":        data.get("lng"),
        "status":     "Ativo",
        "tipo":       "manual"
    }
    alerts = load_alerts()
    alerts.append(alerta)
    save_alerts(alerts[-500:])

    # Cancela DMS se viagem estava ativa
    viagem = load_viagem()
    if viagem.get("ativa"):
        cancelar_dms()
        save_viagem({"ativa": False, "cancelado_por": "panic"})

    print(f"[ALERTA] {alerta['ts']} | {alerta['motorista']} | {alerta['ocorrencia']}")
    try:
        notificar_contatos(alerta)
    except Exception as e:
        print(f"[ALERTA] Erro na notificação: {e}")

    return jsonify({"ok": True, "alerta": alerta})

@app.route("/api/alerts")
def api_alerts():
    return jsonify(load_alerts())

@app.route("/api/resolve", methods=["POST"])
def api_resolve():
    data = request.get_json(force=True, silent=True) or {}
    idx = data.get("idx")
    alerts = load_alerts()
    if idx is not None and 0 <= int(idx) < len(alerts):
        alerts[int(idx)]["status"] = "Resolvido"
        save_alerts(alerts)
    return jsonify({"ok": True})

@app.route("/api/clear_alerts", methods=["POST"])
def api_clear_alerts():
    save_alerts([])
    return jsonify({"ok": True})

# ── Dead Man's Switch ────────────────────
@app.route("/api/viagem/iniciar", methods=["POST"])
@api_auth_required
def api_viagem_iniciar():
    data = request.get_json(force=True, silent=True) or {}
    motorista = (data.get("motorista") or "Motorista").strip()
    minutos   = int(data.get("minutos", 30))
    lat = data.get("lat")
    lng = data.get("lng")

    if minutos < 5 or minutos > 180:
        return jsonify({"ok": False, "erro": "Tempo entre 5 e 180 minutos."})

    iniciar_dms(minutos * 60, motorista, lat, lng)

    # Salva no histórico
    hist = load_historico()
    hist.append({
        "inicio": now_br_str(),
        "motorista": motorista,
        "minutos": minutos,
        "lat": lat,
        "lng": lng,
        "resultado": "em_andamento"
    })
    save_historico(hist[-200:])

    return jsonify({"ok": True, "motorista": motorista, "minutos": minutos, "segundos": minutos * 60})

@app.route("/api/viagem/confirmar", methods=["POST"])
@api_auth_required
def api_viagem_confirmar():
    cancelar_dms()
    viagem = load_viagem()

    # Atualiza histórico — última viagem confirmada
    hist = load_historico()
    for v in reversed(hist):
        if v.get("resultado") == "em_andamento":
            v["resultado"] = "chegou_segura"
            v["fim"] = now_br_str()
            break
    save_historico(hist)

    save_viagem({"ativa": False, "ultimo_fim": now_br_str()})
    return jsonify({"ok": True})

@app.route("/api/viagem/status")
def api_viagem_status():
    viagem = load_viagem()
    if not viagem.get("ativa"):
        return jsonify({"ativa": False})
    expira_em = viagem.get("expira_em", 0)
    restante  = max(0, int(expira_em - now_ts()))
    return jsonify({
        "ativa":     True,
        "motorista": viagem.get("motorista"),
        "restante":  restante,
        "duracao":   viagem.get("duracao", 0)
    })

@app.route("/api/viagem/cancelar", methods=["POST"])
@api_auth_required
def api_viagem_cancelar():
    cancelar_dms()
    hist = load_historico()
    for v in reversed(hist):
        if v.get("resultado") == "em_andamento":
            v["resultado"] = "cancelada"
            v["fim"] = now_br_str()
            break
    save_historico(hist)
    save_viagem({"ativa": False})
    return jsonify({"ok": True})

# ── Passageiro suspeito ──────────────────
@app.route("/api/passageiro_suspeito", methods=["POST"])
@api_auth_required
def api_passageiro_suspeito():
    data = request.get_json(force=True, silent=True) or {}
    alerta = {
        "ts":         now_br_str(),
        "motorista":  (data.get("motorista") or "Motorista").strip(),
        "ocorrencia": f"👤 Passageiro suspeito — {data.get('descricao', 'Sem descrição')}",
        "lat":        data.get("lat"),
        "lng":        data.get("lng"),
        "status":     "Ativo",
        "tipo":       "passageiro_suspeito"
    }
    alerts = load_alerts()
    alerts.append(alerta)
    save_alerts(alerts[-500:])
    try:
        notificar_contatos(alerta, prefixo="👤 PASSAGEIRO SUSPEITO")
    except Exception as e:
        print(f"[SUSPEITO] Erro: {e}")
    return jsonify({"ok": True})

# ══════════════════════════════════════════
# RUN
# ══════════════════════════════════════════
if __name__ == "__main__":
    verificar_dms_ao_iniciar()
    print("=" * 55)
    print("🛡️  DRIVER-SHIELD-360 PREMIUM v3.0")
    print("=" * 55)
    zapi_ok = bool(ZAPI_INSTANCE and ZAPI_TOKEN)
    print(f"📱 WhatsApp Z-API:  {'✅ OK' if zapi_ok else '⚠️  NÃO CONFIGURADO'}")
    print(f"🔐 Senha painel:    {PAINEL_SENHA}")
    print(f"🔑 API Secret:      {API_SECRET[:8]}...")
    print(f"⏰ DMS persistente: ✅")
    print(f"📍 GPS:             ✅")
    print(f"📲 PWA:             ✅")
    print("=" * 55)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

# Chamada ao importar pelo gunicorn também
verificar_dms_ao_iniciar()
