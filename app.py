import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

from werkzeug.security import generate_password_hash, check_password_hash

APP_NAME = "Driver Shield 360"
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trusted_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            login TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def list_contacts():
    conn = get_db()
    rows = conn.execute("SELECT id, name, login, created_at FROM trusted_contacts ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def count_contacts():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM trusted_contacts").fetchone()["n"]
    conn.close()
    return int(n)

def add_contact(name: str, login: str, password: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO trusted_contacts (name, login, password_hash, created_at) VALUES (?,?,?,?)",
        (name.strip(), login.strip().lower(), generate_password_hash(password), utcnow_iso())
    )
    conn.commit()
    conn.close()

def clear_contacts():
    conn = get_db()
    conn.execute("DELETE FROM trusted_contacts")
    conn.commit()
    conn.close()

def find_contact_by_login(login: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM trusted_contacts WHERE login = ?", (login.strip().lower(),)).fetchone()
    conn.close()
    return row

def require_auth():
    return bool(session.get("trusted_login"))

app = Flask(__name__)
# IMPORTANT: set SECRET_KEY in Render Environment. Fallback only for local dev.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
init_db()

@app.get("/")
def index():
    return redirect(url_for("motorista"))

@app.get("/motorista")
def motorista():
    # If you named it motorista_pwa.html, change here accordingly.
    return render_template("motorista.html", app_name=APP_NAME)

@app.route("/cadastro_contatos", methods=["GET", "POST"])
def cadastro_contatos():
    msg = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        login = request.form.get("login", "").strip()
        senha = request.form.get("senha", "").strip()

        if not name or not login or not senha:
            msg = "Preencha nome, login e senha."
        elif count_contacts() >= 3:
            msg = "Limite atingido: você só pode cadastrar até 3 pessoas de confiança."
        else:
            try:
                add_contact(name, login, senha)
                msg = "Contato cadastrado com sucesso."
            except sqlite3.IntegrityError:
                msg = "Esse login já existe. Use outro login."

    contacts = list_contacts()
    return render_template("cadastro_contatos.html", contacts=contacts, msg=msg, app_name=APP_NAME)

@app.route("/api/clear_contacts", methods=["POST"])
def api_clear_contacts():
    clear_contacts()
    return jsonify({"ok": True})

@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        login_ = request.form.get("login", "").strip().lower()
        senha = request.form.get("senha", "").strip()

        row = find_contact_by_login(login_)
        if not row or not check_password_hash(row["password_hash"], senha):
            msg = "Login ou senha inválidos."
        else:
            session["trusted_login"] = login_
            return redirect(url_for("painel"))

    return render_template("login.html", msg=msg, app_name=APP_NAME)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/painel")
def painel():
    if not require_auth():
        return redirect(url_for("login"))
    contacts = list_contacts()
    return render_template("painel.html", contacts=contacts, trusted_login=session.get("trusted_login"), app_name=APP_NAME)

@app.get("/termos")
def termos():
    return render_template("termos.html", app_name=APP_NAME)
