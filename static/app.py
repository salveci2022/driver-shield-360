from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from datetime import datetime, timezone, timedelta
import json, os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "driver-shield-360-secret")

BR_TZ = timezone(timedelta(hours=-3))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONTACTS_FILE = os.path.join(DATA_DIR, "contacts.json")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")

os.makedirs(DATA_DIR, exist_ok=True)

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

def load_contacts():
    return _load_json(CONTACTS_FILE, [])

def save_contacts(contacts):
    _save_json(CONTACTS_FILE, contacts)

def load_alerts():
    return _load_json(ALERTS_FILE, [])

def save_alerts(alerts):
    _save_json(ALERTS_FILE, alerts)

def now_br_str():
    return datetime.now(BR_TZ).strftime("%Y-%m-%d %H:%M:%S")

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

        if len(contacts) >= 3:
            flash("Limite atingido: só é possível cadastrar até 3 pessoas de confiança.", "erro")
            return redirect(url_for("cadastro"))

        if any(c.get("telefone") == telefone for c in contacts):
            flash("Esse telefone já está cadastrado.", "erro")
            return redirect(url_for("cadastro"))

        contacts.append({"nome": nome, "telefone": telefone})
        save_contacts(contacts)
        flash("Contato cadastrado com sucesso.", "ok")
        return redirect(url_for("cadastro"))

    return render_template("cadastro.html", contacts=contacts)

@app.route("/painel")
def painel():
    # Painel aberto (sem senha) para pessoa de confiança
    return render_template("painel.html", modo_aberto=True)

@app.route("/login")
def login():
    # Atalho sem redirecionamento (evita loop)
    return render_template("painel.html", modo_aberto=True)

@app.route("/pessoa_sair")
def pessoa_sair():
    return render_template("pessoa_sair.html")

@app.route("/relatorio")
def relatorio():
    alerts = load_alerts()
    alerts = list(reversed(alerts))
    return render_template("relatorio.html", alerts=alerts)

@app.route("/api/contacts")
def api_contacts():
    return jsonify(load_contacts())

@app.route("/api/panic", methods=["POST"])
def api_panic():
    data = request.get_json(force=True, silent=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")
    ocorrencia = (data.get("ocorrencia") or "Emergência").strip()
    motorista_nome = (data.get("motorista") or "Motorista").strip()

    alerts = load_alerts()
    alerts.append({
        "ts": now_br_str(),
        "motorista": motorista_nome,
        "ocorrencia": ocorrencia,
        "lat": lat,
        "lng": lng,
    })
    save_alerts(alerts)
    return jsonify({"ok": True})

@app.route("/api/alerts")
def api_alerts():
    return jsonify(load_alerts())

@app.route("/api/clear_alerts", methods=["POST"])
def api_clear_alerts():
    save_alerts([])
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
