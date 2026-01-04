
import os
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, session, flash
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
ALERTAS_PATH = os.path.join(DATA_DIR, "alertas.json")
CONTATOS_PATH = os.path.join(DATA_DIR, "contatos.json")

os.makedirs(DATA_DIR, exist_ok=True)

def _carregar_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _salvar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# garante arquivos base
if not os.path.exists(ALERTAS_PATH):
    _salvar_json(ALERTAS_PATH, [])
if not os.path.exists(CONTATOS_PATH):
    _salvar_json(CONTATOS_PATH, [])

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "driver_shield_360_super_seguro")

# ---------- Funções auxiliares ----------

def _agora_brasilia():
    """Retorna datetime com timezone America/Sao_Paulo."""
    return datetime.now(ZoneInfo("America/Sao_Paulo"))

def _criar_alerta(motorista, ocorrencia, latitude=None, longitude=None):
    alertas = _carregar_json(ALERTAS_PATH, [])
    novo_id = (alertas[-1]["id"] + 1) if alertas else 1

    dt_utc = datetime.now(timezone.utc)
    dt_brt = _agora_brasilia()

    alerta = {
        "id": novo_id,
        "timestamp_utc": dt_utc.isoformat(),
        "timestamp_brt": dt_brt.strftime("%Y-%m-%d %H:%M:%S"),
        "motorista": motorista or "Motorista",
        "ocorrencia": ocorrencia or "Ocorrência não informada",
        "latitude": latitude,
        "longitude": longitude,
    }
    alertas.append(alerta)
    _salvar_json(ALERTAS_PATH, alertas)
    return alerta

def _usuario_atual():
    email = session.get("usuario_email")
    if not email:
        return None
    contatos = _carregar_json(CONTATOS_PATH, [])
    for c in contatos:
        if c.get("email") == email:
            return c
    return None

# ---------- Rotas ----------

@app.route("/")
def index():
    """
    Página inicial simples com links rápidos.
    """
    return render_template("index.html")

@app.route("/motorista", methods=["GET"])
def motorista():
    return render_template("motorista.html")

@app.route("/api/panico", methods=["POST"])
def api_panico():
    data = request.get_json(silent=True) or request.form

    motorista_nome = data.get("motorista") or data.get("nome_motorista") or "Motorista"
    ocorrencia = data.get("ocorrencia") or "Ocorrência não informada"
    latitude = data.get("latitude") or data.get("lat") or None
    longitude = data.get("longitude") or data.get("lng") or data.get("lon") or None

    alerta = _criar_alerta(motorista_nome, ocorrencia, latitude, longitude)
    return jsonify({"status": "ok", "alerta": alerta})

@app.route("/api/alertas")
def api_alertas():
    alertas = _carregar_json(ALERTAS_PATH, [])
    return jsonify(alertas)

@app.route("/api/limpar_alertas", methods=["POST"])
def api_limpar_alertas():
    _salvar_json(ALERTAS_PATH, [])
    return jsonify({"status": "ok"})

# ----- Cadastro de pessoas de confiança -----

@app.route("/cadastro_contatos", methods=["GET", "POST"])
def cadastro_contatos():
    contatos = _carregar_json(CONTATOS_PATH, [])
    if request.method == "POST":
        nome = request.form.get("nome")
        email = request.form.get("email")
        senha = request.form.get("senha")

        if not nome or not email or not senha:
            flash("Preencha todos os campos.", "erro")
            return redirect(url_for("cadastro_contatos"))

        # evita duplicados simples
        for c in contatos:
            if c["email"] == email:
                flash("Já existe um cadastro com esse e-mail.", "erro")
                break
        else:
            contatos.append({"nome": nome, "email": email, "senha": senha})
            _salvar_json(CONTATOS_PATH, contatos)
            flash("Pessoa de confiança cadastrada com sucesso!", "sucesso")
        return redirect(url_for("cadastro_contatos"))

    return render_template("cadastro_contatos.html", contatos=contatos)

# rota atalho /cadastro -> /cadastro_contatos
@app.route("/cadastro")
def cadastro_redirect():
    return redirect(url_for("cadastro_contatos"))

@app.route("/apagar_contato/<email>", methods=["POST"])
def apagar_contato(email):
    contatos = _carregar_json(CONTATOS_PATH, [])
    novos = [c for c in contatos if c.get("email") != email]
    _salvar_json(CONTATOS_PATH, novos)
    flash("Contato removido.", "sucesso")
    return redirect(url_for("cadastro_contatos"))

# ----- Login & painel -----

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")
        contatos = _carregar_json(CONTATOS_PATH, [])

        for c in contatos:
            if c.get("email") == email and c.get("senha") == senha:
                session["usuario_email"] = email
                session["usuario_nome"] = c.get("nome")
                flash("Login realizado com sucesso!", "sucesso")
                return redirect(url_for("painel"))

        flash("E-mail ou senha inválidos.", "erro")
        return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/painel")
def painel():
    user = _usuario_atual()
    if not user:
        return redirect(url_for("login"))

    alertas = _carregar_json(ALERTAS_PATH, [])
    # por enquanto, todos os alertas são exibidos
    return render_template("painel.html", usuario=user, alertas=alertas)

# ----- Relatório -----

@app.route("/relatorio")
def relatorio():
    alertas = _carregar_json(ALERTAS_PATH, [])
    return render_template("relatorio.html", alertas=alertas)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
