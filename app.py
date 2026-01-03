from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, redirect, url_for

app = Flask(__name__)

# Em produção, defina SECRET_KEY no Render (Environment → Environment Variables)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# Versão dos termos (para log de consentimento)
TERMS_VERSION = os.environ.get("TERMS_VERSION", "v1")

# "Banco" em memória (MVP). Em produção, use banco persistente.
alerts: List[Dict[str, Any]] = []
contacts: List[Dict[str, str]] = []  # {"name":..., "login":..., "password":...}

# ====== HELPERS ======

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _client_ip() -> str:
    # Render / proxies: X-Forwarded-For pode vir como "ip1, ip2"
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("X-Real-IP", request.remote_addr or "") or ""

def _find_contact(login: str) -> Optional[Dict[str, str]]:
    for c in contacts:
        if c.get("login") == login:
            return c
    return None

# ====== ROTAS (PÁGINAS) ======

@app.get("/")
def index():
    # Página inicial simples: envia para /motorista (tela principal)
    return redirect(url_for("motorista"))

@app.get("/motorista")
def motorista():
    return render_template("motorista.html")

@app.get("/cadastro_contatos")
def cadastro_contatos():
    return render_template("cadastro_contatos.html")

@app.get("/login")
def login():
    return render_template("login.html")

@app.get("/painel")
def painel():
    return render_template("painel.html")

@app.get("/relatorio")
def relatorio():
    return render_template("relatorio.html")

@app.get("/termos")
def termos():
    return render_template("termos.html", terms_version=TERMS_VERSION)

# ====== API ======

@app.get("/api/status")
def api_status():
    return jsonify({"status": "ok", "terms_version": TERMS_VERSION})

@app.get("/api/alerts")
def api_alerts_list():
    # Retorna os últimos 200 (para não crescer infinito)
    return jsonify({"alerts": alerts[-200:]})

@app.post("/api/alert")
def api_alert_create():
    """
    Espera JSON:
    {
      "driver_name": "...",
      "occurrence": "...",
      "lat": -15.7,
      "lng": -47.9,
      "accuracy": 25,
      "address": "opcional",
      "timestamp": "opcional"
    }
    """
    data = request.get_json(silent=True) or {}
    alert = {
        "driver_name": (data.get("driver_name") or "").strip(),
        "occurrence": (data.get("occurrence") or "").strip(),
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "accuracy": data.get("accuracy"),
        "address": (data.get("address") or "").strip(),
        "created_at": _utc_iso(),
        "ip": _client_ip(),
    }
    alerts.append(alert)
    return jsonify({"status": "ok", "alert": alert})

@app.get("/api/contacts")
def api_contacts_list():
    # Nunca devolva senha
    safe = [{"name": c.get("name",""), "login": c.get("login","")} for c in contacts]
    return jsonify({"contacts": safe})

@app.post("/api/contacts")
def api_contacts_create():
    """
    Cadastro de pessoa de confiança.
    Espera JSON: {"name":"...", "login":"...", "password":"..."}
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    login_ = (data.get("login") or "").strip()
    password = (data.get("password") or "").strip()

    if not name or not login_ or not password:
        return jsonify({"status": "error", "message": "Preencha nome, login e senha."}), 400

    if _find_contact(login_):
        return jsonify({"status": "error", "message": "Este login já existe."}), 400

    contacts.append({"name": name, "login": login_, "password": password})
    return jsonify({"status": "ok"})

@app.post("/api/login")
def api_login():
    """
    Login da pessoa de confiança.
    Espera JSON: {"login":"...", "password":"..."}
    """
    data = request.get_json(silent=True) or {}
    login_ = (data.get("login") or "").strip()
    password = (data.get("password") or "").strip()

    c = _find_contact(login_)
    if not c or c.get("password") != password:
        return jsonify({"status": "error", "message": "Login ou senha inválidos."}), 401

    return jsonify({"status": "ok", "name": c.get("name", ""), "login": c.get("login", "")})

@app.post("/api/consent")
def api_consent():
    """
    Registra aceite LGPD/Termos.
    Opcional JSON: {"terms_version":"v1"} - se não vier, usa TERMS_VERSION.
    """
    data = request.get_json(silent=True) or {}
    tv = (data.get("terms_version") or TERMS_VERSION).strip() or TERMS_VERSION

    ip = _client_ip()
    ts = _utc_iso()

    # Log simples (arquivo no container). Em produção, use DB se quiser persistir.
    try:
        with open("consent_log.txt", "a", encoding="utf-8") as f:
            f.write(f"{ts} | IP: {ip} | Termo {tv}\n")
    except Exception:
        # Não derruba o app se o FS estiver readonly
        pass

    return jsonify({"status": "ok", "terms_version": tv})

# ====== EXECUÇÃO LOCAL ======

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
