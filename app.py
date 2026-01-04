
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
    # Painel do motorista
    contatos = _carregar_json(CONTATOS_PATH) or []
    contatos_view = [{"nome": c.get("nome",""), "email": c.get("email","")} for c in contatos]
    return render_template("motorista.html", contatos=contatos_view, max_contatos=3)

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
    # Cadastro (máx. 3 pessoas de confiança)
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONTATOS_PATH):
        _salvar_json(CONTATOS_PATH, [])

    contatos = _carregar_json(CONTATOS_PATH) or []
    error = None
    success = None

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        senha = (request.form.get("senha") or "").strip()

        if not nome or not email:
            error = "Preencha nome e e-mail."
        elif len(contatos) >= 3:
            error = "Você já cadastrou as 3 pessoas de confiança permitidas."
        elif any((c.get("email","").lower() == email) for c in contatos):
            error = "Este e-mail já está cadastrado."
        else:
            contatos.append({"nome": nome, "email": email, "senha": senha})
            _salvar_json(CONTATOS_PATH, contatos)
            success = "Pessoa de confiança cadastrada com sucesso!"

    return render_template(
        "cadastro_contatos.html",
        contatos=contatos,
        error=error,
        success=success,
        max_contatos=3,
    )

@app.route("/cadastro")
def cadastro_redirect():
    # Tela de cadastro de pessoas de confiança (sem redirecionar)
    return render_template("cadastro_contatos.html", max_contatos=3)

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
    # Sem senha: abre direto o painel da pessoa de confiança
    return render_template("painel.html")

@app.route("/logout")
def logout():
    # "Sair" do painel da pessoa de confiança: não redireciona para outros painéis
    return render_template("painel_saida.html")

@app.route("/painel")
def painel():
    # Painel da pessoa de confiança (sempre aberto, sem autenticação)
    return render_template("painel.html")

@app.route("/relatorio")
def relatorio():
    alertas = _carregar_json(ALERTAS_PATH, [])
    return render_template("relatorio.html", alertas=alertas)

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
