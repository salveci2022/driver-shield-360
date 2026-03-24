"""
DRIVER-SHIELD-360 PREMIUM
Sistema de Segurança para Motoristas
Versão PRO: WhatsApp Z-API, Login, Relatório PDF
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, send_file
from datetime import datetime, timezone, timedelta
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from functools import wraps
import json, os, urllib.request, urllib.parse

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "driver-shield-360-premium-2026")

BR_TZ = timezone(timedelta(hours=-3))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONTACTS_FILE = os.path.join(DATA_DIR, "contacts.json")
ALERTS_FILE   = os.path.join(DATA_DIR, "alerts.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════
# CONFIGURAÇÕES Z-API (variáveis de ambiente)
# ══════════════════════════════════════════
ZAPI_INSTANCE   = os.environ.get("ZAPI_INSTANCE", "")
ZAPI_TOKEN      = os.environ.get("ZAPI_TOKEN", "")
ZAPI_CLIENT_TKN = os.environ.get("ZAPI_CLIENT_TOKEN", "")

# Senha do painel da pessoa de confiança
PAINEL_SENHA = os.environ.get("PAINEL_SENHA", "shield360")

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

def load_contacts(): return _load_json(CONTACTS_FILE, [])
def save_contacts(c): _save_json(CONTACTS_FILE, c)
def load_alerts():   return _load_json(ALERTS_FILE, [])
def save_alerts(a):  _save_json(ALERTS_FILE, a)

def now_br_str():
    return datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")

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

def notificar_contatos(alerta):
    contacts = load_contacts()
    motorista = alerta.get("motorista", "Motorista")
    ocorrencia = alerta.get("ocorrencia", "Emergência")
    ts = alerta.get("ts", "")
    lat = alerta.get("lat")
    lng = alerta.get("lng")

    maps_link = ""
    if lat and lng:
        maps_link = f"\n📍 https://www.google.com/maps?q={lat},{lng}&z=16"

    msg = (
        f"🚨 *ALERTA DRIVER-SHIELD-360*\n\n"
        f"🚕 Motorista: {motorista}\n"
        f"⚠️ Ocorrência: {ocorrencia}\n"
        f"⏰ Horário: {ts}"
        f"{maps_link}\n\n"
        f"Acesse o painel para mais detalhes."
    )

    for c in contacts:
        numero = c.get("telefone", "")
        if numero:
            enviar_whatsapp(numero, msg)

# ══════════════════════════════════════════
# LOGIN PAINEL
# ══════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("painel_logado"):
            return redirect(url_for("painel_login"))
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════
# ROTAS
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

        nome = (request.form.get("nome") or "").strip()
        telefone = (request.form.get("telefone") or "").strip()

        if not nome or not telefone:
            flash("Preencha nome e telefone.", "erro")
            return redirect(url_for("cadastro"))

        if len(contacts) >= 5:
            flash("Limite atingido: máximo 5 contatos.", "erro")
            return redirect(url_for("cadastro"))

        if any(c.get("telefone") == telefone for c in contacts):
            flash("Esse telefone já está cadastrado.", "erro")
            return redirect(url_for("cadastro"))

        contacts.append({"nome": nome, "telefone": telefone})
        save_contacts(contacts)
        flash("Contato cadastrado com sucesso!", "ok")
        return redirect(url_for("cadastro"))

    return render_template("cadastro.html", contacts=contacts)

# PAINEL — com login
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

# Manter compatibilidade
@app.route("/login")
def login():
    return redirect(url_for("painel_login"))

@app.route("/pessoa_sair")
def pessoa_sair():
    return render_template("pessoa_sair.html")

@app.route("/ia")
def ia_seguranca():
    return render_template("ia_seguranca.html")

@app.route("/relatorio")
@login_required
def relatorio():
    alerts = list(reversed(load_alerts()))
    return render_template("relatorio.html", alerts=alerts)

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

    pdf.setFillColorRGB(0, 0.67, 1)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(50, alt - 55, "DRIVER-SHIELD-360 PREMIUM")

    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, alt - 75, f"Relatório de Ocorrências — Gerado em: {now_br_str()}")

    pdf.setFillColorRGB(0, 0.67, 1)
    pdf.rect(50, alt - 85, larg - 100, 2, fill=1, stroke=0)

    y = alt - 115
    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de ocorrências: {len(alerts)}")
    y -= 25

    pdf.setFont("Helvetica", 10)
    for i, a in enumerate(alerts):
        if y < 80:
            pdf.showPage()
            pdf.setFillColorRGB(0.04, 0.05, 0.10)
            pdf.rect(0, 0, larg, alt, fill=1, stroke=0)
            y = alt - 50

        pdf.setFillColorRGB(0.1, 0.15, 0.25)
        pdf.rect(48, y - 12, larg - 96, 52, fill=1, stroke=0)

        pdf.setFillColorRGB(0, 0.67, 1)
        pdf.rect(48, y + 38, larg - 96, 2, fill=1, stroke=0)

        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(58, y + 24, f"#{len(alerts)-i}  {a['ts']}  —  {a['ocorrencia']}")

        pdf.setFillColorRGB(0.7, 0.8, 0.9)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(58, y + 8, f"Motorista: {a['motorista']}")

        loc = f"Lat: {a['lat']}  Lng: {a['lng']}" if a.get('lat') and a.get('lng') else "Localização não disponível"
        pdf.drawString(58, y - 6, f"Local: {loc}")

        y -= 70

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"driver_shield_360_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S')}.pdf",
        mimetype="application/pdf"
    )

# ══════════════════════════════════════════
# APIs
# ══════════════════════════════════════════
@app.route("/api/contacts")
def api_contacts():
    return jsonify(load_contacts())

@app.route("/api/panic", methods=["POST"])
def api_panic():
    data = request.get_json(force=True, silent=True) or {}

    alerta = {
        "ts":        now_br_str(),
        "motorista": (data.get("motorista") or "Motorista").strip(),
        "ocorrencia":(data.get("ocorrencia") or "Emergência").strip(),
        "lat":       data.get("lat"),
        "lng":       data.get("lng"),
        "status":    "Ativo"
    }

    alerts = load_alerts()
    alerts.append(alerta)
    alerts = alerts[-500:]
    save_alerts(alerts)

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

# ══════════════════════════════════════════
# RUN
# ══════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("🛡️  DRIVER-SHIELD-360 PREMIUM")
    print("    Sistema de Segurança para Motoristas")
    print("=" * 55)
    zapi_ok = bool(ZAPI_INSTANCE and ZAPI_TOKEN)
    print(f"📱 WhatsApp Z-API: {'✅ CONFIGURADO' if zapi_ok else '⚠️  NÃO CONFIGURADO'}")
    print(f"🔐 Senha do painel: {PAINEL_SENHA}")
    print("=" * 55)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
