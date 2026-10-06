"""
FASE 4 — validação integral antes do deploy.
  A. Regressão de cada funcionalidade (modo JSON, padrão)
  B. Segurança (SQLi, path traversal, segredos no frontend/logs, admin, headers)
  C. Z-API (formato real da requisição) e Shield AI (SDK real contra servidor stub local)
  D. Dead Man's Switch: só o worker dedicado agenda; vários workers; reinício; idempotência no banco
  E. PostgreSQL: integridade referencial, constraints, índices, migrations
  F. LGPD/Git: nada de dados pessoais versionável
Os testes PostgreSQL exigem DS360_TEST_PG_ADMIN_URL (sem ela são PULADOS, nunca simulados).
"""
import glob, json, os, re, shutil, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import ROOT, TEST_SECRETS, CONTATOS_INICIAIS, ALERTAS_INICIAIS, fazer_login
import app as app_module

PG = os.environ.get("DS360_TEST_PG_ADMIN_URL")
requer_pg = pytest.mark.skipif(not PG, reason="DS360_TEST_PG_ADMIN_URL não definida — teste PostgreSQL NÃO EXECUTADO")
if PG:
    import psycopg2
    from psycopg2 import errors as pgerr
    from test_fase3_pg import (pg_url, repo, pg, criar_motorista, criar_painel, novo_cliente, entrar, q,
                               _expirar, SimpleCtx)


def jpost(cliente, url, body=None, csrf=None, headers=None):
    h = {"Content-Type": "application/json"}
    if csrf:
        h["X-CSRF-Token"] = csrf
    h.update(headers or {})
    return cliente.post(url, data=json.dumps(body or {}), headers=h)


# ═════════════════════ A. REGRESSÃO (modo JSON) ═════════════════════

def test_reg_login_motorista_painel_e_logout(client):
    assert fazer_login(client, "motorista").status_code == 302
    assert fazer_login(client, "painel").status_code == 302
    assert client.get("/motorista").status_code == 200 and client.get("/painel").status_code == 200
    with client.session_transaction() as s:
        csrf = s["_csrf"]
    client.post("/painel/logout", data={"_csrf": csrf})
    assert client.get("/painel").status_code == 302 and client.get("/motorista").status_code == 200
    with client.session_transaction() as s:
        csrf = s["_csrf"]
    client.post("/motorista/logout", data={"_csrf": csrf})
    assert client.get("/motorista").status_code == 302


def test_reg_sos_sem_e_com_gps(motorista):
    r1 = jpost(motorista, "/api/panic", {"motorista": "Ana", "ocorrencia": "Assalto"}, motorista.csrf).get_json()
    r2 = jpost(motorista, "/api/panic", {"motorista": "Ana", "ocorrencia": "Assalto",
                                          "lat": -15.7801, "lng": -47.9292}, motorista.csrf).get_json()
    assert r1["ok"] and r1["alerta"]["lat"] is None
    assert r2["ok"] and (r2["alerta"]["lat"], r2["alerta"]["lng"]) == ("-15.780100", "-47.929200")
    assert len(app_module.load_alerts()) == len(ALERTAS_INICIAIS) + 2


def test_reg_viagem_inicio_status_encerramento(motorista):
    r = jpost(motorista, "/api/viagem/iniciar", {"motorista": "Ana", "minutos": 15, "lat": "-15.7", "lng": "-47.9"},
              motorista.csrf).get_json()
    assert r == {"ok": True, "motorista": "Ana", "minutos": 15, "segundos": 900}
    st = motorista.get("/api/viagem/status").get_json()
    assert st["ativa"] and st["motorista"] == "Ana" and 890 <= st["restante"] <= 900
    assert jpost(motorista, "/api/viagem/confirmar", csrf=motorista.csrf).get_json() == {"ok": True}
    assert motorista.get("/api/viagem/status").get_json() == {"ativa": False}
    assert app_module.load_historico()[-1]["resultado"] == "chegou_segura"
    assert "Ana" in motorista.get("/historico").get_data(as_text=True)


def test_reg_passageiro_suspeito(motorista):
    assert jpost(motorista, "/api/passageiro_suspeito", {"descricao": "Homem alto, camisa azul"},
                 motorista.csrf).get_json() == {"ok": True}
    a = app_module.load_alerts()[-1]
    assert a["tipo"] == "passageiro_suspeito" and "camisa azul" in a["ocorrencia"]


def test_reg_contatos_add_remover_limite_duplicado(motorista):
    for i in range(6):
        motorista.post("/cadastro", data={"acao": "add", "nome": f"C{i}", "telefone": f"6198888{i:04d}",
                                          "relacao": "Familiar", "_csrf": motorista.csrf})
    assert len(app_module.load_contacts()) == 5      # limite
    motorista.post("/cadastro", data={"acao": "delete", "idx": "1", "_csrf": motorista.csrf})
    assert len(app_module.load_contacts()) == 4
    motorista.post("/cadastro", data={"acao": "add", "nome": "Dup", "telefone": CONTATOS_INICIAIS[0]["telefone"],
                                      "_csrf": motorista.csrf})
    assert len(app_module.load_contacts()) == 4      # duplicado recusado


def test_reg_alertas_resolucao_e_limpeza(painel):
    alertas = painel.get("/api/alerts").get_json()
    assert alertas[0]["status"] == "Ativo"
    assert jpost(painel, "/api/resolve", {"id": alertas[0]["id"]}, painel.csrf).status_code == 200
    assert painel.get("/api/alerts").get_json()[0]["status"] == "Resolvido"
    assert jpost(painel, "/api/clear_alerts", csrf=painel.csrf).status_code == 200
    assert painel.get("/api/alerts").get_json() == []
    assert glob.glob(os.path.join(app_module.DATA_DIR, "alerts_backup_*.json"))


def test_reg_pdf(painel):
    r = painel.get("/relatorio.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF") and r.mimetype == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"]


def test_reg_checklist(client):
    html = client.get("/checklist").get_data(as_text=True)
    assert html.count('data-acao="toggle"') == 12 and "/static/js/checklist.js" in html
    assert client.get("/static/js/checklist.js").status_code == 200


def test_reg_pwa(client):
    m = client.get("/manifest.json").get_json()
    assert m["start_url"] == "/motorista" and m["display"] == "standalone"
    for icone in m["icons"]:
        assert client.get(icone["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.mimetype and sw.headers["Cache-Control"] == "no-cache"


class _UrlopenFalso:
    def __init__(self):
        self.pedidos = []

    def __call__(self, req, timeout=None):
        self.pedidos.append(req)
        class Resp:
            status = 200
            def read(self_inner):
                return b'{"zaapId":"x","messageId":"y"}'
        return Resp()


@pytest.fixture
def zapi(monkeypatch):
    monkeypatch.setattr(app_module, "ZAPI_INSTANCE", "INST-SECRETA-123")
    monkeypatch.setattr(app_module, "ZAPI_TOKEN", "TOKEN-SECRETO-456")
    monkeypatch.setattr(app_module, "ZAPI_CLIENT_TKN", "CLIENT-SECRETO-789")
    falso = _UrlopenFalso()
    monkeypatch.setattr(app_module.urllib.request, "urlopen", falso)
    return falso


def test_reg_whatsapp_zapi_formato_da_requisicao(motorista, zapi, capsys):
    jpost(motorista, "/api/panic", {"motorista": "Ana", "ocorrencia": "Assalto", "lat": "-15.78", "lng": "-47.92"},
          motorista.csrf)
    assert len(zapi.pedidos) == len(CONTATOS_INICIAIS)
    req = zapi.pedidos[0]
    assert req.full_url == "https://api.z-api.io/instances/INST-SECRETA-123/token/TOKEN-SECRETO-456/send-text"
    assert req.get_method() == "POST" and req.headers["Client-token"] == "CLIENT-SECRETO-789"
    corpo = json.loads(req.data)
    assert corpo["phone"] == "61999990001"
    assert "Assalto" in corpo["message"] and "google.com/maps?q=-15.780000,-47.920000" in corpo["message"]
    log = capsys.readouterr().out
    for segredo in ("INST-SECRETA-123", "TOKEN-SECRETO-456", "CLIENT-SECRETO-789", "61999990001"):
        assert segredo not in log


def test_reg_dms_ponta_a_ponta_com_whatsapp(motorista, zapi):
    jpost(motorista, "/api/viagem/iniciar", {"motorista": "Ana", "minutos": 5}, motorista.csrf)
    v = app_module.load_viagem()
    v["expira_em"] = app_module.now_ts() - 1
    app_module.save_viagem(v)
    assert app_module.tarefa_dms() == 1 and app_module.tarefa_dms() == 0
    assert len(zapi.pedidos) == len(CONTATOS_INICIAIS)
    assert "ALERTA AUTOMÁTICO" in json.loads(zapi.pedidos[0].data)["message"]
    assert app_module.load_alerts()[-1]["tipo"] == "dead_mans_switch"


# ── Shield AI com o SDK REAL contra um servidor stub local (sem chave real) ──
class _StubAnthropic(BaseHTTPRequestHandler):
    recebidos = []

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _StubAnthropic.recebidos.append({"path": self.path, "headers": dict(self.headers), "body": corpo})
        resp = json.dumps({"id": "msg_stub", "type": "message", "role": "assistant", "model": corpo["model"],
                           "content": [{"type": "text", "text": "Acione o SOS e ligue 190."}],
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 12, "output_tokens": 8}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_ia(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _StubAnthropic)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _StubAnthropic.recebidos = []
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-CHAVE-DE-TESTE-NAO-REAL")
    monkeypatch.setattr(app_module, "_ia_cliente", None)
    yield _StubAnthropic.recebidos
    srv.shutdown()


def test_reg_shield_ai_sdk_real_contra_stub(motorista, stub_ia):
    r = jpost(motorista, "/api/ia", {"mensagens": [{"role": "user", "content": "Fui seguido, o que faço?"}]},
              motorista.csrf)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "resposta": "Acione o SOS e ligue 190."}
    pedido = stub_ia[0]
    assert pedido["path"].startswith("/v1/messages")
    h = {k.lower(): v for k, v in pedido["headers"].items()}
    assert h["x-api-key"] == "sk-ant-CHAVE-DE-TESTE-NAO-REAL"
    assert "server-side-fallback-2026-07-01" in h["anthropic-beta"]
    b = pedido["body"]
    assert b["model"] == app_module.IA_MODELO and b["system"] == app_module.IA_SISTEMA
    assert b["fallbacks"] == "default" and b["output_config"] == {"effort": "low"} and b["max_tokens"] == 2048
    assert b["messages"] == [{"role": "user", "content": "Fui seguido, o que faço?"}]
    assert "sk-ant" not in r.get_data(as_text=True)


# ═════════════════════ B. SEGURANÇA ═════════════════════

@pytest.mark.parametrize("caminho", ["/static/../app.py", "/static/%2e%2e/app.py", "/static/..%2fapp.py",
                                     "/static/../data/contacts.json", "/static/%2e%2e%2fdata%2fcontacts.json",
                                     "/data/contacts.json", "/.env", "/seguranca.db"])
def test_path_traversal(client, caminho):
    r = client.get(caminho)
    corpo = r.get_data(as_text=True)
    assert r.status_code in (301, 308, 400, 404) or "SECRET" not in corpo
    assert "61999990001" not in corpo and "_carregar_secrets" not in corpo


def test_codigo_sem_execucao_de_comandos_ou_eval():
    for arq in ("app.py", "repositorio_json.py", "repositorio_pg.py", "seguranca_db.py", "armazenamento.py",
                "agendador.py", "lgpd.py"):
        fonte = open(os.path.join(ROOT, arq), encoding="utf-8").read()
        assert not re.search(r"\b(subprocess|os\.system|os\.popen|eval\(|exec\(|pickle|yaml\.load)", fonte), arq


def test_sql_sempre_parametrizado():
    """Nenhum valor externo é interpolado em SQL: só %s/%(x)s. f-strings e .format() em SQL são proibidos."""
    for arq in ("repositorio_pg.py", "seguranca_db.py", "importar_json.py", "rollback_pg_para_json.py", "migrar.py"):
        fonte = open(os.path.join(ROOT, arq), encoding="utf-8").read()
        assert not re.search(r"execute\(\s*f[\"']", fonte), arq
        assert not re.search(r"execute\([^)]*\.format\(", fonte), arq
        assert not re.search(r"execute\([^)]*%\s*\(", fonte.replace("%(", "")), arq


def test_nenhum_segredo_no_frontend(client, monkeypatch):
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-SEGREDO-FRONT")
    monkeypatch.setattr(app_module, "ZAPI_TOKEN", "ZAPI-SEGREDO-FRONT")
    fazer_login(client, "motorista")
    fazer_login(client, "painel")
    segredos = list(TEST_SECRETS.values()) + ["sk-ant-SEGREDO-FRONT", "ZAPI-SEGREDO-FRONT"]
    paginas = ["/", "/motorista", "/cadastro", "/checklist", "/historico", "/ia", "/painel", "/relatorio",
               "/painel/login", "/motorista/login", "/pessoa_sair", "/manifest.json", "/sw.js",
               "/api/csrf", "/api/alerts", "/api/contacts", "/api/viagem/status", "/api/lgpd/retencao"]
    estaticos = [os.path.relpath(p, ROOT).replace(os.sep, "/")
                 for p in glob.glob(os.path.join(ROOT, "static", "**", "*.*"), recursive=True)
                 if p.endswith((".js", ".css", ".json"))]
    for url in paginas + ["/" + e for e in estaticos]:
        corpo = client.get(url).get_data(as_text=True)
        for s in segredos:
            assert s not in corpo, f"segredo em {url}"


def test_logs_sem_dados_sensiveis_em_fluxo_completo(client, zapi, capsys):
    fazer_login(client, "motorista", senha="SENHA-ERRADA-NO-LOG")
    fazer_login(client, "motorista")
    client.get("/motorista")
    with client.session_transaction() as s:
        csrf, sid = s["_csrf"], s["motorista_sid"]
    jpost(client, "/api/panic", {"motorista": "Fulana de Tal", "ocorrencia": "OCORRENCIA-PRIVADA",
                                 "lat": "-15.78", "lng": "-47.92"}, csrf)
    jpost(client, "/api/viagem/iniciar", {"motorista": "Fulana de Tal", "minutos": 5}, csrf)
    jpost(client, "/api/passageiro_suspeito", {"descricao": "DESCRICAO-PRIVADA"}, csrf)
    log = capsys.readouterr().out + capsys.readouterr().err
    proibidos = list(TEST_SECRETS.values()) + ["SENHA-ERRADA-NO-LOG", sid, csrf, "Fulana", "OCORRENCIA-PRIVADA",
                                               "DESCRICAO-PRIVADA", "61999990001", "-15.78", "TOKEN-SECRETO-456"]
    for p in proibidos:
        assert p not in log, f"dado sensível no log: {p[:6]}…"


def test_nao_ha_rotas_de_admin_na_web():
    assert not [r.rule for r in app_module.app.url_map.iter_rules() if "admin" in r.rule.lower()]


def test_headers_de_seguranca_tambem_em_erros(client):
    r = client.get("/nao-existe")
    assert r.status_code == 404
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Strict-Transport-Security"):
        assert h in r.headers


def test_corpo_grande_rejeitado(client):
    assert client.post("/api/csp-report", data="x" * (70 * 1024)).status_code == 413


# ═════════════════════ C/D. DMS: worker dedicado, reinícios, vários workers ═════════════════════

def test_web_sem_agendador_quando_worker_dedicado(client, monkeypatch):
    chamadas = []
    monkeypatch.setattr(app_module, "AGENDADOR_EMBUTIDO", False)
    monkeypatch.setattr(app_module, "tarefa_dms", lambda: chamadas.append(1))
    monkeypatch.setattr(app_module.AGENDADOR, "garantir_thread", lambda *a, **k: chamadas.append("thread"))
    monkeypatch.setattr(app_module, "_dms_ultima_verificacao", 0.0)
    for url in ("/", "/checklist", "/motorista/login"):
        client.get(url)
    app_module.verificar_dms_ao_iniciar()
    assert chamadas == []


def _env_pg(url, embutido, data_dir):
    env = {k: v for k, v in os.environ.items() if k != "DS360_DEV"}
    env.update({"STORAGE_BACKEND": "postgres", "DATABASE_URL": url, "AGENDADOR_EMBUTIDO": embutido,
                "DS360_DATA_DIR": data_dir, "PAINEL_SENHA": "", "MOTORISTA_SENHA": ""})
    for k in ("ZAPI_INSTANCE", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN"):
        env.pop(k, None)
    return env


def _n_dms(repo, mid):
    return q(repo, "SELECT count(*) AS n FROM alertas WHERE motorista_id = %s AND tipo = 'dead_mans_switch'",
             mid)[0]["n"]


@requer_pg
def test_reinicio_do_web_com_worker_dedicado_nao_dispara(pg, repo, pg_url, tmp_path):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=_env_pg(pg_url, "0", str(tmp_path)),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert _n_dms(repo, m.motorista_id) == 0          # o web (AGENDADOR_EMBUTIDO=0) não agenda nada


@requer_pg
def test_reinicio_nao_gera_disparo_indevido(pg, repo, pg_url, tmp_path):
    ativa = criar_motorista(repo)          # em andamento, não expirada → não dispara
    confirmada = criar_motorista(repo)     # confirmada e "expirada" → não dispara
    expirada = criar_motorista(repo)       # em andamento e expirada → dispara UMA vez
    repo.iniciar_viagem(ativa.motorista_id, "A", 30, None, None)
    repo.iniciar_viagem(confirmada.motorista_id, "C", 5, None, None)
    repo.encerrar_viagem(confirmada.motorista_id, "chegou_segura")
    with repo.tx() as c:
        c.execute("UPDATE viagens SET expira_em = now() - interval '1 hour' WHERE motorista_id = %s",
                  (confirmada.motorista_id,))
    repo.iniciar_viagem(expirada.motorista_id, "E", 5, None, None)
    _expirar(repo, expirada.motorista_id)
    for _ in range(3):   # três reinícios seguidos (modo serviço único)
        r = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=_env_pg(pg_url, "1", str(tmp_path)),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    assert _n_dms(repo, ativa.motorista_id) == 0
    assert _n_dms(repo, confirmada.motorista_id) == 0
    assert _n_dms(repo, expirada.motorista_id) == 1
    assert repo.status_viagem([ativa.motorista_id])["ativa"] is True


SCRIPT_WORKER = """
import sys, threading
import app
barreira = threading.Event()
app.AGENDADOR.executar_pendentes(forcar=True)
print(app.tarefa_dms())
"""


@requer_pg
def test_varios_workers_gunicorn_nao_duplicam_alerta(pg, repo, pg_url, tmp_path):
    """Simula 6 workers (processos independentes que importam o app e rodam o agendador ao mesmo tempo)."""
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT_WORKER], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=_env_pg(pg_url, "1", str(tmp_path)))
             for _ in range(6)]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
    assert _n_dms(repo, m.motorista_id) == 1


@requer_pg
def test_worker_dedicado_agendador_py_dispara(pg, repo, pg_url, tmp_path):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    env = _env_pg(pg_url, "1", str(tmp_path))     # o próprio agendador.py força AGENDADOR_EMBUTIDO=0
    env["AGENDADOR_PASSO"] = "0.5"
    proc = subprocess.Popen([sys.executable, "agendador.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        limite = time.time() + 30
        while time.time() < limite and _n_dms(repo, m.motorista_id) == 0:
            time.sleep(0.3)
    finally:
        proc.terminate()
        proc.communicate(timeout=30)
    assert _n_dms(repo, m.motorista_id) == 1


@requer_pg
def test_idempotencia_dms_garantida_pelo_banco(pg, repo):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    (mid, alerta), = [d for d in repo.disparar_viagens_expiradas() if d[0] == m.motorista_id]
    viagem_id = q(repo, "SELECT viagem_id FROM alertas WHERE id = %s", alerta["id"])[0]["viagem_id"]
    with pytest.raises(pgerr.UniqueViolation):
        with repo.tx() as c:
            c.execute("INSERT INTO alertas (motorista_id, viagem_id, tipo, nome_exibido, ocorrencia) "
                      "VALUES (%s, %s, 'dead_mans_switch', 'M', 'duplicado')", (mid, viagem_id))


# ═════════════════════ E. POSTGRESQL: integridade ═════════════════════

@requer_pg
def test_integridade_referencial(pg, repo):
    for sql, params in [
        ("INSERT INTO contatos_confianca (motorista_id, nome, telefone) VALUES (%s, 'x', '61900000000')", (999999999,)),
        ("INSERT INTO alertas (motorista_id, tipo, nome_exibido, ocorrencia) VALUES (%s, 'manual', 'x', 'y')", (999999999,)),
        ("INSERT INTO motoristas (usuario_id, nome) VALUES (%s, 'x')", (999999999,)),
        ("INSERT INTO painel_acessos (usuario_id, motorista_id) VALUES (%s, %s)", (999999999, 999999999)),
    ]:
        with pytest.raises(pgerr.ForeignKeyViolation):
            with repo.tx() as c:
                c.execute(sql, params)


@requer_pg
def test_constraints(pg, repo):
    m = criar_motorista(repo)
    casos = [
        ("INSERT INTO alertas (motorista_id, tipo, nome_exibido, ocorrencia, lat, lng) "
         "VALUES (%s, 'manual', 'x', 'y', 999, 0)", pgerr.CheckViolation),
        ("INSERT INTO alertas (motorista_id, tipo, nome_exibido, ocorrencia) VALUES (%s, 'invalido', 'x', 'y')",
         pgerr.CheckViolation),
        ("INSERT INTO alertas (motorista_id, tipo, status, nome_exibido, ocorrencia) "
         "VALUES (%s, 'manual', 'Apagado', 'x', 'y')", pgerr.CheckViolation),
        ("INSERT INTO viagens (publico_id, motorista_id, nome_exibido, minutos, status) "
         "VALUES ('x' || random(), %s, 'x', 0, 'em_andamento')", pgerr.CheckViolation),
    ]
    for sql, erro in casos:
        with pytest.raises(erro):
            with repo.tx() as c:
                c.execute(sql, (m.motorista_id,))
    repo.iniciar_viagem(m.motorista_id, "M", 30, None, None)
    with pytest.raises(pgerr.UniqueViolation):     # segunda viagem ativa direto no banco
        with repo.tx() as c:
            c.execute("INSERT INTO viagens (publico_id, motorista_id, nome_exibido, minutos, status) "
                      "VALUES ('dup' || random(), %s, 'x', 5, 'em_andamento')", (m.motorista_id,))
    with pytest.raises(pgerr.UniqueViolation):     # login duplicado (sem diferenciar maiúsculas)
        repo.criar_usuario(m.login.upper(), "senha-forte-12345", "motorista")


@requer_pg
def test_indices_esperados(pg, repo):
    nomes = {r["indexname"] for r in q(repo, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    for idx in ("usuarios_login_unico", "viagens_uma_ativa", "viagens_expiracao", "alertas_motorista",
                "contatos_motorista", "sessoes_expira", "tentativas_busca", "alertas_um_dms_por_viagem",
                "alertas_origem_legado", "viagens_origem_legado"):
        assert idx in nomes, idx


@requer_pg
def test_sql_injection_no_login_e_nos_dados(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    for login in ("' OR '1'='1", "' OR 1=1 --", f"{m.login}' --", "x'; UPDATE usuarios SET ativo=false; --"):
        assert entrar(c, "motorista", SimpleCtx(login=login, senha="' OR '1'='1")).status_code in (401, 429)
    assert q(repo, "SELECT ativo FROM usuarios WHERE id = %s", m.usuario_id)[0]["ativo"] is True
    c2 = novo_cliente()
    entrar(c2, "motorista", m)
    carga = "Robert'); DELETE FROM alertas; --"
    c2.post("/cadastro", data={"acao": "add", "nome": carga, "telefone": "61912345678", "_csrf": c2.csrf})
    assert repo.listar_contatos(m.motorista_id)[0]["nome"] == carga     # gravado literalmente


@requer_pg
def test_admin_nao_entra_pelas_telas_web(pg, repo):
    login = "adm_" + uuid.uuid4().hex[:6]
    conta = repo.criar_usuario(login, "senha-admin-forte-1", "admin")
    adm = SimpleCtx(login=login, senha="senha-admin-forte-1", **conta)
    for tipo in ("motorista", "painel"):
        c = novo_cliente()
        assert entrar(c, tipo, adm).status_code == 401


# ═════════════════════ F. LGPD / Git ═════════════════════

@pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")
def test_git_nao_versiona_dados_pessoais(tmp_path):
    """Copia a pasta do projeto (inclusive data/ e backups locais, se existirem) para um repositório
    temporário e confere o que entraria num commit. Nada é feito no projeto real."""
    destino = tmp_path / "repo"
    shutil.copytree(ROOT, destino, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    subprocess.run(["git", "init", "-q"], cwd=destino, check=True)
    subprocess.run(["git", "add", "-A"], cwd=destino, check=True, capture_output=True)
    arquivos = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=destino, check=True,
                              capture_output=True, text=True).stdout.splitlines()
    assert arquivos, "nada seria versionado?"
    proibidos = re.compile(r"(^|/)(data|data_backup_[^/]*|backup_fase[^/]*|lgpd_backup_[^/]*)/|\.db$|\.env($|\.)"
                           r"|alerts_backup_|\.corrompido-|\.lock$")
    assert not [a for a in arquivos if proibidos.search(a)]
    # nenhum arquivo versionável contém os telefones reais/de contato do data/
    contatos_locais = os.path.join(ROOT, "data", "contacts.json")
    telefones = {c.get("telefone") for c in json.load(open(contatos_locais, encoding="utf-8"))
                 if c.get("telefone")} if os.path.exists(contatos_locais) else set()
    for a in arquivos:
        caminho = destino / a
        if caminho.suffix in (".png", ".mp3", ".ico"):
            continue
        texto = caminho.read_text(encoding="utf-8", errors="ignore")
        for t in telefones:
            assert t not in texto, f"telefone de contato real em {a}"
