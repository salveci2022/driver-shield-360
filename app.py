import os
import secrets
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ===== Config =====
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(16)
TERMS_VERSION = os.getenv("TERMS_VERSION", "v1.0")

# In-memory storage (free tier friendly). For production, use a DB.
alerts = []     # newest first
contacts = []   # list of {name, whatsapp}

# ===== Helpers =====
def _utc_iso():
    return datetime.now(timezone.utc).isoformat()

def _client_ip():
    # Render puts client ip behind proxy. X-Forwarded-For can contain multiple IPs.
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""

# ===== Pages =====
@app.route("/")
def index():
    # main page for driver panel
    return render_template("motorista.html")

@app.route("/motorista")
def motorista():
    return render_template("motorista.html")

@app.route("/cadastro")
def cadastro():
    return render_template("cadastro_contatos.html")

@app.route("/painel")
def painel():
    # Panel for trusted contact (no password for speed).
    return render_template("painel.html")

@app.route("/termos")
def termos():
    return render_template("termos.html", terms_version=TERMS_VERSION)

@app.route("/relatorio")
def relatorio():
    # simple report (reuse JSON via /api/alerts)
    return render_template("relatorio.html", alerts=alerts, terms_version=TERMS_VERSION)

# ===== APIs =====
@app.route("/api/contacts", methods=["GET", "POST"])
def api_contacts():
    if request.method == "GET":
        # Do not expose full whatsapp in public pages; cadastro page masks it.
        return jsonify({"contacts": contacts})

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    whatsapp = "".join(ch for ch in str(data.get("whatsapp") or "") if ch.isdigit())

    if not name or len(whatsapp) < 10:
        return jsonify({"status": "error", "message": "Dados inválidos"}), 400

    contacts.append({"name": name, "whatsapp": whatsapp})
    return jsonify({"status": "ok", "contacts": contacts})

@app.route("/api/contacts_delete", methods=["POST"])
def api_contacts_delete():
    data = request.get_json(silent=True) or {}
    try:
        idx = int(data.get("index"))
    except Exception:
        return jsonify({"status": "error", "message": "Índice inválido"}), 400

    if idx < 0 or idx >= len(contacts):
        return jsonify({"status": "error", "message": "Fora do intervalo"}), 400

    contacts.pop(idx)
    return jsonify({"status": "ok", "contacts": contacts})

@app.route("/api/panic", methods=["POST"])
def api_panic():
    data = request.get_json(silent=True) or {}
    driver_name = (data.get("driver_name") or "Motorista").strip()
    occurrence = (data.get("occurrence") or "Ocorrência não informada").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    alert = {
        "ts": _utc_iso(),
        "ip": _client_ip(),
        "driver_name": driver_name,
        "occurrence": occurrence,
        "lat": lat,
        "lng": lng,
    }
    alerts.insert(0, alert)
    return jsonify({"status": "ok", "alert": alert})

@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    return jsonify({"alerts": alerts, "terms_version": TERMS_VERSION})

@app.route("/api/clear_alerts", methods=["POST"])
def api_clear_alerts():
    alerts.clear()
    return jsonify({"status": "ok"})

# ===== Run local =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
