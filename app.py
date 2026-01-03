from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

@app.get("/")
def index():
    return "Driver Shield 360 — online ✅"

@app.get("/motorista")
def motorista():
    return render_template("motorista.html")

@app.get("/termos")
def termos():
    return render_template("termos.html")

# Rotas placeholder (se você já tem telas, pode trocar depois)
@app.get("/cadastro")
def cadastro():
    return "<h1>Cadastro</h1><p>Configure sua tela /cadastro aqui.</p>"

@app.get("/relatorio")
def relatorio():
    return "<h1>Relatório</h1><p>Configure sua tela /relatorio aqui.</p>"

# Endpoint do alerta (você pode integrar com WhatsApp/Telegram depois)
@app.post("/api/alerta")
def api_alerta():
    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    ocorrencia = (data.get("ocorrencia") or "").strip()
    if not nome or not ocorrencia:
        return jsonify(ok=False, error="Dados inválidos"), 400

    # Aqui é onde você integra com seu envio real (WhatsApp/Telegram/Email)
    # Por enquanto, só confirma "ok" para não quebrar a tela.
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)