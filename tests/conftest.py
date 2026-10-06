"""
Configuração dos testes: secrets de teste e diretório de dados TEMPORÁRIO.
Os arquivos reais em data/ nunca são lidos nem alterados pelos testes.
"""
import json, os, shutil, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_SECRETS = {
    "SECRET_KEY":      "teste-secret-key-0123456789abcdef",
    "PAINEL_SENHA":    "painel-teste-Senha!42",
    "MOTORISTA_SENHA": "motorista-teste-Codigo!77",
    "API_SECRET":      "api-secret-teste-9f8e7d6c5b4a",
}

_TMP_DATA = tempfile.mkdtemp(prefix="ds360_test_data_")
os.environ.update(TEST_SECRETS)
os.environ["DS360_DATA_DIR"] = _TMP_DATA
os.environ["AGENDADOR_EMBUTIDO"] = "0"     # testes chamam as tarefas diretamente
os.environ.pop("STORAGE_BACKEND", None)   # suíte principal: modo JSON
os.environ.pop("DS360_DEV", None)
for k in ("ZAPI_INSTANCE", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN"):
    os.environ.pop(k, None)   # garante que nenhum WhatsApp real é enviado

import app as app_module  # noqa: E402

CONTATOS_INICIAIS = [{"nome": "Ana Teste", "telefone": "61999990001", "relacao": "Familiar"}]
ALERTAS_INICIAIS = [{"ts": "01/01/2026 10:00:00", "motorista": "Joao", "ocorrencia": "Teste",
                     "lat": None, "lng": None, "status": "Ativo"}]


@pytest.fixture(autouse=True)
def dados_limpos():
    assert app_module.DATA_DIR == _TMP_DATA, "testes nunca devem usar data/ real"
    for nome in os.listdir(_TMP_DATA):
        if nome.startswith("seguranca.db"):
            continue   # banco de segurança: limpo via API abaixo
        caminho = os.path.join(_TMP_DATA, nome)
        shutil.rmtree(caminho) if os.path.isdir(caminho) else os.remove(caminho)
    app_module.save_contacts(json.loads(json.dumps(CONTATOS_INICIAIS)))
    app_module.save_alerts(json.loads(json.dumps(ALERTAS_INICIAIS)))
    app_module.SEG_DB.limpar_tudo()
    app_module.SEG_DB.revogar_todas()
    yield
    app_module.cancelar_dms()


@pytest.fixture
def app():
    return app_module.app


@pytest.fixture
def client(app):
    c = app.test_client()
    c.environ_base["HTTP_X_FORWARDED_PROTO"] = "https"   # como no Render (proxy HTTPS)
    return c


def _csrf_da_sessao(client):
    with client.session_transaction() as s:
        return s.get("_csrf")


def fazer_login(client, tipo, senha=None):
    senha = senha if senha is not None else TEST_SECRETS["PAINEL_SENHA" if tipo == "painel" else "MOTORISTA_SENHA"]
    client.get(f"/{tipo}/login")
    return client.post(f"/{tipo}/login", data={"senha": senha, "_csrf": _csrf_da_sessao(client)})


@pytest.fixture
def motorista(client):
    r = fazer_login(client, "motorista")
    assert r.status_code == 302
    client.get("/motorista")   # gera o token CSRF da nova sessão
    client.csrf = _csrf_da_sessao(client)
    return client


@pytest.fixture
def painel(client):
    r = fazer_login(client, "painel")
    assert r.status_code == 302
    client.get("/painel")
    client.csrf = _csrf_da_sessao(client)
    return client
