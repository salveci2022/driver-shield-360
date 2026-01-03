import os
import json
import uuid
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, redirect, url_for, session

APP_NAME = "Driver Shield 360"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
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

def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _now_utc_iso():
    return datetime.now(timezone.utc).isoformat()

def _human_br(ts_iso):
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return ts_iso

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")

# ----------------------------
# Pages
# ----------------------------
@app.get("/")
def index():
    return redirect(url_for("motorista"))

@app.get("/motorista")
def motorista():
    return render_template("motorista.html")

@app.get("/cadastro")
def cadastro():
    return render_template("cadastro_contatos.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        expected = os.environ.get("PANEL_PASSWORD", "1234")
        if pw == expected:
            session["auth"] = True
            return redirect(url_for("painel"))
        return render_template("login.html", error="Senha inválida.")
    return render_template("login.html", error=None)

@app.get("/painel")
def painel():
    if not session.get("auth"):
        return redirect(url_for("login"))
    return render_template("painel.html")

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/relatorio")
def relatorio():
    return render_template("relatorio.html")

@app.get("/termos")
def termos():
    updated_at = os.environ.get("TERMS_UPDATED_AT", datetime.now().strftime("%d/%m/%Y"))
    contact = os.environ.get("CONTROLLER_CONTACT", "WhatsApp: (61) 9 9939-2090")
    return render_template("termos.html", updated_at=updated_at, contact=contact)

# ----------------------------
# API
# ----------------------------
@app.get("/api/contacts")
def api_contacts_get():
    contacts = _load_json(CONTACTS_FILE, [])
    return jsonify(ok=True, contacts=contacts)

@app.post("/api/contacts")
def api_contacts_add():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if len(name) < 2 or len(phone) < 8:
        return jsonify(ok=False, error="Nome e telefone são obrigatórios."), 400

    contacts = _load_json(CONTACTS_FILE, [])
    new_contact = {"id": uuid.uuid4().hex[:10], "name": name, "phone": phone}
    contacts.append(new_contact)
    _save_json(CONTACTS_FILE, contacts)
    return jsonify(ok=True, contact=new_contact)

@app.delete("/api/contacts/<cid>")
def api_contacts_delete(cid):
    contacts = _load_json(CONTACTS_FILE, [])
    contacts2 = [c for c in contacts if c.get("id") != cid]
    _save_json(CONTACTS_FILE, contacts2)
    return jsonify(ok=True)

@app.post("/api/accept-terms")
def api_accept_terms():
    # Minimal endpoint just to record acceptance server-side if needed.
    # We store only a timestamp (no personal data) to keep it lightweight.
    data = request.get_json(silent=True) or {}
    accepted = bool(data.get("accepted", False))
    if not accepted:
        return jsonify(ok=True)
    meta = _load_json(os.path.join(DATA_DIR, "terms_accept.json"), [])
    meta.append({"ts": _now_utc_iso()})
    _save_json(os.path.join(DATA_DIR, "terms_accept.json"), meta[-500:])  # cap
    return jsonify(ok=True)

@app.post("/api/panic")
def api_panic():
    data = request.get_json(silent=True) or {}
    driver_name = (data.get("driver_name") or "").strip()
    occurrence = (data.get("occurrence") or "").strip()
    accepted_terms = bool(data.get("accepted_terms", False))

    if not accepted_terms:
        return jsonify(ok=False, error="É necessário aceitar os termos para usar o botão."), 400
    if len(driver_name) < 2:
        return jsonify(ok=False, error="Informe seu nome/apelido."), 400
    if len(occurrence) < 2:
        return jsonify(ok=False, error="Selecione o tipo de ocorrência."), 400

    alert = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now_utc_iso(),
        "ts_human": _human_br(_now_utc_iso()),
        "driver_name": driver_name,
        "occurrence": occurrence,
        "location": data.get("location") if isinstance(data.get("location"), dict) else None,
        # Defensive logging: keep minimal metadata
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "ua": request.headers.get("User-Agent", "")[:250],
    }

    alerts = _load_json(ALERTS_FILE, [])
    alerts.append(alert)
    _save_json(ALERTS_FILE, alerts[-2000:])  # cap

    return jsonify(ok=True, alert=alert)

@app.get("/api/alerts")
def api_alerts_get():
    alerts = _load_json(ALERTS_FILE, [])
    return jsonify(ok=True, alerts=alerts)

@app.post("/api/clear_alerts")
def api_clear_alerts():
    _save_json(ALERTS_FILE, [])
    return jsonify(ok=True)

# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
