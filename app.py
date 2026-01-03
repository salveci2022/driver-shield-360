from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Sessão/CSRF etc. (MVP). Em produção, defina SECRET_KEY no Render.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# "Banco" em memória (MVP). Em produção, use banco persistente.
alerts: List[Dict[str, Any]] = []
contacts: List[Dict[str, str]] = []  # {"name": ..., "login": ..., "password": ...}

TERMS_VERSION = os.environ.get("TERMS_VERSION", "v1.0")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip() -> str:
    # Render/Proxy costuma passar o IP real em X-Forwarded-For
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ====== PÁGINAS ======

@app.route("/")
def home():
    # Mantém seu comportamento atual (sem quebrar)
    return "Driver Shield 360 – versão 4 rodando ✅"


@app.route("/motorista")
def motorista():
    return render_template("motorista.html")


@app.route("/termos")
def termos():
    return render_template("termos.html")


@app.route("/relatorio")
def relatorio():
    return render_template("relatorio.html", alerts=alerts)


# ====== API ======

@app.route("/api/contacts", methods=["GET"])
def api_contacts():
    # Por padrão, devolve só dados necessários ao painel (sem senha)
    safe = [{"name": c.get("name", ""), "login": c.get("login", "")} for c in contacts]
    return jsonify(safe)


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
    alerts.insert(0, alert)  # mais recente primeiro

    return jsonify({"status": "ok", "alert": alert})


@app.route("/api/clear_alerts", methods=["POST"])
def api_clear_alerts():
    alerts.clear()
    return jsonify({"status": "ok", "message": "Alertas limpos."})


@app.route("/api/accept-terms", methods=["POST"])
def api_accept_terms():
    """
    Registro simples de consentimento LGPD:
    - timestamp UTC
    - IP
    - versão do termo
    """
    ip = _client_ip()
    ts = _utc_iso()

    # Log simples. Em produção, você pode trocar por SQLite/DB.
    with open("consent_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{ts} | IP: {ip} | Termo {TERMS_VERSION}\n")

    return jsonify({"status": "ok"})


# ====== EXECUÇÃO LOCAL ======

if __name__ == "__main__":
    # debug=True somente local
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
