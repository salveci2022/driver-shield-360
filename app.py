from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request, send_from_directory

APP_NAME = "Driver-Shield 360"

def _ensure_instance(app: Flask) -> None:
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except Exception:
        # On some platforms instance_path may not be creatable; fallback to /tmp
        app.instance_path = "/tmp/driver_shield_360"
        os.makedirs(app.instance_path, exist_ok=True)

def _read_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

app = Flask(__name__, static_folder="static", template_folder="templates")
_ensure_instance(app)

CONTACTS_PATH = os.path.join(app.instance_path, "contacts.json")
ALERTS_PATH = os.path.join(app.instance_path, "alerts.json")

DEFAULT_OCCURRENCES = [
    "Abordagem suspeita",
    "Tentativa de assalto",
    "Ameaça / intimidação",
    "Passageiro agressivo",
    "Seguimento / perseguição",
    "Emergência médica",
    "Outro",
]

@app.get("/health")
def health():
    return jsonify({"ok": True, "app": APP_NAME})

@app.get("/")
def index():
    return render_template("index.html", app_name=APP_NAME)

@app.get("/motorista")
def motorista():
    contacts = _read_json(CONTACTS_PATH, [])
    return render_template(
        "motorista.html",
        app_name=APP_NAME,
        occurrences=DEFAULT_OCCURRENCES,
        contacts=contacts,
    )

@app.get("/cadastro")
def cadastro():
    contacts = _read_json(CONTACTS_PATH, [])
    return render_template("cadastro.html", app_name=APP_NAME, contacts=contacts, max_contacts=3)

@app.get("/painel")
def painel():
    # Painel da pessoa de confiança (sem senha)
    return render_template("painel.html", app_name=APP_NAME)

@app.get("/relatorio")
def relatorio():
    alerts = _read_json(ALERTS_PATH, [])
    return render_template("relatorio.html", app_name=APP_NAME, alerts=list(reversed(alerts)))

# ---------------- APIs ----------------

@app.get("/api/contacts")
def api_get_contacts():
    return jsonify({"contacts": _read_json(CONTACTS_PATH, [])})

@app.post("/api/contacts")
def api_add_contact():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()[:60]
    phone = str(payload.get("phone", "")).strip()[:30]

    if not name or not phone:
        return jsonify({"ok": False, "error": "Nome e telefone são obrigatórios."}), 400

    contacts: List[Dict[str, str]] = _read_json(CONTACTS_PATH, [])
    # enforce max 3
    if len(contacts) >= 3:
        return jsonify({"ok": False, "error": "Limite de 3 pessoas de confiança atingido."}), 400

    # normalize phone digits (keep +)
    phone_norm = re_sub_phone(phone)

    contacts.append({"name": name.upper(), "phone": phone_norm})
    _write_json(CONTACTS_PATH, contacts)
    return jsonify({"ok": True, "contacts": contacts})

@app.delete("/api/contacts")
def api_clear_contacts():
    _write_json(CONTACTS_PATH, [])
    return jsonify({"ok": True})

def re_sub_phone(phone: str) -> str:
    # Keep leading +, remove spaces and non-digits
    phone = phone.replace(" ", "")
    plus = "+" if phone.startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return plus + digits

@app.get("/api/alerts")
def api_get_alerts():
    return jsonify({"alerts": _read_json(ALERTS_PATH, [])})

@app.delete("/api/alerts")
def api_clear_alerts():
    _write_json(ALERTS_PATH, [])
    return jsonify({"ok": True})

@app.post("/api/alert")
def api_create_alert():
    payload = request.get_json(silent=True) or {}

    occurrence = str(payload.get("occurrence", "Abordagem suspeita")).strip()
    if occurrence not in DEFAULT_OCCURRENCES:
        occurrence = "Outro"

    driver_name = str(payload.get("driver_name", "")).strip()[:60]
    lat = payload.get("lat")
    lng = payload.get("lng")
    accuracy = payload.get("accuracy")

    alert = {
        "ts": _now_iso(),
        "occurrence": occurrence,
        "driver_name": driver_name,
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
    }

    alerts: List[Dict[str, Any]] = _read_json(ALERTS_PATH, [])
    alerts.append(alert)
    # keep last 100
    alerts = alerts[-100:]
    _write_json(ALERTS_PATH, alerts)

    return jsonify({"ok": True, "alert": alert})

# PWA helpers (optional)
@app.get("/service-worker.js")
def service_worker():
    return send_from_directory(app.static_folder, "service-worker.js")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
