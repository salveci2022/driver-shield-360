
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR,'templates'), static_folder=os.path.join(BASE_DIR,'static'))

# =========================
# Arquivos de dados (contatos)
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONTATOS_FILE = os.path.join(DATA_DIR, "contatos.json")

def _garantir_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONTATOS_FILE):
        with open(CONTATOS_FILE, "w", encoding="utf-8") as f:
            f.write("[]")

def carregar_contatos():
    """Retorna lista de pessoas de confiança cadastradas."""
    _garantir_data_dir()
    try:
        with open(CONTATOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []
app.secret_key = os.environ.get("SECRET_KEY", "driver_shield_360_super_seguro")

# Produção (blindado)
app.config['ENV'] = 'production'
app.config['DEBUG'] = False
app.config['TESTING'] = False
app.config['PROPAGATE_EXCEPTIONS'] = False

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
    contatos = carregar_contatos()
    return render_template("motorista.html", contatos=contatos)

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

@app.route("/cadastro", methods=["GET", "POST"])
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
    # Painel da pessoa de confiança sem senha (sempre aberto)
    return redirect(url_for("painel"))


@app.route("/logout")
def logout():
    # Não usamos login; "sair" leva para uma tela isolada
    return redirect(url_for("painel_saida"))


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



@app.route("/painel_saida")
def painel_saida():
    return render_template("painel_saida.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


# ---- Erros amigáveis (sem stacktrace no navegador) ----
@app.errorhandler(404)
def not_found(e):
    try:
        return render_template("404.html"), 404
    except Exception:
        return "Página não encontrada.", 404

@app.errorhandler(500)
def server_error(e):
    try:
        return render_template("500.html"), 500
    except Exception:
        return "Erro interno no servidor.", 500
