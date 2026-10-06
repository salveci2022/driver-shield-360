"""FASE 3 — lado JSON (padrão/rollback), CSP final sem estilos inline e agendador com lease em arquivo."""
import json, os, re, threading

import pytest

from conftest import ROOT, fazer_login
import app as app_module
from agendador import Agendador
from repositorio_json import RepositorioJSON


def test_csp_sem_unsafe_inline_em_nenhuma_diretiva(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "style-src 'self' https://fonts.googleapis.com" in csp


@pytest.mark.parametrize("url", ["/", "/checklist", "/motorista", "/cadastro", "/historico", "/ia",
                                 "/painel", "/relatorio", "/painel/login", "/motorista/login", "/pessoa_sair"])
def test_paginas_sem_estilo_inline(client, url):
    fazer_login(client, "motorista")
    fazer_login(client, "painel")
    html = client.get(url).get_data(as_text=True)
    assert "<style" not in html
    assert not re.search(r"<[a-zA-Z][^>]*\sstyle\s*=", html), f"atributo style= em {url}"


def test_css_externo_servido(client):
    for nome in ("motorista", "painel", "index", "cadastro"):
        r = client.get(f"/static/css/{nome}.css")
        assert r.status_code == 200 and "text/css" in r.headers["Content-Type"]


def test_modo_json_e_o_padrao_e_nao_pede_usuario(client):
    assert app_module.STORAGE_BACKEND == "json" and not app_module.REPO.multiusuario
    assert 'name="usuario"' not in client.get("/motorista/login").get_data(as_text=True)


def test_lease_em_arquivo_entre_workers(tmp_path):
    repo = RepositorioJSON(str(tmp_path))
    execucoes = []
    workers = []
    for _ in range(5):
        ag = Agendador(lambda n, ttl, ex: repo.adquirir_tarefa(n, ttl, ex))
        ag.tarefa("t", intervalo=30)(lambda: execucoes.append(1))
        workers.append(ag)
    ts = [threading.Thread(target=w.executar_pendentes) for w in workers]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(execucoes) == 1


def test_tarefa_com_erro_nao_derruba_agendador(capsys):
    ag = Agendador(lambda n, ttl, ex: True)
    ag.tarefa("quebra", 1)(lambda: 1 / 0)
    ag.tarefa("ok", 1)(lambda: None)
    assert ag.executar_pendentes() == ["ok"]
    assert "ZeroDivisionError" in capsys.readouterr().out


def test_tarefa_dms_json_dispara_viagem_expirada(motorista):
    motorista.post("/api/viagem/iniciar", data=json.dumps({"minutos": 5}),
                   headers={"Content-Type": "application/json", "X-CSRF-Token": motorista.csrf})
    v = app_module.load_viagem()
    v["expira_em"] = app_module.now_ts() - 1
    app_module.save_viagem(v)
    assert app_module.tarefa_dms() == 1
    assert app_module.tarefa_dms() == 0       # idempotente
    assert app_module.load_alerts()[-1]["tipo"] == "dead_mans_switch"


def test_auditoria_json_sem_dados_sensiveis(motorista):
    motorista.post("/api/panic", data=json.dumps({"motorista": "Fulana", "ocorrencia": "SEGREDO-OCORRENCIA"}),
                   headers={"Content-Type": "application/json", "X-CSRF-Token": motorista.csrf})
    linhas = open(os.path.join(app_module.DATA_DIR, "auditoria.jsonl"), encoding="utf-8").read()
    assert '"sos"' in linhas and "SEGREDO-OCORRENCIA" not in linhas and "Fulana" not in linhas
    assert "127.0.0.1" not in linhas


def test_resolver_alerta_inexistente_retorna_404(painel):
    r = painel.post("/api/resolve", data=json.dumps({"id": 999}),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": painel.csrf})
    assert r.status_code == 404


def test_requirements_tem_driver_postgres():
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "psycopg2-binary" in req
