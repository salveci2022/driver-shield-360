"""Testes da FASE 1 — segurança crítica, mais regressão das funcionalidades principais."""
import json, os, subprocess, sys

import pytest

from conftest import ROOT, TEST_SECRETS, CONTATOS_INICIAIS, ALERTAS_INICIAIS, fazer_login
import app as app_module

API = TEST_SECRETS["API_SECRET"]


def j(client, url, body=None, csrf=None, headers=None):
    h = {"Content-Type": "application/json"}
    if csrf:
        h["X-CSRF-Token"] = csrf
    h.update(headers or {})
    return client.post(url, data=json.dumps(body or {}), headers=h)


# ── 1. /api/token e secrets ──────────────────────────────────────────

def test_api_token_removida(client, motorista):
    assert client.get("/api/token").status_code == 404


def test_secret_nunca_vai_ao_navegador(motorista):
    for url in ("/motorista", "/cadastro", "/historico", "/checklist", "/sw.js", "/", "/ia",
                "/static/js/motorista.js", "/static/js/painel.js", "/static/js/ia.js"):
        corpo = motorista.get(url).get_data(as_text=True)
        assert API not in corpo
        assert "ds360-api-2026" not in corpo
        assert "X-Shield-Token" not in corpo


def _importar_app_com_env(env_extra, remover=()):
    env = {k: v for k, v in os.environ.items() if k not in remover and k != "DS360_DEV"}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("ausente", ["SECRET_KEY", "PAINEL_SENHA", "MOTORISTA_SENHA", "API_SECRET"])
def test_app_nao_inicia_sem_secret(ausente):
    r = _importar_app_com_env({}, remover=(ausente,))
    assert r.returncode != 0
    assert ausente in r.stderr
    for valor in TEST_SECRETS.values():          # mensagem de erro não vaza valores
        assert valor not in r.stderr


@pytest.mark.parametrize("nome,valor", [("PAINEL_SENHA", "shield360"),
                                        ("API_SECRET", "ds360-api-2026"),
                                        ("SECRET_KEY", "driver-shield-360-premium-2026")])
def test_app_rejeita_valores_padrao_antigos(nome, valor):
    r = _importar_app_com_env({nome: valor})
    assert r.returncode != 0
    assert nome in r.stderr


def test_modo_dev_gera_valores_aleatorios():
    r = _importar_app_com_env({"DS360_DEV": "1"}, remover=tuple(TEST_SECRETS))
    assert r.returncode == 0, r.stderr


# ── 2. Autenticação/autorização ──────────────────────────────────────

@pytest.mark.parametrize("url", ["/api/alerts", "/api/contacts", "/api/viagem/status", "/api/csrf"])
def test_get_api_exige_login(client, url):
    r = client.get(url)
    assert r.status_code == 401
    assert "Ana Teste" not in r.get_data(as_text=True)


@pytest.mark.parametrize("url", ["/api/resolve", "/api/clear_alerts", "/api/panic",
                                 "/api/viagem/iniciar", "/api/viagem/confirmar",
                                 "/api/viagem/cancelar", "/api/passageiro_suspeito"])
def test_post_api_exige_login(client, url):
    # FASE 2: sem token CSRF → 403 (CSRF global); com CSRF válido mas sem login → 401
    assert j(client, url, {"idx": 0, "minutos": 30}).status_code == 403
    client.get("/motorista/login")
    with client.session_transaction() as s:
        csrf = s["_csrf"]
    assert j(client, url, {"idx": 0, "minutos": 30}, csrf=csrf).status_code == 401
    assert app_module.load_alerts() == ALERTAS_INICIAIS   # nada apagado/alterado


@pytest.mark.parametrize("url", ["/motorista", "/cadastro", "/historico"])
def test_paginas_do_motorista_exigem_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/motorista/login" in r.headers["Location"]


@pytest.mark.parametrize("url", ["/painel", "/relatorio", "/relatorio.pdf"])
def test_paginas_do_painel_exigem_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/painel/login" in r.headers["Location"]


def test_cadastro_nao_altera_sem_login(client):
    client.post("/cadastro", data={"acao": "add", "nome": "Invasor", "telefone": "11999999999"})
    client.post("/cadastro", data={"acao": "delete", "idx": "0"})
    assert app_module.load_contacts() == CONTATOS_INICIAIS


def test_motorista_nao_acessa_apis_do_painel(motorista):
    assert motorista.get("/api/alerts").status_code == 401
    assert j(motorista, "/api/clear_alerts", csrf=motorista.csrf).status_code == 401
    assert j(motorista, "/api/resolve", {"idx": 0}, csrf=motorista.csrf).status_code == 401
    assert motorista.get("/painel").status_code == 302


def test_painel_nao_dispara_sos_nem_altera_contatos(painel):
    assert j(painel, "/api/panic", csrf=painel.csrf).status_code == 401
    painel.post("/cadastro", data={"acao": "delete", "idx": "0", "_csrf": painel.csrf})
    assert app_module.load_contacts() == CONTATOS_INICIAIS


def test_senha_errada_nao_loga(client):
    r = fazer_login(client, "painel", senha="errada")
    assert r.status_code == 401
    assert client.get("/painel").status_code == 302


def test_senha_do_painel_nao_vale_para_motorista(client):
    fazer_login(client, "motorista", senha=TEST_SECRETS["PAINEL_SENHA"])
    assert client.get("/motorista").status_code == 302


def test_token_servidor_continua_funcionando(client):
    r = j(client, "/api/panic", {"motorista": "Integração"}, headers={"X-Shield-Token": API})
    assert r.status_code == 200
    assert j(client, "/api/panic", headers={"X-Shield-Token": "errado"}).status_code in (401, 403)
    assert client.get("/api/alerts", headers={"X-Shield-Token": API}).status_code == 401  # só onde já era aceito


def test_sessao_do_painel_expira(painel):
    # FASE 2: expiração controlada no servidor (seguranca.db), não no cookie
    with painel.session_transaction() as s:
        sid = s["painel_sid"]
    app_module.SEG_DB.expirar_sessao(sid)
    assert painel.get("/api/alerts").status_code == 401


# ── 3. CSRF ──────────────────────────────────────────────────────────

def test_api_post_sem_csrf_rejeitada(motorista):
    r = j(motorista, "/api/panic", {"motorista": "x"})
    assert r.status_code == 403
    assert len(app_module.load_alerts()) == len(ALERTAS_INICIAIS)
    r = motorista.post("/api/panic", data="{}", content_type="text/plain")   # CSRF clássico cross-site
    assert r.status_code == 403


def test_clear_alerts_sem_csrf_rejeitado(painel):
    assert painel.post("/api/clear_alerts").status_code == 403
    assert app_module.load_alerts() == ALERTAS_INICIAIS


def test_cadastro_sem_csrf_rejeitado(motorista):
    motorista.post("/cadastro", data={"acao": "add", "nome": "X", "telefone": "11988887777"})
    motorista.post("/cadastro", data={"acao": "delete", "idx": "0"})
    assert app_module.load_contacts() == CONTATOS_INICIAIS


def test_login_sem_csrf_rejeitado(client):
    client.get("/painel/login")
    r = client.post("/painel/login", data={"senha": TEST_SECRETS["PAINEL_SENHA"]})
    assert r.status_code == 302 and "/painel/login" in r.headers["Location"]   # FASE 2: CSRF global
    assert client.get("/painel").status_code == 302


# ── 4. Brute force / sessão / cookies ────────────────────────────────

def test_bloqueio_apos_falhas_de_login(client):
    for _ in range(app_module.LOGIN_MAX_FALHAS):
        assert fazer_login(client, "painel", senha="errada").status_code == 401
    r = fazer_login(client, "painel")   # senha correta, mas IP bloqueado
    assert r.status_code == 429
    assert client.get("/painel").status_code == 302


def test_bloqueio_por_ip(client, app):
    for _ in range(app_module.LOGIN_MAX_FALHAS):
        fazer_login(client, "motorista", senha="errada")
    outro = app.test_client()
    outro.environ_base.update({"HTTP_X_FORWARDED_PROTO": "https", "REMOTE_ADDR": "10.0.0.99"})
    assert fazer_login(outro, "motorista").status_code == 302


def test_login_renova_sessao(client):
    client.get("/painel/login")
    with client.session_transaction() as s:
        s["valor_plantado"] = "atacante"
        csrf_antes = s["_csrf"]
    fazer_login(client, "painel")
    with client.session_transaction() as s:
        assert "valor_plantado" not in s
        assert s.get("_csrf") != csrf_antes


def test_login_mantem_outro_papel(client):
    fazer_login(client, "motorista")
    fazer_login(client, "painel")
    assert client.get("/motorista").status_code == 200
    assert client.get("/painel").status_code == 200


def test_open_redirect_bloqueado(client):
    client.get("/motorista/login")
    with client.session_transaction() as s:
        csrf = s["_csrf"]
    r = client.post("/motorista/login?next=//evil.example.com",
                    data={"senha": TEST_SECRETS["MOTORISTA_SENHA"], "_csrf": csrf})
    assert r.status_code == 302 and r.headers["Location"] == "/motorista"


def test_flags_do_cookie(client):
    r = fazer_login(client, "motorista")
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert "Expires=" in cookie   # sessão persistente (30 dias)


def test_logout_motorista(motorista):
    # FASE 2: logout só por POST + CSRF
    motorista.post("/motorista/logout", data={"_csrf": motorista.csrf})
    assert motorista.get("/motorista").status_code == 302


def test_headers_de_seguranca(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" in r.headers
    assert "geolocation=(self)" in r.headers["Permissions-Policy"]


def test_dados_pessoais_sem_cache(motorista):
    assert motorista.get("/api/contacts").headers["Cache-Control"] == "no-store"
    assert motorista.get("/cadastro").headers["Cache-Control"] == "no-store"


# ── 5. XSS ───────────────────────────────────────────────────────────

PAYLOAD = '<img src=x onerror=alert(1)>'


def test_cadastro_nome_nao_injeta_js(motorista):
    nome = "');alert(document.cookie);//"
    motorista.post("/cadastro", data={"acao": "add", "nome": nome, "telefone": "11977776666",
                                      "_csrf": motorista.csrf})
    html = motorista.get("/cadastro").get_data(as_text=True)
    assert "confirm('Remover ');alert" not in html
    assert "confirm('Remover &#39;);alert" not in html
    assert 'data-nome="&#39;);alert(document.cookie);//"' in html


def test_cadastro_escapa_html(motorista):
    motorista.post("/cadastro", data={"acao": "add", "nome": PAYLOAD, "telefone": "11977775555",
                                      "_csrf": motorista.csrf})
    assert PAYLOAD not in motorista.get("/cadastro").get_data(as_text=True)


def test_painel_escapa_dados_dos_alertas():
    tpl = open(os.path.join(ROOT, "static", "js", "painel.js"), encoding="utf-8").read()   # FASE 2: JS externo (CSP)
    for campo in ("a.ocorrencia", "a.motorista", "a.ts"):
        assert "${" + campo + "}" not in tpl
        assert "esc(" + campo + ")" in tpl
    assert "function esc(" in tpl


def test_relatorio_e_historico_escapam(client):
    fazer_login(client, "motorista")
    fazer_login(client, "painel")
    client.get("/motorista")
    with client.session_transaction() as s:
        csrf = s["_csrf"]
    j(client, "/api/panic", {"motorista": PAYLOAD, "ocorrencia": PAYLOAD}, csrf=csrf)
    j(client, "/api/viagem/iniciar", {"motorista": PAYLOAD, "minutos": 30}, csrf=csrf)
    assert PAYLOAD not in client.get("/relatorio").get_data(as_text=True)
    assert PAYLOAD not in client.get("/historico").get_data(as_text=True)


def test_coordenadas_invalidas_descartadas(motorista):
    j(motorista, "/api/panic", {"lat": "javascript:alert(1)", "lng": "999"}, csrf=motorista.csrf)
    ultimo = app_module.load_alerts()[-1]
    assert ultimo["lat"] is None and ultimo["lng"] is None
    j(motorista, "/api/panic", {"lat": "-15.7801", "lng": "-47.9292"}, csrf=motorista.csrf)
    ultimo = app_module.load_alerts()[-1]
    assert ultimo["lat"] == "-15.780100" and ultimo["lng"] == "-47.929200"


def test_campos_com_limite_de_tamanho(motorista):
    j(motorista, "/api/panic", {"motorista": "A" * 5000, "ocorrencia": "B" * 5000}, csrf=motorista.csrf)
    ultimo = app_module.load_alerts()[-1]
    assert len(ultimo["motorista"]) == 60 and len(ultimo["ocorrencia"]) == 120


def test_entradas_invalidas_nao_geram_500(painel, motorista):
    assert j(painel, "/api/resolve", {"idx": "abc"}, csrf=painel.csrf).status_code == 400
    assert j(motorista, "/api/viagem/iniciar", {"minutos": "abc"}, csrf=motorista.csrf).status_code == 400
    assert j(motorista, "/api/panic", {"motorista": {"x": 1}}, csrf=motorista.csrf).status_code == 200


# ── 6. Logs ──────────────────────────────────────────────────────────

def test_logs_nao_vazam_senha_nem_telefone(client, capsys):
    fazer_login(client, "painel", senha="SenhaTentada123")
    app_module.enviar_whatsapp("61999990001", "msg")
    out = capsys.readouterr().out
    assert "SenhaTentada123" not in out
    assert "61999990001" not in out
    for valor in TEST_SECRETS.values():
        assert valor not in out


def test_mascara_telefone():
    assert app_module.mascarar_telefone("61999990001") == "61*****0001"
    assert app_module.mascarar_telefone("") == "***"


# ── 7. PWA ───────────────────────────────────────────────────────────

def test_service_worker_network_first(client):
    sw = client.get("/sw.js").get_data(as_text=True)
    assert "shield360-v5" in sw   # FASE 3: versão nova (CSS/JS externos)
    assert "startsWith('/api/')" in sw
    assert "!resp.redirected" in sw
    assert client.get("/manifest.json").status_code == 200


# ── 8. Regressão: fluxos principais continuam funcionando ────────────

def test_fluxo_motorista_completo(motorista):
    # SOS com GPS
    r = j(motorista, "/api/panic", {"motorista": "Carlos", "ocorrencia": "Assalto",
                                    "lat": "-15.78", "lng": "-47.93"}, csrf=motorista.csrf)
    assert r.status_code == 200 and r.get_json()["ok"]
    assert app_module.load_alerts()[-1]["motorista"] == "Carlos"
    # contatos
    assert motorista.get("/api/contacts").get_json() == CONTATOS_INICIAIS
    motorista.post("/cadastro", data={"acao": "add", "nome": "Bia", "telefone": "61988887777",
                                      "relacao": "Amigo(a)", "_csrf": motorista.csrf})
    assert len(app_module.load_contacts()) == 2
    motorista.post("/cadastro", data={"acao": "delete", "idx": "1", "_csrf": motorista.csrf})
    assert app_module.load_contacts() == CONTATOS_INICIAIS
    # Dead Man's Switch
    r = j(motorista, "/api/viagem/iniciar", {"motorista": "Carlos", "minutos": 30}, csrf=motorista.csrf)
    assert r.get_json()["segundos"] == 1800
    st = motorista.get("/api/viagem/status").get_json()
    assert st["ativa"] and st["motorista"] == "Carlos"
    assert j(motorista, "/api/viagem/confirmar", csrf=motorista.csrf).get_json()["ok"]
    assert motorista.get("/api/viagem/status").get_json() == {"ativa": False}
    assert app_module.load_historico()[-1]["resultado"] == "chegou_segura"
    j(motorista, "/api/viagem/iniciar", {"minutos": 10}, csrf=motorista.csrf)
    j(motorista, "/api/viagem/cancelar", csrf=motorista.csrf)
    assert app_module.load_historico()[-1]["resultado"] == "cancelada"
    # passageiro suspeito
    assert j(motorista, "/api/passageiro_suspeito", {"descricao": "agressivo"},
             csrf=motorista.csrf).status_code == 200
    # páginas
    for url in ("/motorista", "/cadastro", "/historico", "/checklist", "/ia", "/"):
        assert motorista.get(url).status_code == 200, url


def test_dms_dispara_alerta_automatico(motorista):
    j(motorista, "/api/viagem/iniciar", {"motorista": "Dani", "minutos": 5}, csrf=motorista.csrf)
    assert app_module._disparar_dms() is False          # FASE 2: não dispara antes de expirar
    v = app_module.load_viagem(); v["expira_em"] = app_module.now_ts() - 1; app_module.save_viagem(v)
    assert app_module._disparar_dms(v["id"]) is True
    ultimo = app_module.load_alerts()[-1]
    assert ultimo["tipo"] == "dead_mans_switch" and ultimo["motorista"] == "Dani"


def test_fluxo_painel_completo(painel):
    # FASE 3: cada alerta traz seu id (o painel resolve por id, não por posição)
    assert painel.get("/api/alerts").get_json() == [dict(a, id=i) for i, a in enumerate(ALERTAS_INICIAIS)]
    assert painel.get("/api/viagem/status").status_code == 200
    assert j(painel, "/api/resolve", {"idx": 0}, csrf=painel.csrf).status_code == 200
    assert app_module.load_alerts()[0]["status"] == "Resolvido"
    assert painel.get("/relatorio").status_code == 200
    pdf = painel.get("/relatorio.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    assert painel.get("/historico").status_code == 200
    assert j(painel, "/api/clear_alerts", csrf=painel.csrf).status_code == 200
    assert app_module.load_alerts() == []
    backups = [f for f in os.listdir(app_module.DATA_DIR) if f.startswith("alerts_backup_")]
    assert len(backups) == 1   # limpar não perde dados


def test_csrf_endpoint_para_pagina_em_cache(motorista):
    r = motorista.get("/api/csrf")
    assert r.status_code == 200 and r.get_json()["csrf"] == motorista.csrf
