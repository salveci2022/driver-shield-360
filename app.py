from flask import Flask, render_template

app = Flask(__name__, static_folder="static", template_folder="templates")

# Painel ÚNICO do Motorista (sem login, sem redirects, sem sessão)
@app.route("/")
def motorista():
    return render_template("motorista.html")

# compat: /motorista renderiza o mesmo painel
@app.route("/motorista")
def motorista_alt():
    return render_template("motorista.html")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
