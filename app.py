from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime

app = Flask(__name__)
app.secret_key = "troque-esta-chave-no-servidor"  # chave simples para sessão (MVP)

# Bancos em memória (apenas para demonstração)
alerts = []
contacts = []  # cada contato: {"name": ..., "login": ..., "password": ...}


@app.route("/")
def home():
    return "Driver Shield 360 – versão 4 rodando ✅"


# ====== MOTORISTA ======

@app.route("/motorista")
def motorista():
    return render_template("motorista.html")


# ====== CADASTRO DE PESSOAS DE CONFIANÇA ======

@app.route("/cadastro_contatos", methods=["GET", "POST"])
def cadastro_contatos():
    global contacts
    msg = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        login = request.form.get("login", "").strip()
        password = request.form.get("password", "").strip()
        if name and login and password:
            contacts.append({"name": name, "login": login, "password": password})
            msg = "Contato cadastrado com sucesso."
        else:
            msg = "Preencha todos os campos."
    return render_template("cadastro_contatos.html", contacts=contacts, msg=msg)


@app.route("/api/contacts", methods=["GET"])
def api_contacts():
    # Não expõe senha
    public_contacts = [{"name": c["name"], "login": c["login"]} for c in contacts]
    return jsonify(public_contacts)




@app.route("/api/clear_contacts", methods=["POST"])
def api_clear_contacts():
    """Limpa TODAS as pessoas de confiança cadastradas."""
    contacts.clear()
    return jsonify({"status": "ok", "message": "Contatos apagados."})

# ====== LOGIN DAS PESSOAS DE CONFIANÇA E PAINEL ======

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_input = request.form.get("login", "").strip()
        password_input = request.form.get("password", "").strip()

        for c in contacts:
            if c["login"] == login_input and c["password"] == password_input:
                session["contact_name"] = c["name"]
                session["contact_login"] = c["login"]
                return redirect(url_for("painel"))

        return render_template("login.html", error="Login ou senha inválidos.", last_login=login_input)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/painel")
def painel():
    if "contact_name" not in session:
        return redirect(url_for("login"))
    return render_template("painel.html", contact_name=session.get("contact_name"))


# ====== API DE ALERTAS ======

@app.route("/api/panic", methods=["POST"])
def panic():
    data = request.get_json() or {}

    driver_name = data.get("driver_name", "Motorista")
    lat = data.get("lat")
    lng = data.get("lng")
    occurrence = data.get("occurrence", "Ocorrência não informada")

    # As pessoas de confiança vêm do cadastro global
    contact_names = [c["name"] for c in contacts][:3]

    alert = {
        "driver_name": driver_name,
        "lat": lat,
        "lng": lng,
        "occurrence": occurrence,
        "contacts": contact_names,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    alerts.append(alert)
    print("🔴 ALERTA RECEBIDO:", alert)

    return jsonify({"status": "ok", "message": "Alerta registrado com sucesso."})


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(alerts)


@app.route("/api/clear_alerts", methods=["POST"])
def clear_alerts():
    alerts.clear()
    return jsonify({"status": "ok", "message": "Alertas limpos."})


# ====== RELATÓRIO DE OCORRÊNCIAS ======

@app.route("/relatorio")
def relatorio():
    """Página simples para listar as ocorrências registradas."""
    return render_template("relatorio.html", alerts=alerts)



if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
