from __future__ import annotations
import os, json, time
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, abort

APP_NAME = "Driver-Shield 360"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONTACTS_FILE = DATA_DIR / "contacts.json"
ALERTS_FILE = DATA_DIR / "alerts.json"

MAX_CONTACTS = 3

def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def now_iso() -> str:
    # Use UTC to keep server consistent; UI can show local time if desired.
    return datetime.now(timezone.utc).isoformat()

app = Flask(__name__)

@app.get("/")
def home():
    return render_template("index.html", app_name=APP_NAME)

@app.get("/motorista")
def motorista():
    return render_template("motorista.html", app_name=APP_NAME)

@app.get("/painel")
def painel():
    # Painel da pessoa de confiança (sem senha)
    return render_template("painel.html", app_name=APP_NAME)

@app.get("/cadastro")
def cadastro():
    return render_template("cadastro_contatos.html", app_name=APP_NAME, max_contacts=MAX_CONTACTS)

@app.get("/relatorio")
def relatorio():
    return render_template("relatorio.html", app_name=APP_NAME)

# ---------- API ----------
@app.get("/api/contacts")
def api_get_contacts():
    contacts = _load_json(CONTACTS_FILE, [])
    # normalize
    if not isinstance(contacts, list):
        contacts = []
    return jsonify({"ok": True, "max": MAX_CONTACTS, "contacts": contacts})

@app.post("/api/contacts")
def api_add_contact():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    phone = str(payload.get("phone", "")).strip()

    if not name or not phone:
        return jsonify({"ok": False, "error": "Nome e telefone são obrigatórios."}), 400

    contacts = _load_json(CONTACTS_FILE, [])
    if not isinstance(contacts, list):
        contacts = []

    if len(contacts) >= MAX_CONTACTS:
        return jsonify({"ok": False, "error": f"Limite de {MAX_CONTACTS} pessoas de confiança atingido."}), 400

    # Basic phone sanitization: keep digits
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 8:
        return jsonify({"ok": False, "error": "Telefone inválido."}), 400

    contact = {
        "id": str(int(time.time() * 1000)),
        "name": name.upper(),
        "phone": digits
    }
    contacts.append(contact)
    _save_json(CONTACTS_FILE, contacts)
    return jsonify({"ok": True, "contacts": contacts})

@app.delete("/api/contacts/<cid>")
def api_delete_contact(cid: str):
    contacts = _load_json(CONTACTS_FILE, [])
    if not isinstance(contacts, list):
        contacts = []
    contacts = [c for c in contacts if str(c.get("id")) != str(cid)]
    _save_json(CONTACTS_FILE, contacts)
    return jsonify({"ok": True, "contacts": contacts})

@app.post("/api/alerts")
def api_post_alert():
    payload = request.get_json(silent=True) or {}
    occ = str(payload.get("occurrence", "")).strip() or "Abordagem suspeita"
    driver_name = str(payload.get("driver_name", "")).strip()
    lat = payload.get("lat")
    lng = payload.get("lng")
    accuracy = payload.get("accuracy")

    alert = {
        "id": str(int(time.time() * 1000)),
        "created_at": now_iso(),
        "occurrence": occ,
        "driver_name": driver_name,
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
    }

    alerts = _load_json(ALERTS_FILE, [])
    if not isinstance(alerts, list):
        alerts = []
    alerts.insert(0, alert)
    alerts = alerts[:200]  # keep last 200
    _save_json(ALERTS_FILE, alerts)
    return jsonify({"ok": True, "alert": alert})

@app.get("/api/alerts")
def api_get_alerts():
    alerts = _load_json(ALERTS_FILE, [])
    if not isinstance(alerts, list):
        alerts = []
    return jsonify({"ok": True, "alerts": alerts})

@app.post("/api/alerts/clear")
def api_clear_alerts():
    _save_json(ALERTS_FILE, [])
    return jsonify({"ok": True})

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "name": APP_NAME})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
