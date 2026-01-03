from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

# ✅ IMPORTANTE (Render): defina SECRET_KEY nas variáveis de ambiente.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# MVP (em memória). Se você reiniciar o serviço, os cadastros somem.
alerts: List[Dict[str, Any]] = []
contacts: List[Dict[str, Any]] = []  # {"name":..., "login":..., "password_hash":..., "created_at":...}

TERMS_VERSION = os.environ.get("TERMS_VERSION", "v1.0")

# ------------------------------------------------------------
# PÁGINAS (GET)
# ------------------------------------------------------------
@app.get("/")
def home():
    return redirect(url_for("motorista"))

@app.get("/motorista")
def motorista():
    return render_template("motorista.html", terms_version=TERMS_VERSION)

@app.get("/termos")
def termos():
    return render_template("termos.html", terms_version=TERMS_VERSION)

@app.get("/cadastro_contatos")
def cadastro_contatos():
    return render_template("cadastro_contatos.html", terms_version=TERMS_VERSION)

@app.get("/login")
def login_page():
    return render_template("login.html", terms_version=TERMS_VERSION)

@app.get("/painel")
def painel():
    # Painel simples protegido por sessão
    if not session.get("trusted_ok"):
        return redirect(url_for("login_page"))
    return render_template("painel.html", terms_version=TERMS_VERSION)

@app.get("/relatorio")
def relatorio():
    return render_template("relatorio.html", terms_version=TERMS_VERSION)

# ------------------------------------------------------------
# FORMULÁRIOS (POST) - evita "Method Not Allowed"
# ------------------------------------------------------------
@app.post("/cadastro_contatos")
def cadastro_contatos_post():
    name = (request.form.get("name") or "").strip()
    login = (request.form.get("login") or "").strip().lower()
    password = (request.form.get("password") or "").strip()

    if not name or not login or not password:
        return render_template(
            "cadastro_contatos.html",
            error="Preencha nome, login e senha.",
            terms_version=TERMS_VERSION,
        ), 400

    # evita duplicado
    if any(c.get("login") == login for c in contacts):
        return render_template(
            "cadastro_contatos.html",
            error="Esse login já existe. Use outro.",
            terms_version=TERMS_VERSION,
        ), 400

    contacts.append(
        {
            "name": name,
            "login": login,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return redirect(url_for("login_page"))

@app.post("/login")
def login_post():
    login = (request.form.get("login") or "").strip().lower()
    password = (request.form.get("password") or "").strip()

    user = next((c for c in contacts if c.get("login") == login), None)
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return render_template(
            "login.html",
            error="Login ou senha inválidos.",
            terms_version=TERMS_VERSION,
        ), 401

    session["trusted_ok"] = True
    session["trusted_login"] = login
    return redirect(url_for("painel"))

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ------------------------------------------------------------
# APIs (JSON) - para o sistema
# ------------------------------------------------------------
@app.get("/api/contacts")
def api_contacts():
    # NÃO expose senha/hash; só lista nomes/logins
    safe = [{"name": c["name"], "login": c["login"]} for c in contacts]
    return jsonify({"ok": True, "contacts": safe})

@app.post("/api/panic")
def api_panic():
    data = request.get_json(silent=True) or {}
    now = datetime.now(timezone.utc).isoformat()

    event = {
        "id": f"ALERT-{len(alerts)+1:05d}",
        "type": (data.get("type") or "Risco").strip(),
        "message": (data.get("message") or "").strip(),
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "accuracy": data.get("accuracy"),
        "created_at": now,
        "terms_version": TERMS_VERSION,
    }
    alerts.append(event)
    return jsonify({"ok": True, "event": event})

@app.get("/api/alerts")
def api_alerts():
    return jsonify({"ok": True, "alerts": alerts[-200:]})

@app.post("/api/consent")
def api_consent():
    # Registro mínimo de aceite (para sua proteção jurídica)
    payload = request.get_json(silent=True) or {}
    consent = {
        "accepted": bool(payload.get("accepted", True)),
        "terms_version": payload.get("terms_version") or TERMS_VERSION,
        "ua": request.headers.get("User-Agent", ""),
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    # Por padrão, só registra no log do Render.
    app.logger.info("CONSENT_LOG %s", consent)
    return jsonify({"ok": True})

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Local
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
