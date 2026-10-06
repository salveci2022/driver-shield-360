"""FASE 5 — preparação da homologação: URL_PUBLICA, /saude, Blueprint de staging e anonimização."""
import hashlib, json, os, re, subprocess, sys

import pytest

from conftest import ROOT, CONTATOS_INICIAIS
import app as app_module
import importar_json

sys.path.insert(0, os.path.join(ROOT, "homologacao"))
import anonimizar_para_staging as anon

PG = os.environ.get("DS360_TEST_PG_ADMIN_URL")
BLUEPRINT = open(os.path.join(ROOT, "render-staging.yaml"), encoding="utf-8").read()


# ── URL_PUBLICA ─────────────────────────────────────────────
def test_link_do_whatsapp_usa_url_publica(motorista, monkeypatch):
    enviados = []
    monkeypatch.setattr(app_module, "URL_PUBLICA", "https://driver-shield-360-staging.onrender.com")
    monkeypatch.setattr(app_module, "enviar_whatsapp", lambda n, m: enviados.append(m))
    motorista.post("/api/panic", data="{}", headers={"Content-Type": "application/json",
                                                     "X-CSRF-Token": motorista.csrf})
    assert enviados and all("https://driver-shield-360-staging.onrender.com/painel/login" in m for m in enviados)
    assert all("driver-shield-360.onrender.com" not in m for m in enviados)


def test_url_publica_padrao_mantem_producao():
    assert app_module.URL_PUBLICA == "https://driver-shield-360.onrender.com"


def test_url_publica_sem_https_e_recusada():
    env = {k: v for k, v in os.environ.items() if k != "DS360_DEV"}
    env["URL_PUBLICA"] = "http://exemplo.com"
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode != 0 and "URL_PUBLICA" in r.stderr


# ── /saude ──────────────────────────────────────────────────
def test_saude_json(client):
    r = client.get("/saude")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert r.headers["Cache-Control"] == "no-store"


def test_saude_sem_detalhes_quando_falha(client, monkeypatch, capsys):
    def falhar():
        raise OSError("caminho /segredo/interno senha=xyz")
    monkeypatch.setattr(app_module.REPO, "verificar_saude", falhar)
    r = client.get("/saude")
    assert r.status_code == 503 and r.get_json() == {"ok": False}
    saida = r.get_data(as_text=True) + capsys.readouterr().out
    assert "segredo" not in saida and "senha" not in saida


@pytest.mark.skipif(not PG, reason="DS360_TEST_PG_ADMIN_URL não definida — teste PostgreSQL NÃO EXECUTADO")
def test_saude_postgres_ok_e_banco_fora(client, monkeypatch):
    from test_fase3_pg import _novo_banco
    from repositorio_pg import RepositorioPG
    repo = RepositorioPG(_novo_banco(), 1, 2)
    monkeypatch.setattr(app_module, "REPO", repo)
    assert client.get("/saude").status_code == 200
    fora = RepositorioPG("postgresql://ninguem:x@127.0.0.1:1/nada?connect_timeout=2", 1, 1)
    monkeypatch.setattr(app_module, "REPO", fora)
    r = client.get("/saude")
    assert r.status_code == 503 and r.get_json() == {"ok": False}
    repo.fechar()


# ── Blueprint de homologação ────────────────────────────────
def test_blueprint_nao_tem_nome_aplicado_automaticamente():
    assert not os.path.exists(os.path.join(ROOT, "render.yaml"))


def test_blueprint_somente_recursos_de_staging():
    nomes = re.findall(r"^\s*-?\s*name:\s*(\S+)", BLUEPRINT, re.M)
    recursos = [n for n in nomes if n.startswith(("driver-shield-360", "ds360"))]
    assert recursos and all("staging" in n for n in recursos), recursos
    assert "branch: homologacao" in BLUEPRINT and BLUEPRINT.count("branch: homologacao") == 2
    assert BLUEPRINT.count('autoDeployTrigger: "off"') == 2


def test_blueprint_comandos_e_agendador():
    assert "preDeployCommand: python migrar.py aplicar" in BLUEPRINT
    assert "--bind 0.0.0.0:$PORT" in BLUEPRINT and "--preload" not in BLUEPRINT
    assert "startCommand: python agendador.py" in BLUEPRINT
    assert re.search(r"key: AGENDADOR_EMBUTIDO\s+value: \"0\"", BLUEPRINT)
    assert "healthCheckPath: /saude" in BLUEPRINT
    assert "value: postgres" in BLUEPRINT
    assert "ipAllowList: []" in BLUEPRINT


def test_blueprint_sem_segredos():
    for chave in ("SECRET_KEY", "API_SECRET", "ZAPI_INSTANCE", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN"):
        bloco = re.search(rf"key: {chave}\s*(?:#[^\n]*)?\n\s*(\w+):\s*(\S+)", BLUEPRINT)
        assert bloco and (bloco.group(1), bloco.group(2)) in (("generateValue", "true"), ("sync", "false")), chave
    assert "DS360_DEV" not in BLUEPRINT and "sk-ant" not in BLUEPRINT
    assert not re.search(r"postgres(ql)?://", BLUEPRINT)


def test_blueprint_shield_ai_desligado_na_homologacao():
    assert not re.search(r"^\s*-\s*key:\s*ANTHROPIC_API_KEY", BLUEPRINT, re.M)
    assert "IA_MODELO" not in BLUEPRINT


def test_blueprint_versao_python_testada():
    assert 'value: "3.14.3"' in BLUEPRINT


# ── Anonimização para a homologação ─────────────────────────
def _dados(pasta):
    os.makedirs(pasta)
    json.dump([{"nome": "Maria Real", "telefone": "61987650001", "relacao": "Familiar"},
               {"nome": "João Real", "telefone": "61987650002", "relacao": "Amigo(a)"}],
              open(os.path.join(pasta, "contacts.json"), "w", encoding="utf-8"))
    json.dump([{"ts": "23/03/2026 23:19:50", "motorista": "Fulana Real", "ocorrencia": "Assalto",
                "lat": "-23.550520", "lng": "-46.633308", "status": "Ativo"},
               {"ts": "24/03/2026 10:00:00", "motorista": "Fulana Real",
                "ocorrencia": "👤 Passageiro suspeito — homem de camisa vermelha, placa ABC1234",
                "lat": None, "lng": None, "status": "Resolvido", "tipo": "passageiro_suspeito"}],
              open(os.path.join(pasta, "alerts.json"), "w", encoding="utf-8"))
    json.dump([{"inicio": "20/03/2026 08:00:00", "motorista": "Fulana Real", "minutos": 30,
                "lat": "-23.5", "lng": "-46.6", "resultado": "chegou_segura", "fim": "20/03/2026 08:20:00"}],
              open(os.path.join(pasta, "historico_viagens.json"), "w", encoding="utf-8"))
    json.dump({"ativa": True, "motorista": "Fulana Real", "expira_em": 1},
              open(os.path.join(pasta, "viagem.json"), "w", encoding="utf-8"))
    return pasta


def _hashes(pasta):
    return {n: hashlib.sha256(open(os.path.join(pasta, n), "rb").read()).hexdigest() for n in sorted(os.listdir(pasta))}


def test_anonimizacao_remove_dados_pessoais(tmp_path):
    origem = _dados(str(tmp_path / "data"))
    antes = _hashes(origem)
    anon.anonimizar(origem, str(tmp_path / "staging"), ["5561900000000", "5561900000001"])
    assert _hashes(origem) == antes                         # originais intactos
    texto = "".join(open(tmp_path / "staging" / n, encoding="utf-8").read() for n in anon.ARQUIVOS)
    for pessoal in ("Maria Real", "João Real", "61987650001", "61987650002", "Fulana", "-23.55", "-46.63",
                    "camisa vermelha", "ABC1234"):
        assert pessoal not in texto, pessoal
    contatos = json.load(open(tmp_path / "staging" / "contacts.json", encoding="utf-8"))
    assert [c["telefone"] for c in contatos] == ["5561900000000", "5561900000001"]
    assert json.load(open(tmp_path / "staging" / "viagem.json", encoding="utf-8")) == {"ativa": False}


def test_anonimizacao_continua_valida_para_o_importador(tmp_path):
    origem = _dados(str(tmp_path / "data"))
    anon.anonimizar(origem, str(tmp_path / "staging"), ["5561900000000"])
    v = importar_json.validar(str(tmp_path / "staging"))
    assert v["resumo"]["alertas"] == {"lidos": 2, "validos": 2}
    assert v["resumo"]["historico"] == {"lidos": 1, "validos": 1}
    assert v["resumo"]["contatos"]["validos"] == 1          # 2º contato repetiria o mesmo telefone de teste
    tipos = [a["tipo"] for a in v["validos"]["alertas"]]
    assert tipos == ["manual", "passageiro_suspeito"]


def test_anonimizacao_nunca_sobrescreve_e_exige_telefone_da_equipe(tmp_path):
    origem = _dados(str(tmp_path / "data"))
    (tmp_path / "existe").mkdir()
    with pytest.raises(FileExistsError):
        anon.anonimizar(origem, str(tmp_path / "existe"), ["5561900000000"])
    with pytest.raises(ValueError):
        anon.anonimizar(origem, str(tmp_path / "nova"), [])
    with pytest.raises(ValueError):
        anon.anonimizar(origem, str(tmp_path / "nova2"), ["123"])


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "data", "contacts.json")),
                    reason="clone sem dados locais (esperado no GitHub/CI)")
def test_anonimizacao_dos_dados_reais_nao_altera_originais(tmp_path):
    """Executa sobre o data/ REAL (somente leitura) e confere que nada mudou e nada pessoal saiu."""
    real = os.path.join(ROOT, "data")
    antes = {n: _hashes(real)[n] for n in ("alerts.json", "contacts.json")}
    anon.anonimizar(real, str(tmp_path / "staging_real"), ["5561900000000"])
    assert {n: _hashes(real)[n] for n in antes} == antes
    reais = json.load(open(os.path.join(real, "contacts.json"), encoding="utf-8"))
    texto = "".join(open(tmp_path / "staging_real" / n, encoding="utf-8").read() for n in anon.ARQUIVOS)
    for c in reais:
        assert c["telefone"] not in texto and c["nome"] not in texto
