import os
import json
import secrets
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, abort

APP_NAME = "Driver Shield 360"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONTACTS_PATH = os.path.join(DATA_DIR, "contacts.json")
ALERTS_PATH = os.path.join(DATA_DIR, "alerts.json")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change")
TERMS_VERSION = os.environ.get("TERMS_VERSION", "v1.0")
TRUST_KEY = os.environ.get("TRUST_KEY", "MUDE-ESTA-CHAVE")

TRUST_TOKENS = {}  # token -> {"created":...}

app = Flask(__name__)
app.secret_key = SECRET_KEY

def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONTACTS_PATH):
        with open(CONTACTS_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(ALERTS_PATH):
        with open(ALERTS_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)

def _load_json(path):
    _ensure_files()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path, data):
    _ensure_files()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _ts_human(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z",""))
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso

@app.get("/motorista")
def motorista():
    return render_template("motorista.html")

@app.get("/cadastro")
def cadastro():
    return render_template("cadastro.html")

@app.get("/relatorio")
def relatorio():
    return render_template("relatorio.html")

@app.get("/termos")
def termos():
    html = render_template("termos.html")
    injected = f"<script>window.TERMS_VERSION={json.dumps(TERMS_VERSION)};window.TERMS_DATE={json.dumps(datetime.now().strftime('%d/%m/%Y'))};</script>"
    return html.replace("</head>", injected + "\n</head>")

@app.get("/painel")
def painel():
    k = (request.args.get("k") or "").strip()
    if k != TRUST_KEY:
        abort(404)
    return render_template("painel.html")

# -------- API contatos --------
@app.get("/api/contacts")
def api_contacts_get():
    return jsonify({"contacts": _load_json(CONTACTS_PATH)})

@app.post("/api/contacts")
def api_contacts_add():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    pin = (payload.get("pin") or "").strip()
    if not name or not pin.isdigit() or not (4 <= len(pin) <= 6):
        return jsonify({"ok": False}), 400

    contacts = _load_json(CONTACTS_PATH)
    new_id = (max([c.get("id",0) for c in contacts]) + 1) if contacts else 1
    contacts.append({"id": new_id, "name": name, "pin": pin})
    _save_json(CONTACTS_PATH, contacts)
    return jsonify({"ok": True, "id": new_id})

@app.delete("/api/contacts/<int:cid>")
def api_contacts_del(cid: int):
    contacts = [c for c in _load_json(CONTACTS_PATH) if c.get("id") != cid]
    _save_json(CONTACTS_PATH, contacts)
    return jsonify({"ok": True})

# -------- API alertas --------
@app.post("/api/panic")
def api_panic():
    payload = request.get_json(silent=True) or {}
    driver_name = (payload.get("driver_name") or "").strip()
    occurrence = (payload.get("occurrence") or "").strip()
    location = payload.get("location", None)

    if not driver_name or not occurrence:
        return jsonify({"ok": False}), 400

    alerts = _load_json(ALERTS_PATH)
    new_id = (max([a.get("id",0) for a in alerts]) + 1) if alerts else 1
    iso = datetime.now(timezone.utc).isoformat()

    alert = {
        "id": new_id,
        "ts": iso,
        "ts_human": _ts_human(iso),
        "driver_name": driver_name,
        "occurrence": occurrence,
        "location": location if isinstance(location, dict) else None
    }
    alerts.insert(0, alert)
    _save_json(ALERTS_PATH, alerts)
    return jsonify({"ok": True, "id": new_id})

def _trust_authed():
    token = request.headers.get("X-Trust-Token", "")
    return token in TRUST_TOKENS

@app.get("/api/alerts")
def api_alerts_get():
    if request.headers.get("X-Trust-Token") and not _trust_authed():
        return jsonify({"ok": False}), 401
    return jsonify({"alerts": _load_json(ALERTS_PATH)})

@app.post("/api/alerts/clear")
def api_alerts_clear():
    _save_json(ALERTS_PATH, [])
    return jsonify({"ok": True})

# -------- API login confiança --------
@app.post("/api/trust/login")
def api_trust_login():
    payload = request.get_json(silent=True) or {}
    pin = (payload.get("pin") or "").strip()
    if not pin.isdigit():
        return jsonify({"ok": False}), 400

    contacts = _load_json(CONTACTS_PATH)
    ok = any(c.get("pin") == pin for c in contacts)
    if not ok:
        return jsonify({"ok": False}), 401

    token = secrets.token_urlsafe(24)
    TRUST_TOKENS[token] = {"created": datetime.now(timezone.utc).isoformat()}
    return jsonify({"ok": True, "token": token})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
