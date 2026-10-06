"""
FASE 3 — PostgreSQL: migrations, isolamento entre motoristas (IDOR/BOLA), autenticação,
autorização, importação JSON→PostgreSQL, rollback, concorrência, DMS/agendador, rate limit.

Requer um PostgreSQL de TESTE: DS360_TEST_PG_ADMIN_URL=postgresql://usuario:senha@host:porta/postgres
Cada execução cria um banco novo (ds360_teste_<aleatório>); nada é apagado (sem DROP/TRUNCATE).
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time, uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROOT, TEST_SECRETS
import app as app_module

ADMIN_URL = os.environ.get("DS360_TEST_PG_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DS360_TEST_PG_ADMIN_URL não definida (PostgreSQL de teste)")

if ADMIN_URL:
    import psycopg2, psycopg2.extras
    import migrar, importar_json, rollback_pg_para_json
    from repositorio_pg import RepositorioPG
    from seguranca_db import SegurancaPG
    from agendador import Agendador


def _novo_banco():
    nome = "ds360_teste_" + uuid.uuid4().hex[:10]
    adm = psycopg2.connect(ADMIN_URL)
    adm.autocommit = True
    with adm.cursor() as c:
        c.execute(f"CREATE DATABASE {nome}")
    adm.close()
    return re.sub(r"/[^/?]+(\?|$)", f"/{nome}\\1", ADMIN_URL, count=1)


@pytest.fixture(scope="module")
def pg_url():
    url = _novo_banco()
    conn = psycopg2.connect(url)
    migrar.aplicar(conn)
    conn.close()
    return url


@pytest.fixture(scope="module")
def repo(pg_url):
    r = RepositorioPG(pg_url)
    yield r
    r.fechar()


@pytest.fixture
def pg(repo, monkeypatch):
    """Coloca o app em modo PostgreSQL (multiusuário) durante o teste."""
    seg = SegurancaPG(repo, TEST_SECRETS["SECRET_KEY"])
    monkeypatch.setattr(app_module, "REPO", repo)
    monkeypatch.setattr(app_module, "SEG_DB", seg)
    monkeypatch.setattr(app_module, "_dms_ultima_verificacao", time.time() + 10**6)  # sem varredura automática
    return SimpleCtx(repo=repo, seg=seg)


class SimpleCtx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _login_unico(prefixo):
    return f"{prefixo}_{uuid.uuid4().hex[:8]}"


def criar_motorista(repo, nome="Motorista"):
    login, senha = _login_unico("mot"), "senha-forte-" + uuid.uuid4().hex[:6]
    conta = repo.criar_usuario(login, senha, "motorista", nome=nome)
    return SimpleCtx(login=login, senha=senha, **conta)


def criar_painel(repo, *motoristas):
    login, senha = _login_unico("pnl"), "senha-forte-" + uuid.uuid4().hex[:6]
    conta = repo.criar_usuario(login, senha, "painel")
    for m in motoristas:
        repo.vincular_painel(conta["usuario_id"], m.motorista_id)
    return SimpleCtx(login=login, senha=senha, **conta)


def novo_cliente(ip=None):
    c = app_module.app.test_client()
    c.environ_base["HTTP_X_FORWARDED_PROTO"] = "https"
    c.environ_base["REMOTE_ADDR"] = ip or f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    return c


def entrar(cliente, tipo, conta, senha=None):
    cliente.get(f"/{tipo}/login")
    with cliente.session_transaction() as s:
        csrf = s["_csrf"]
    r = cliente.post(f"/{tipo}/login", data={"usuario": conta.login, "senha": senha or conta.senha, "_csrf": csrf})
    pagina = "/motorista" if tipo == "motorista" else "/painel"
    cliente.get(pagina)
    with cliente.session_transaction() as s:
        cliente.csrf = s.get("_csrf")
    return r


def j(cliente, url, body=None, headers=None, csrf=True):
    h = {"Content-Type": "application/json"}
    if csrf and getattr(cliente, "csrf", None):
        h["X-CSRF-Token"] = cliente.csrf
    h.update(headers or {})
    return cliente.post(url, data=json.dumps(body or {}), headers=h)


def q(repo, sql, *params):
    with repo.tx() as c:
        c.execute(sql, params)
        return c.fetchall()


@pytest.fixture
def cenario(pg, repo):
    """Dois motoristas (A e B), cada um com contato e alerta; painel PA vinculado só a A."""
    A, B = criar_motorista(repo, "Ana Motorista"), criar_motorista(repo, "Bruno Motorista")
    PA = criar_painel(repo, A)
    repo.adicionar_contato(A.motorista_id, "Contato da Ana", "61911110000", "Familiar")
    repo.adicionar_contato(B.motorista_id, "Contato do Bruno", "61922220000", "Familiar")
    alerta_b = repo.registrar_alerta(B.motorista_id, {"tipo": "manual", "motorista": "Bruno Motorista",
                                                      "ocorrencia": "Alerta do Bruno", "lat": "-15.1", "lng": "-47.1"})
    alerta_a = repo.registrar_alerta(A.motorista_id, {"tipo": "manual", "motorista": "Ana Motorista",
                                                      "ocorrencia": "Alerta da Ana"})
    return SimpleCtx(A=A, B=B, PA=PA, alerta_a=alerta_a, alerta_b=alerta_b)


# ═════════════ Migrations ═════════════

def test_migracoes_sem_comandos_destrutivos():
    for _v, nome, sql, _h in migrar.listar_migracoes():
        assert not migrar.PROIBIDOS.search(migrar._sem_comentarios(sql)), nome


def test_migracao_destrutiva_e_recusada(tmp_path, monkeypatch):
    (tmp_path / "0001_ruim.sql").write_text("CREATE TABLE x (a int);\nDROP TABLE x;", encoding="utf-8")
    monkeypatch.setattr(migrar, "PASTA", str(tmp_path))
    with pytest.raises(ValueError, match="destrutivo"):
        migrar.listar_migracoes()


def test_migracoes_idempotentes_e_registradas(pg_url):
    conn = psycopg2.connect(pg_url)
    try:
        assert migrar.aplicar(conn) == []
        st = migrar.status(conn)
        assert st["pendentes"] == [] and st["aplicadas"] == len(migrar.listar_migracoes())
    finally:
        conn.close()


def test_tabelas_exigidas_existem(repo):
    tabelas = {r["tablename"] for r in q(repo, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    for t in ("usuarios", "motoristas", "contatos_confianca", "veiculos", "viagens", "alertas", "ocorrencias",
              "localizacoes", "sessoes", "auditoria", "tentativas", "tarefas_agendadas", "painel_acessos",
              "importacoes", "schema_migrations"):
        assert t in tabelas, t


def test_migracao_alterada_apos_aplicar_e_detectada():
    url = _novo_banco()
    conn = psycopg2.connect(url)
    try:
        migrar.aplicar(conn)
        with conn.cursor() as c:
            c.execute("UPDATE schema_migrations SET sha256 = 'adulterado'")
        conn.commit()
        assert migrar.status(conn)["alteradas_apos_aplicar"]
        with pytest.raises(RuntimeError, match="alteradas"):
            migrar.aplicar(conn)
    finally:
        conn.close()


def _importar_app(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DS360_DEV",)}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_app_postgres_recusa_banco_nao_migrado():
    r = _importar_app({"STORAGE_BACKEND": "postgres", "DATABASE_URL": _novo_banco()})
    assert r.returncode != 0 and "migrar.py aplicar" in r.stderr


def test_app_postgres_exige_database_url():
    r = _importar_app({"STORAGE_BACKEND": "postgres", "DATABASE_URL": ""})
    assert r.returncode != 0 and "DATABASE_URL" in r.stderr


def test_app_postgres_inicia_com_banco_migrado(pg_url):
    env = {"STORAGE_BACKEND": "postgres", "DATABASE_URL": pg_url}
    for k in ("PAINEL_SENHA", "MOTORISTA_SENHA"):
        env[k] = ""   # no PostgreSQL as senhas compartilhadas não são exigidas
    r = _importar_app(env)
    assert r.returncode == 0, r.stderr
    assert pg_url.split("@")[0] not in r.stdout + r.stderr   # credenciais do banco nunca vão para o log


# ═════════════ Autenticação ═════════════

def test_login_correto_e_incorreto(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    assert entrar(c, "motorista", m, senha="errada-123456").status_code == 401
    assert c.get("/motorista").status_code == 302
    assert entrar(c, "motorista", m).status_code == 302
    assert c.get("/motorista").status_code == 200


def test_usuario_inexistente_mesma_mensagem(pg, repo):
    c = novo_cliente()
    r1 = entrar(c, "motorista", SimpleCtx(login="nao_existe_" + uuid.uuid4().hex[:6], senha="x" * 12))
    m = criar_motorista(repo)
    r2 = entrar(novo_cliente(), "motorista", m, senha="senha-errada-99")
    assert r1.status_code == r2.status_code == 401
    assert "Usuário ou senha incorretos" in r1.get_data(as_text=True)
    assert "Usuário ou senha incorretos" in r2.get_data(as_text=True)


def test_papel_errado_nao_entra(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    assert entrar(c, "painel", m).status_code == 401
    assert c.get("/painel").status_code == 302


def test_senha_armazenada_com_hash(pg, repo):
    m = criar_motorista(repo)
    h = q(repo, "SELECT senha_hash FROM usuarios WHERE id = %s", m.usuario_id)[0]["senha_hash"]
    assert m.senha not in h and h.startswith(("scrypt:", "pbkdf2:"))


def test_troca_de_senha_invalida_sessoes(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    assert c.get("/api/viagem/status").status_code == 200
    time.sleep(0.01)
    repo.trocar_senha(m.usuario_id, "nova-senha-forte-1")
    assert c.get("/api/viagem/status").status_code == 401


def test_usuario_desativado_perde_acesso(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    repo.definir_ativo(m.usuario_id, False)
    assert c.get("/api/viagem/status").status_code == 401
    assert entrar(novo_cliente(), "motorista", m).status_code == 401


def test_sessao_e_ip_gravados_somente_como_hash(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente(ip="203.0.113.77")
    entrar(c, "motorista", m, senha="errada-0000000")
    entrar(c, "motorista", m)
    with c.session_transaction() as s:
        sid = s["motorista_sid"]
    tudo = json.dumps(q(repo, "SELECT * FROM sessoes") + q(repo, "SELECT * FROM tentativas") +
                      q(repo, "SELECT * FROM auditoria"), default=str)
    assert sid not in tudo and "203.0.113.77" not in tudo and m.senha not in tudo


def test_limite_por_conta_contra_ataque_distribuido(pg, repo):
    m = criar_motorista(repo)
    for i in range(app_module.LOGIN_MAX_FALHAS_CONTA):
        entrar(novo_cliente(), "motorista", m, senha=f"errada-{i:08d}")   # cada tentativa de um IP novo
    assert entrar(novo_cliente(), "motorista", m).status_code == 429


def test_limite_por_ip_postgres(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    for i in range(app_module.LOGIN_MAX_FALHAS):
        assert entrar(c, "motorista", m, senha=f"errada-{i:08d}").status_code == 401
    assert entrar(c, "motorista", m).status_code == 429


def test_logout_revoga_sessao_no_postgres(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    c.post("/motorista/logout", data={"_csrf": c.csrf})
    assert c.get("/motorista").status_code == 302
    assert q(repo, "SELECT count(*) AS n FROM sessoes WHERE usuario_id = %s", m.usuario_id)[0]["n"] == 0


# ═════════════ Isolamento entre motoristas (IDOR / BOLA) ═════════════

def test_motorista_ve_somente_os_proprios_contatos(cenario):
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    html = c.get("/cadastro").get_data(as_text=True)
    assert "Contato da Ana" in html and "Contato do Bruno" not in html
    api = c.get("/api/contacts").get_json()
    assert [x["nome"] for x in api] == ["Contato da Ana"]


def test_idor_remover_contato_de_outro_motorista(cenario, repo):
    contato_b = repo.listar_contatos(cenario.B.motorista_id)[0]["id"]
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    r = c.post("/cadastro", data={"acao": "delete", "idx": str(contato_b), "_csrf": c.csrf})
    assert r.status_code == 302
    assert len(repo.listar_contatos(cenario.B.motorista_id)) == 1   # intacto


def test_painel_ve_somente_motoristas_vinculados(cenario):
    c = novo_cliente()
    entrar(c, "painel", cenario.PA)
    ocorrencias = [a["ocorrencia"] for a in c.get("/api/alerts").get_json()]
    assert "Alerta da Ana" in ocorrencias and "Alerta do Bruno" not in ocorrencias


def test_idor_resolver_alerta_de_outro_motorista(cenario, repo):
    c = novo_cliente()
    entrar(c, "painel", cenario.PA)
    r = j(c, "/api/resolve", {"id": cenario.alerta_b["id"]})
    assert r.status_code == 404
    assert q(repo, "SELECT status FROM alertas WHERE id = %s", cenario.alerta_b["id"])[0]["status"] == "Ativo"
    assert j(c, "/api/resolve", {"id": cenario.alerta_a["id"]}).status_code == 200


def test_limpar_alertas_afeta_somente_vinculados_e_nao_apaga(cenario, repo):
    c = novo_cliente()
    entrar(c, "painel", cenario.PA)
    assert j(c, "/api/clear_alerts").status_code == 200
    assert c.get("/api/alerts").get_json() == []
    linhas = q(repo, "SELECT motorista_id, arquivado_em FROM alertas WHERE id IN (%s, %s)",
               cenario.alerta_a["id"], cenario.alerta_b["id"])
    estado = {r["motorista_id"]: r["arquivado_em"] for r in linhas}
    assert estado[cenario.A.motorista_id] is not None      # arquivado (não apagado)
    assert estado[cenario.B.motorista_id] is None          # outro motorista intacto


def test_id_de_motorista_no_corpo_e_ignorado(cenario, repo):
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    r = j(c, "/api/panic", {"motorista_id": cenario.B.motorista_id, "motorista": "Ana", "ocorrencia": "Teste IDOR"})
    assert r.status_code == 200
    dono = q(repo, "SELECT motorista_id FROM alertas WHERE id = %s", r.get_json()["alerta"]["id"])[0]["motorista_id"]
    assert dono == cenario.A.motorista_id


def test_historico_e_status_isolados(cenario, repo):
    repo.iniciar_viagem(cenario.B.motorista_id, "Bruno Motorista", 30, None, None)
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    assert c.get("/api/viagem/status").get_json() == {"ativa": False}
    assert "Bruno Motorista" not in c.get("/historico").get_data(as_text=True)
    pa = novo_cliente()
    entrar(pa, "painel", cenario.PA)
    assert pa.get("/api/viagem/status").get_json() == {"ativa": False}


def test_pdf_e_relatorio_somente_vinculados(cenario):
    c = novo_cliente()
    entrar(c, "painel", cenario.PA)
    html = c.get("/relatorio").get_data(as_text=True)
    assert "Alerta da Ana" in html and "Alerta do Bruno" not in html
    pdf = c.get("/relatorio.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    texto = _texto_pdf(pdf.data)
    assert "Bruno" not in texto and "Ana Motorista" in texto


def _texto_pdf(dados):
    """Conteúdo das páginas do PDF (o ReportLab comprime os streams com zlib/ASCII85)."""
    import base64, zlib
    partes = []
    for bruto in re.findall(rb"stream\s*\n(.*?)\s*endstream", dados, re.S):
        bruto = bruto.strip()
        for decodificar in (lambda b: zlib.decompress(base64.a85decode(b[:-2] if b.endswith(b"~>") else b)),
                            zlib.decompress, lambda b: b):
            try:
                partes.append(decodificar(bruto).decode("latin-1"))
                break
            except Exception:
                continue
    return " ".join(partes)


def test_exportacao_lgpd_somente_do_proprio(cenario):
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    texto = json.dumps(c.get("/api/lgpd/exportar").get_json(), ensure_ascii=False)
    assert "Contato da Ana" in texto and "Bruno" not in texto and "61922220000" not in texto


def test_painel_sem_vinculo_nao_ve_nada(pg, cenario, repo):
    sem = criar_painel(repo)
    c = novo_cliente()
    entrar(c, "painel", sem)
    assert c.get("/api/alerts").get_json() == []
    assert j(c, "/api/resolve", {"id": cenario.alerta_a["id"]}).status_code == 404


def test_motorista_nao_usa_apis_do_painel(cenario):
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    assert c.get("/api/alerts").status_code == 401
    assert j(c, "/api/resolve", {"id": cenario.alerta_a["id"]}).status_code == 401


def test_token_servidor_exige_motorista_explicito(cenario, repo):
    c = novo_cliente()
    h = {"X-Shield-Token": TEST_SECRETS["API_SECRET"]}
    assert j(c, "/api/panic", {}, headers=h, csrf=False).status_code == 400
    assert j(c, "/api/panic", {}, headers=dict(h, **{"X-Shield-Motorista": "999999"}), csrf=False).status_code == 400
    r = j(c, "/api/panic", {"ocorrencia": "Integração"}, csrf=False,
          headers=dict(h, **{"X-Shield-Motorista": str(cenario.B.motorista_id)}))
    assert r.status_code == 200
    assert q(repo, "SELECT motorista_id FROM alertas WHERE id = %s", r.get_json()["alerta"]["id"])[0]["motorista_id"] \
        == cenario.B.motorista_id


def test_veiculos_isolados(cenario, repo):
    repo.adicionar_veiculo(cenario.A.motorista_id, "ABC1D23", "Onix")
    repo.adicionar_veiculo(cenario.B.motorista_id, "XYZ9Z99", "HB20")
    assert [v["placa"] for v in repo.listar_veiculos(cenario.A.motorista_id)] == ["ABC1D23"]


def test_notificacao_vai_somente_para_contatos_do_proprio_motorista(cenario, monkeypatch):
    enviados = []
    monkeypatch.setattr(app_module, "enviar_whatsapp", lambda numero, msg: enviados.append(numero))
    c = novo_cliente()
    entrar(c, "motorista", cenario.A)
    j(c, "/api/panic", {"ocorrencia": "SOS"})
    assert enviados == ["61911110000"]


# ═════════════ Funcionalidades (PostgreSQL) ═════════════

def test_sos_com_gps_registra_localizacao(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    r = j(c, "/api/panic", {"motorista": "M", "ocorrencia": "Assalto", "lat": "-15.7801", "lng": "-47.9292"})
    a = r.get_json()["alerta"]
    assert a["lat"] == "-15.780100" and a["lng"] == "-47.929200" and a["status"] == "Ativo"
    loc = q(repo, "SELECT lat, lng, origem FROM localizacoes WHERE alerta_id = %s", a["id"])
    assert len(loc) == 1 and loc[0]["origem"] == "sos"


def test_gps_invalido_nao_e_gravado(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    a = j(c, "/api/panic", {"lat": "javascript:alert(1)", "lng": "999"}).get_json()["alerta"]
    assert a["lat"] is None and not q(repo, "SELECT 1 FROM localizacoes WHERE alerta_id = %s", a["id"])


def test_passageiro_suspeito_registra_ocorrencia(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    assert j(c, "/api/passageiro_suspeito", {"descricao": "agressivo"}).status_code == 200
    oc = q(repo, "SELECT categoria, descricao FROM ocorrencias WHERE motorista_id = %s", m.motorista_id)
    assert oc == [{"categoria": "passageiro_suspeito", "descricao": "agressivo"}]


def test_fluxo_de_viagem_postgres(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    assert j(c, "/api/viagem/iniciar", {"motorista": "M", "minutos": 30, "lat": "-15.7", "lng": "-47.9"}).get_json()["segundos"] == 1800
    st = c.get("/api/viagem/status").get_json()
    assert st["ativa"] and 1790 <= st["restante"] <= 1800
    j(c, "/api/viagem/confirmar")
    assert c.get("/api/viagem/status").get_json() == {"ativa": False}
    j(c, "/api/viagem/iniciar", {"minutos": 10})
    j(c, "/api/viagem/cancelar")
    resultados = [v["resultado"] for v in repo.listar_historico([m.motorista_id])]
    assert resultados == ["cancelada", "chegou_segura"]
    assert "Chegou" in c.get("/historico").get_data(as_text=True) or c.get("/historico").status_code == 200


def test_sos_cancela_viagem_ativa(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    j(c, "/api/viagem/iniciar", {"minutos": 30})
    j(c, "/api/panic", {})
    assert c.get("/api/viagem/status").get_json() == {"ativa": False}
    assert q(repo, "SELECT status FROM viagens WHERE motorista_id = %s", m.motorista_id)[0]["status"] == "cancelada_por_sos"


def test_apenas_uma_viagem_ativa_por_motorista(pg, repo):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 30, None, None)
    repo.iniciar_viagem(m.motorista_id, "M", 30, None, None)
    st = [r["status"] for r in q(repo, "SELECT status FROM viagens WHERE motorista_id = %s ORDER BY id", m.motorista_id)]
    assert st == ["interrompida", "em_andamento"]


def test_contatos_limite_e_duplicado_com_concorrencia(pg, repo):
    m = criar_motorista(repo)
    resultados = []
    def add(i):
        resultados.append(repo.adicionar_contato(m.motorista_id, f"C{i}", f"6190000{i:04d}", "Familiar"))
    ts = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert resultados.count("ok") == 5 and len(repo.listar_contatos(m.motorista_id)) == 5
    m2 = criar_motorista(repo)
    assert repo.adicionar_contato(m2.motorista_id, "X", "61999990000", "") == "ok"
    assert repo.adicionar_contato(m2.motorista_id, "Y", "61999990000", "") == "duplicado"


def test_alertas_simultaneos_postgres(pg, repo):
    """Mais threads que conexões no pool: ninguém perde o alerta (espera uma conexão livre)."""
    m = criar_motorista(repo)
    ts = [threading.Thread(target=repo.registrar_alerta,
                           args=(m.motorista_id, {"tipo": "manual", "motorista": "M", "ocorrencia": str(i)}))
          for i in range(25)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(repo.listar_alertas([m.motorista_id])) == 25


def test_xss_postgres_escapado(pg, repo):
    payload = '<img src=x onerror=alert(1)>'
    m = criar_motorista(repo)
    p = criar_painel(repo, m)
    c = novo_cliente()
    entrar(c, "motorista", m)
    j(c, "/api/panic", {"motorista": payload, "ocorrencia": payload})
    j(c, "/api/viagem/iniciar", {"motorista": payload, "minutos": 30})
    c.post("/cadastro", data={"acao": "add", "nome": payload, "telefone": "61987654321", "_csrf": c.csrf})
    assert payload not in c.get("/cadastro").get_data(as_text=True)
    assert payload not in c.get("/historico").get_data(as_text=True)
    pc = novo_cliente()
    entrar(pc, "painel", p)
    assert payload not in pc.get("/relatorio").get_data(as_text=True)


def test_csrf_postgres(pg, repo):
    m = criar_motorista(repo)
    c = novo_cliente()
    entrar(c, "motorista", m)
    assert j(c, "/api/panic", {}, csrf=False).status_code == 403
    assert c.post("/cadastro", data={"acao": "add", "nome": "X", "telefone": "61900001111"}).status_code == 302
    assert repo.listar_contatos(m.motorista_id) == []


# ═════════════ Dead Man's Switch / agendador ═════════════

def _expirar(repo, motorista_id):
    with repo.tx() as c:
        c.execute("UPDATE viagens SET expira_em = now() - interval '1 second' "
                  "WHERE motorista_id = %s AND status = 'em_andamento'", (motorista_id,))


def test_dms_nao_dispara_antes_de_expirar(pg, repo):
    m = criar_motorista(repo)
    v = repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    assert repo.disparar_viagem(v["id"]) is None
    assert app_module.tarefa_dms() == 0 or not repo.listar_alertas([m.motorista_id])


def test_dms_dispara_uma_unica_vez_com_threads(pg, repo):
    m = criar_motorista(repo, "Dani")
    repo.adicionar_contato(m.motorista_id, "Contato", "61955554444", "")
    repo.iniciar_viagem(m.motorista_id, "Dani", 5, "-15.5", "-47.5")
    _expirar(repo, m.motorista_id)
    total = []
    ts = [threading.Thread(target=lambda: total.append(len(repo.disparar_viagens_expiradas()))) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    alertas = [a for a in repo.listar_alertas([m.motorista_id]) if a["tipo"] == "dead_mans_switch"]
    assert len(alertas) == 1 and alertas[0]["motorista"] == "Dani" and alertas[0]["lat"] == "-15.500000"


SCRIPT_DMS = """
import sys
sys.path.insert(0, sys.argv[1])
from repositorio_pg import RepositorioPG
r = RepositorioPG(sys.argv[2], 1, 2)
print(len(r.disparar_viagens_expiradas()))
"""


def test_dms_dispara_uma_unica_vez_entre_processos(pg, repo, pg_url):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT_DMS, ROOT, pg_url], stdout=subprocess.PIPE, text=True)
             for _ in range(6)]
    [p.communicate(timeout=60) for p in procs]
    assert sum(1 for a in repo.listar_alertas([m.motorista_id]) if a["tipo"] == "dead_mans_switch") == 1


def test_dms_viagem_antiga_nao_dispara_na_nova(pg, repo):
    m = criar_motorista(repo)
    antiga = repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    assert repo.disparar_viagem(antiga["id"]) is None
    assert repo.status_viagem([m.motorista_id])["ativa"] is True


def test_dms_confirmada_nao_dispara(pg, repo):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    repo.encerrar_viagem(m.motorista_id, "chegou_segura")
    with repo.tx() as c:
        c.execute("UPDATE viagens SET expira_em = now() - interval '1 hour' WHERE motorista_id = %s", (m.motorista_id,))
    assert not [d for d in repo.disparar_viagens_expiradas() if d[0] == m.motorista_id]


def test_dms_notifica_contatos_do_motorista_certo(pg, repo, monkeypatch):
    A, B = criar_motorista(repo), criar_motorista(repo)
    repo.adicionar_contato(A.motorista_id, "CA", "61900000001", "")
    repo.adicionar_contato(B.motorista_id, "CB", "61900000002", "")
    repo.iniciar_viagem(B.motorista_id, "B", 5, None, None)
    _expirar(repo, B.motorista_id)
    enviados = []
    monkeypatch.setattr(app_module, "enviar_whatsapp", lambda numero, msg: enviados.append(numero))
    app_module.tarefa_dms()
    assert enviados == ["61900000002"]


def test_lease_do_agendador_entre_workers(pg, repo):
    """Dois 'workers' (agendadores independentes) disputam a mesma tarefa: ela roda uma vez."""
    execucoes = []
    nome = "teste_lease_" + uuid.uuid4().hex[:6]
    workers = []
    for _ in range(4):
        ag = Agendador(lambda n, ttl, ex: repo.adquirir_tarefa(n, ttl, ex))
        ag.tarefa(nome, intervalo=30)(lambda: execucoes.append(1))
        workers.append(ag)
    ts = [threading.Thread(target=w.executar_pendentes) for w in workers]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(execucoes) == 1


def test_agendador_em_thread_dispara_dms_sem_timer(pg, repo, monkeypatch):
    m = criar_motorista(repo)
    repo.iniciar_viagem(m.motorista_id, "M", 5, None, None)
    _expirar(repo, m.motorista_id)
    ag = Agendador(lambda n, ttl, ex: repo.adquirir_tarefa(n, ttl, ex))
    ag.tarefa("dms_teste_" + uuid.uuid4().hex[:6], intervalo=10)(app_module.tarefa_dms)
    ag.garantir_thread(passo=0.1)
    try:
        limite = time.time() + 10
        while time.time() < limite and repo.status_viagem([m.motorista_id])["ativa"]:
            time.sleep(0.1)
    finally:
        ag.parar()
    assert repo.status_viagem([m.motorista_id]) == {"ativa": False}
    assert any(a["tipo"] == "dead_mans_switch" for a in repo.listar_alertas([m.motorista_id]))


def test_sem_threading_timer_no_codigo():
    fonte = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in fonte.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"Timer\s*\(", codigo)


# ═════════════ Rate limit compartilhado (PostgreSQL) ═════════════

SCRIPT_RATE = """
import sys
sys.path.insert(0, sys.argv[1])
from repositorio_pg import RepositorioPG
from seguranca_db import SegurancaPG
s = SegurancaPG(RepositorioPG(sys.argv[2], 1, 2), 'pepper')
print(s.consumir('login:x', sys.argv[3], 5, 900))
"""


def test_rate_limit_atomico_entre_processos_postgres(pg_url):
    chave = "198.51.100." + str(uuid.uuid4().int % 250)
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT_RATE, ROOT, pg_url, chave],
                              stdout=subprocess.PIPE, text=True) for _ in range(8)]
    saidas = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert saidas.count("True") == 5 and saidas.count("False") == 3


# ═════════════ Importação JSON → PostgreSQL ═════════════

def _dados_legados(pasta, ativa_expirada=False):
    os.makedirs(pasta, exist_ok=True)
    contatos = [{"nome": "Ana Contato", "telefone": "61999990001", "relacao": "Familiar"},
                {"nome": "Duplicada", "telefone": "(61) 99999-0001"},          # duplicado
                {"nome": "", "telefone": "61999990002"},                        # inválido
                {"nome": "Beto", "telefone": "123"},                            # inválido
                {"nome": "Carla", "telefone": "61999990003"}]
    alertas = [{"ts": "23/03/2026 23:19:50", "motorista": "Motorista", "ocorrencia": "Tentativa de assalto",
                "lat": None, "lng": None, "status": "Ativo"},
               {"ts": "24/03/2026 10:00:00", "motorista": "Motorista", "ocorrencia": "Acidente",
                "lat": "-15.780000", "lng": "-47.929200", "status": "Resolvido", "tipo": "manual"},
               {"ts": "24/03/2026 10:00:00", "motorista": "Motorista", "ocorrencia": "Acidente",
                "lat": "-15.780000", "lng": "-47.929200", "status": "Resolvido", "tipo": "manual"},   # duplicado
               {"ts": "data-errada", "motorista": "X", "ocorrencia": "Y"},                          # inválido
               {"ts": "25/03/2026 08:00:00", "motorista": "M", "ocorrencia": "GPS ruim", "lat": "999", "lng": "1"}]
    historico = [{"inicio": "20/03/2026 08:00:00", "motorista": "Motorista", "minutos": 30, "lat": None, "lng": None,
                  "resultado": "chegou_segura", "fim": "20/03/2026 08:20:00"},
                 {"inicio": "21/03/2026 08:00:00", "motorista": "Motorista", "minutos": 30, "lat": "-15.7",
                  "lng": "-47.9", "resultado": "em_andamento"}]
    viagem = {"ativa": False}
    if ativa_expirada:
        viagem = {"id": "abc", "ativa": True, "motorista": "Motorista", "expira_em": time.time() - 60,
                  "inicio": time.time() - 2000, "duracao": 1800, "lat_inicio": None, "lng_inicio": None}
    for nome, dados in (("contacts.json", contatos), ("alerts.json", alertas),
                        ("historico_viagens.json", historico), ("viagem.json", viagem)):
        with open(os.path.join(pasta, nome), "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False)
    return pasta


def _hashes(pasta):
    return {n: hashlib.sha256(open(os.path.join(pasta, n), "rb").read()).hexdigest()
            for n in ("contacts.json", "alerts.json", "historico_viagens.json", "viagem.json")}


def test_validacao_aponta_invalidos_e_duplicados_sem_dados_pessoais(tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"))
    v = importar_json.validar(pasta)
    assert v["resumo"]["contatos"] == {"lidos": 5, "validos": 2}
    assert v["resumo"]["alertas"] == {"lidos": 5, "validos": 3}
    texto = " ".join(v["problemas"])
    assert "duplicado" in texto and "telefone inválido" in texto and "data inválida" in texto
    assert "coordenadas inválidas" in texto
    for dado_pessoal in ("Ana Contato", "61999990001", "Tentativa de assalto", "Beto"):
        assert dado_pessoal not in texto


def test_simulacao_nao_grava_nada(pg_url, repo, tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"))
    antes = _hashes(pasta)
    login = _login_unico("imp")
    conn = psycopg2.connect(pg_url)
    try:
        rel = importar_json.importar(conn, pasta, login, "senha-forte-123", executar=False)
    finally:
        conn.close()
    assert rel["modo"] == "simulacao"
    assert repo.buscar_usuario(login) is None
    assert _hashes(pasta) == antes and os.listdir(pasta) == sorted(os.listdir(pasta)) and \
        not [d for d in os.listdir(pasta) if d.startswith("backup_")]


def test_importacao_completa_backup_e_idempotencia(pg, pg_url, repo, tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"))
    antes = _hashes(pasta)
    login, login_p = _login_unico("imp"), _login_unico("impp")
    conn = psycopg2.connect(pg_url)
    try:
        rel = importar_json.importar(conn, pasta, login, "senha-forte-123", "Motorista Importado",
                                     login_p, "senha-painel-123", executar=True)
        assert rel["importados"] == {"contatos": 2, "alertas": 3, "viagens": 2}
        rel2 = importar_json.importar(conn, pasta, login, None, login_painel=login_p, executar=True)
        assert rel2["importados"] == {"contatos": 0, "alertas": 0, "viagens": 0}
    finally:
        conn.close()
    # JSONs originais intactos; backup com manifesto que confere
    assert _hashes(pasta) == antes
    manifesto = json.load(open(os.path.join(rel["backup"], "MANIFESTO_SHA256.json")))
    assert manifesto == antes
    assert os.path.exists(os.path.join(rel["backup"], "RELATORIO_IMPORTACAO.json"))
    # dados visíveis pelo app, com o login importado
    mid = rel["motorista_id"]
    c = novo_cliente()
    entrar(c, "motorista", SimpleCtx(login=login, senha="senha-forte-123"))
    assert "Ana Contato" in c.get("/cadastro").get_data(as_text=True)
    pc = novo_cliente()
    entrar(pc, "painel", SimpleCtx(login=login_p, senha="senha-painel-123"))
    ocorr = [a["ocorrencia"] for a in pc.get("/api/alerts").get_json()]
    assert ocorr == ["Tentativa de assalto", "Acidente", "GPS ruim"]
    hist = repo.listar_historico([mid])
    assert [h["resultado"] for h in hist] == ["cancelada", "chegou_segura"]   # em_andamento antigo → interrompida
    assert q(repo, "SELECT count(*) AS n FROM importacoes WHERE motorista_id = %s", mid)[0]["n"] >= 4


def test_falha_no_meio_da_importacao_nao_grava_nada(pg_url, repo, tmp_path, monkeypatch):
    pasta = _dados_legados(str(tmp_path / "data"))
    login = _login_unico("imp")
    def falhar(*a, **k):
        raise RuntimeError("falha simulada no meio da importação")
    monkeypatch.setattr(importar_json, "_inserir_alertas", falhar)
    conn = psycopg2.connect(pg_url)
    try:
        with pytest.raises(RuntimeError, match="falha simulada"):
            importar_json.importar(conn, pasta, login, "senha-forte-123", executar=True)
    finally:
        conn.close()
    assert repo.buscar_usuario(login) is None          # rollback: nem o usuário ficou
    assert not q(repo, "SELECT 1 FROM contatos_confianca WHERE nome = 'Ana Contato' AND telefone = '61999990001' "
                       "AND motorista_id NOT IN (SELECT motorista_id FROM importacoes)")


def test_importacao_de_viagem_ativa_expirada_dispara_dms(pg, pg_url, repo, tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"), ativa_expirada=True)
    login = _login_unico("imp")
    conn = psycopg2.connect(pg_url)
    try:
        rel = importar_json.importar(conn, pasta, login, "senha-forte-123", executar=True)
    finally:
        conn.close()
    mid = rel["motorista_id"]
    assert repo.status_viagem([mid])["ativa"] is True
    app_module.tarefa_dms()
    assert repo.status_viagem([mid]) == {"ativa": False}
    assert any(a["tipo"] == "dead_mans_switch" for a in repo.listar_alertas([mid]))


def test_importacao_recusa_login_com_outro_papel(pg_url, repo, tmp_path):
    p = criar_painel(repo)
    pasta = _dados_legados(str(tmp_path / "data"))
    conn = psycopg2.connect(pg_url)
    try:
        with pytest.raises(ValueError, match="outro papel"):
            importar_json.importar(conn, pasta, p.login, "senha-forte-123", executar=True)
    finally:
        conn.close()


# ═════════════ Rollback PostgreSQL → JSON ═════════════

def test_rollback_reconstroi_jsons_e_inclui_dados_novos(pg, pg_url, repo, tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"))
    login = _login_unico("imp")
    conn = psycopg2.connect(pg_url)
    try:
        rel = importar_json.importar(conn, pasta, login, "senha-forte-123", executar=True)
        mid = rel["motorista_id"]
        repo.registrar_alerta(mid, {"tipo": "manual", "motorista": "Motorista", "ocorrencia": "Depois da migração"})
        arquivos = rollback_pg_para_json.exportar_formato_json(conn, mid)
    finally:
        conn.close()
    assert [c["telefone"] for c in arquivos["contacts.json"]] == ["61999990001", "61999990003"]
    ocorr = [a["ocorrencia"] for a in arquivos["alerts.json"]]
    assert ocorr == ["Tentativa de assalto", "Acidente", "GPS ruim", "Depois da migração"]
    assert arquivos["alerts.json"][0]["ts"] == "23/03/2026 23:19:50"         # formato e fuso preservados
    assert arquivos["alerts.json"][1]["lat"] == "-15.780000"
    assert arquivos["viagem.json"] == {"ativa": False}
    # o modo JSON lê os arquivos gerados normalmente
    destino = rollback_pg_para_json.gravar_pasta(arquivos, str(tmp_path / "rollback"))
    from repositorio_json import RepositorioJSON, LEGADO
    rj = RepositorioJSON(destino)
    assert len(rj.listar_alertas([LEGADO])) == 4 and len(rj.listar_contatos(LEGADO)) == 2


def test_rollback_aplicar_faz_backup_antes(pg, pg_url, repo, tmp_path):
    pasta = _dados_legados(str(tmp_path / "data"))
    antes = _hashes(pasta)
    login = _login_unico("imp")
    conn = psycopg2.connect(pg_url)
    try:
        mid = importar_json.importar(conn, pasta, login, "senha-forte-123", executar=True)["motorista_id"]
        arquivos = rollback_pg_para_json.exportar_formato_json(conn, mid)
    finally:
        conn.close()
    backup = rollback_pg_para_json.aplicar_em_data(arquivos, pasta)
    assert _hashes(backup) == antes                 # versão anterior preservada
    assert json.load(open(os.path.join(pasta, "contacts.json"), encoding="utf-8")) == arquivos["contacts.json"]


def test_rollback_nunca_sobrescreve_pasta_existente(tmp_path):
    (tmp_path / "existe").mkdir()
    with pytest.raises(FileExistsError):
        rollback_pg_para_json.gravar_pasta({"contacts.json": []}, str(tmp_path / "existe"))
