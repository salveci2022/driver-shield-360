"""
DRIVER-SHIELD-360 PREMIUM v3.0
Sistema de Segurança para Motoristas
Correções: DMS persistente, proteção API, checklist, histórico viagens

Armazenamento (STORAGE_BACKEND):
  json      — padrão; um único motorista; arquivos em data/ (também é o caminho de rollback)
  postgres  — multiusuário com isolamento por motorista (DATABASE_URL)
"""
from flask import (Flask, render_template, request, jsonify, redirect, url_for, flash, session,
                   send_file, g, has_request_context, Response)
from datetime import datetime, timezone, timedelta
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
import json, os, urllib.request, urllib.error, hmac, secrets, math
from armazenamento import ler_json, gravar_json
from seguranca_db import SegurancaDB
from repositorio_json import RepositorioJSON, LEGADO
from agendador import Agendador

# ══════════════════════════════════════════
# ARMAZENAMENTO
# ══════════════════════════════════════════
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "json").strip().lower()
DATABASE_URL    = os.environ.get("DATABASE_URL", "").strip()
if STORAGE_BACKEND not in ("json", "postgres"):
    raise RuntimeError("STORAGE_BACKEND deve ser 'json' ou 'postgres'")
if STORAGE_BACKEND == "postgres" and not DATABASE_URL:
    raise RuntimeError("STORAGE_BACKEND=postgres exige DATABASE_URL")

# ══════════════════════════════════════════
# SECRETS — somente via variáveis de ambiente
# Em produção, a ausência de um secret obrigatório impede a inicialização.
# DS360_DEV=1 (apenas desenvolvimento local) gera valores aleatórios.
# ══════════════════════════════════════════
DEV_MODE = os.environ.get("DS360_DEV") == "1"

# No PostgreSQL cada usuário tem a própria senha (hash no banco): as senhas
# compartilhadas do ambiente só são exigidas no modo JSON.
SECRETS_OBRIGATORIOS = ("SECRET_KEY", "API_SECRET") + (
    ("PAINEL_SENHA", "MOTORISTA_SENHA") if STORAGE_BACKEND == "json" else ())

# Valores que já estiveram no código ou na documentação: nunca aceitos.
VALORES_INSEGUROS = {
    "driver-shield-360-premium-2026", "shield360", "ds360-api-2026",
    "MinhasSenha@2026", "shield360-prod-CHAVE_ALEATORIA_AQUI",
    "flask-secret-OUTRA_CHAVE_ALEATORIA",
}

def _carregar_secrets():
    valores, ausentes, inseguros = {}, [], []
    for nome in SECRETS_OBRIGATORIOS:
        valor = os.environ.get(nome, "").strip()
        if not valor:
            ausentes.append(nome)
        elif valor in VALORES_INSEGUROS:
            inseguros.append(nome)
        valores[nome] = valor
    if DEV_MODE:
        for nome in ausentes + inseguros:
            valores[nome] = secrets.token_urlsafe(24)
        if ausentes or inseguros:
            print(f"[CONFIG] AVISO: DS360_DEV=1 - valores aleatorios gerados para: {', '.join(ausentes + inseguros)}")
        return valores
    if ausentes or inseguros:
        partes = []
        if ausentes:
            partes.append(f"ausentes: {', '.join(ausentes)}")
        if inseguros:
            partes.append(f"com valor padrão inseguro: {', '.join(inseguros)}")
        raise RuntimeError(
            "Configuração insegura — variáveis de ambiente obrigatórias "
            + "; ".join(partes)
            + ". Configure-as antes de iniciar a aplicação."
        )
    return valores

_SECRETS = _carregar_secrets()

app = Flask(__name__)
app.secret_key = _SECRETS["SECRET_KEY"]
app.config.update(
    # __Host-: o navegador só aceita o cookie via HTTPS, no domínio exato e com Path=/
    SESSION_COOKIE_NAME="ds360_sessao" if DEV_MODE else "__Host-ds360_sessao",
    SESSION_COOKIE_HTTPONLY=True,
    # Lax (e não Strict): links do WhatsApp para o painel precisam manter a sessão;
    # POSTs cross-site continuam sem cookie e, além disso, exigem token CSRF.
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEV_MODE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=64 * 1024,
)
# Render fica atrás de UM proxy: usa X-Forwarded-For/Proto (o último valor, inserido pelo proxy)
# para IP real e HTTPS. Sem proxy na frente, use DS360_PROXY_HOPS=0 — caso contrário um cliente
# poderia forjar X-Forwarded-For para escapar do limite de tentativas de login.
PROXY_HOPS = int(os.environ.get("DS360_PROXY_HOPS", "1"))
if PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS, x_proto=PROXY_HOPS)

BR_TZ = timezone(timedelta(hours=-3))

DATA_DIR        = os.environ.get("DS360_DATA_DIR") or os.path.join(os.path.dirname(__file__), "data")
CONTACTS_FILE   = os.path.join(DATA_DIR, "contacts.json")
ALERTS_FILE     = os.path.join(DATA_DIR, "alerts.json")
VIAGEM_FILE     = os.path.join(DATA_DIR, "viagem.json")
HISTORICO_FILE  = os.path.join(DATA_DIR, "historico_viagens.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════
# CONFIGURAÇÕES
# ══════════════════════════════════════════
ZAPI_INSTANCE   = os.environ.get("ZAPI_INSTANCE", "")
ZAPI_TOKEN      = os.environ.get("ZAPI_TOKEN", "")
ZAPI_CLIENT_TKN = os.environ.get("ZAPI_CLIENT_TOKEN", "")
# Endereço público usado no link do WhatsApp (homologação aponta para o próprio staging)
URL_PUBLICA     = os.environ.get("URL_PUBLICA", "https://driver-shield-360.onrender.com").strip().rstrip("/")
if not (URL_PUBLICA.startswith("https://") or DEV_MODE):
    raise RuntimeError("URL_PUBLICA deve começar com https://")
PAINEL_SENHA    = _SECRETS.get("PAINEL_SENHA", "")
MOTORISTA_SENHA = _SECRETS.get("MOTORISTA_SENHA", "")
API_SECRET      = _SECRETS["API_SECRET"]  # apenas integrações servidor-a-servidor; nunca enviado ao navegador

PAINEL_TTL    = 12 * 3600        # sessão do painel: 12h (absoluta)
MOTORISTA_TTL = 30 * 24 * 3600   # sessão do motorista: 30 dias (renovada a cada uso)

# Shield AI — a chave fica SOMENTE no servidor
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
IA_MODELO         = os.environ.get("IA_MODELO", "claude-opus-5-5")
IA_LIMITE         = 20          # pedidos por motorista/IP…
IA_JANELA         = 10 * 60     # …a cada 10 minutos

def _criar_armazenamento():
    """Repositório de dados + estado de segurança (sessões, rate limit) compartilhados entre workers."""
    if STORAGE_BACKEND == "postgres":
        import psycopg2, migrar
        from repositorio_pg import RepositorioPG
        from seguranca_db import SegurancaPG
        conn = psycopg2.connect(DATABASE_URL)
        try:
            st = migrar.status(conn)
        finally:
            conn.close()
        if st["pendentes"] or st["alteradas_apos_aplicar"]:
            raise RuntimeError("Banco PostgreSQL desatualizado: execute 'python migrar.py aplicar' antes de iniciar.")
        repo = RepositorioPG(DATABASE_URL)
        return repo, SegurancaPG(repo, _SECRETS["SECRET_KEY"])
    return (RepositorioJSON(DATA_DIR),
            SegurancaDB(os.path.join(DATA_DIR, "seguranca.db"), _SECRETS["SECRET_KEY"]))

REPO, SEG_DB = _criar_armazenamento()

# ══════════════════════════════════════════
# PERSISTÊNCIA JSON (modo legado — leitura/escrita direta dos arquivos; usado por
# ferramentas e testes. As rotas usam sempre REPO.)
# ══════════════════════════════════════════
_load_json = ler_json
_save_json = gravar_json

def load_contacts():  return _load_json(CONTACTS_FILE, [])
def save_contacts(c): _save_json(CONTACTS_FILE, c)
def load_alerts():    return _load_json(ALERTS_FILE, [])
def save_alerts(a):   _save_json(ALERTS_FILE, a)
def load_viagem():    return _load_json(VIAGEM_FILE, {"ativa": False})
def save_viagem(v):   _save_json(VIAGEM_FILE, v)
def load_historico(): return _load_json(HISTORICO_FILE, [])
def save_historico(h): _save_json(HISTORICO_FILE, h)

def now_br_str():
    return datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")

def now_ts():
    return datetime.now(timezone.utc).timestamp()

# ══════════════════════════════════════════
# SEGURANÇA — utilitários
# ══════════════════════════════════════════
def _iguais(a, b):
    """Comparação em tempo constante."""
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))

def mascarar_telefone(numero):
    digitos = "".join(filter(str.isdigit, str(numero or "")))
    if len(digitos) <= 6:
        return "***"
    return f"{digitos[:2]}{'*' * (len(digitos) - 6)}{digitos[-4:]}"

def _texto(valor, padrao, maximo):
    if not isinstance(valor, (str, int, float)):
        return padrao
    s = " ".join(str(valor).split())[:maximo]
    return s or padrao

def _coord(valor, limite):
    """Aceita apenas coordenadas numéricas válidas; mantém o formato string usado no histórico."""
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or abs(f) > limite:
        return None
    return f"{f:.6f}"

def _destino_seguro(destino, padrao):
    """Evita open redirect: apenas caminhos locais."""
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return padrao

def _id_inteiro(valor):
    """Ids vindos do cliente: apenas inteiros não negativos (bool não é aceito)."""
    if isinstance(valor, bool):
        return None
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None

# ── CSRF ─────────────────────────────────
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok

app.jinja_env.globals["csrf_token"] = csrf_token

@app.context_processor
def _contexto_templates():
    return {"multiusuario": REPO.multiusuario}

def _csrf_valido():
    esperado = session.get("_csrf")
    enviado = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    return bool(esperado) and bool(enviado) and _iguais(enviado, esperado)

# ── Sessões por papel (revogáveis no servidor — seguranca_db.py) ──
# O cookie guarda apenas "<papel>_sid"; validade, expiração, usuário e motorista ficam no servidor.
SENHA_POR_PAPEL = {"painel": PAINEL_SENHA, "motorista": MOTORISTA_SENHA}
TTL_POR_PAPEL   = {"painel": PAINEL_TTL, "motorista": MOTORISTA_TTL}
# Modo JSON: trocar a senha no ambiente invalida todas as sessões daquele papel.
# Modo PostgreSQL: trocar a senha do usuário (ou desativá-lo) invalida as sessões dele.
_SENHA_FP = {p: SEG_DB.impressao_senha(s) for p, s in SENHA_POR_PAPEL.items() if s}

def _senha_fp(papel):
    return "" if REPO.multiusuario else _SENHA_FP.get(papel, "")

def _info_sessao(papel):
    cache = g.setdefault("_sessoes", {})
    if papel not in cache:
        cache[papel] = SEG_DB.sessao_valida(session.get(f"{papel}_sid"), papel, _senha_fp(papel))
    return cache[papel]

def _expiracao_sessao(papel):
    info = _info_sessao(papel)
    return info["expira"] if info else None

def _papel_ativo(papel):
    return _info_sessao(papel) is not None

def _iniciar_sessao(papel, usuario_id=None, motorista_id=None):
    """Login: descarta a sessão anterior (anti session fixation), preservando outro papel ativo."""
    SEG_DB.revogar_sessao(session.get(f"{papel}_sid"))
    outros = {k: v for k, v in session.items() if k.endswith("_sid") and k != f"{papel}_sid"}
    session.clear()
    session.update(outros)
    session.permanent = True
    session[f"{papel}_sid"] = SEG_DB.criar_sessao(papel, TTL_POR_PAPEL[papel], _senha_fp(papel),
                                                  usuario_id, motorista_id)
    g.pop("_sessoes", None)

def _encerrar_sessao(papel):
    SEG_DB.revogar_sessao(session.pop(f"{papel}_sid", None))
    g.pop("_sessoes", None)

def _renovar_motorista():
    # Sessão deslizante: motorista ativo nunca perde acesso ao SOS (renova no máx. 1x/hora)
    expira = _expiracao_sessao("motorista")
    if expira is not None and expira - now_ts() < MOTORISTA_TTL - 3600:
        SEG_DB.renovar_sessao(session["motorista_sid"], MOTORISTA_TTL)

# ── Contexto de autorização (anti-IDOR/BOLA) ─────
# O motorista de uma operação vem SEMPRE da sessão (ou do token de servidor + cabeçalho
# explícito), nunca de um campo enviado pelo navegador.
def _int_ou_none(v):
    return int(v) if v is not None else None

def motorista_da_sessao():
    info = _info_sessao("motorista")
    if not info:
        return None
    return _int_ou_none(info.get("motorista_id")) if REPO.multiusuario else LEGADO

def usuario_da_sessao():
    for papel in ("motorista", "painel"):
        info = _info_sessao(papel)
        if info and info.get("usuario_id") is not None:
            return _int_ou_none(info["usuario_id"])
    return None

def motoristas_do_painel_sessao():
    info = _info_sessao("painel")
    if not info:
        return []
    return REPO.motoristas_do_painel(_int_ou_none(info.get("usuario_id")) if REPO.multiusuario else None)

def motoristas_visiveis():
    """Motoristas cujos dados a sessão atual pode ler: o próprio (motorista) + os vinculados (painel)."""
    ids = []
    proprio = motorista_da_sessao()
    if proprio is not None:
        ids.append(proprio)
    for m in motoristas_do_painel_sessao():
        if m not in ids:
            ids.append(m)
    return ids

class _MotoristaInvalido(Exception):
    pass

def motorista_alvo():
    """Motorista de uma operação de escrita do motorista (SOS, viagem, contato...)."""
    if g.get("via_token"):
        if not REPO.multiusuario:
            return LEGADO
        mid = _id_inteiro(request.headers.get("X-Shield-Motorista"))
        if mid is None or not REPO.motorista_existe(mid):
            raise _MotoristaInvalido()
        return mid
    return motorista_da_sessao()

@app.errorhandler(_MotoristaInvalido)
def _erro_motorista_invalido(_e):
    return jsonify({"ok": False, "erro": "Cabeçalho X-Shield-Motorista ausente ou inválido"}), 400

# ── Auditoria (sem senhas, tokens ou conteúdo sensível) ──
def auditar(acao, motorista_id=None, alvo=None, **detalhes):
    try:
        if has_request_context():
            usuario, ip_hash = usuario_da_sessao(), SEG_DB._h(_ip_cliente())[:16]
        else:
            usuario, ip_hash = None, None
        REPO.auditar(acao, usuario_id=usuario, motorista_id=motorista_id, alvo=alvo,
                     ip_hash=ip_hash, detalhes=detalhes)
    except Exception as e:   # auditoria nunca pode derrubar o SOS
        print(f"[AUDITORIA] Falha ao registrar '{acao}': {type(e).__name__}")

# ── Rate limit de login (compartilhado entre workers) ─
LOGIN_MAX_FALHAS       = 5     # por IP
LOGIN_MAX_FALHAS_CONTA = 20    # por conta (multiusuário): freia credential stuffing distribuído
LOGIN_JANELA           = 15 * 60

def _ip_cliente():
    return request.remote_addr or "?"

# ══════════════════════════════════════════
# AUTORIZAÇÃO
# ══════════════════════════════════════════
def _token_servidor_valido():
    """X-Shield-Token / _token: apenas para integrações servidor-a-servidor (não usa cookie → sem CSRF)."""
    token = request.headers.get("X-Shield-Token")
    if not token:
        corpo = request.get_json(force=True, silent=True)
        token = corpo.get("_token") if isinstance(corpo, dict) else None
    return bool(token) and _iguais(token, API_SECRET)

def api_auth(*papeis, aceita_token=False):
    """Protege APIs: exige sessão de um dos papéis ou token de servidor. (CSRF: ver protecao_csrf)"""
    def deco(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if aceita_token and _token_servidor_valido():
                g.via_token = True
                return f(*args, **kwargs)
            if not any(_papel_ativo(p) for p in papeis):
                return jsonify({"ok": False, "erro": "Não autorizado", "login": True}), 401
            if "motorista" in papeis and not any(_papel_ativo(p) for p in papeis if p != "motorista") \
                    and motorista_da_sessao() is None:
                return jsonify({"ok": False, "erro": "Não autorizado", "login": True}), 401
            _renovar_motorista()
            return f(*args, **kwargs)
        decorated.aceita_token = aceita_token
        return decorated
    return deco

def pagina_auth(*papeis):
    """Protege páginas: redireciona para o login do primeiro papel. (CSRF: ver protecao_csrf)"""
    login_por_papel = {"painel": "painel_login", "motorista": "motorista_login"}
    def deco(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not any(_papel_ativo(p) for p in papeis):
                return redirect(url_for(login_por_papel[papeis[0]], next=request.path))
            _renovar_motorista()
            return f(*args, **kwargs)
        return decorated
    return deco

# ══════════════════════════════════════════
# CSRF GLOBAL — vale para TODA requisição que altera estado, inclusive rotas futuras.
# Exceções: rotas marcadas com aceita_token, quando chamadas com o token de servidor
# (sem cookie de sessão, portanto sem risco de CSRF), e o relatório de CSP.
# ══════════════════════════════════════════
METODOS_SEGUROS = ("GET", "HEAD", "OPTIONS")

def _origem_valida():
    origem = request.headers.get("Origin")
    if not origem:
        return True   # navegadores antigos/clients sem Origin: o token CSRF continua obrigatório
    return origem == request.host_url.rstrip("/")

@app.before_request
def protecao_csrf():
    if request.method in METODOS_SEGUROS:
        return None
    view = app.view_functions.get(request.endpoint)
    if getattr(view, "csrf_isento", False):
        return None   # apenas relatórios de CSP do navegador (não alteram dados)
    if getattr(view, "aceita_token", False) and _token_servidor_valido():
        return None
    if _origem_valida() and _csrf_valido():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "erro": "Token CSRF inválido", "csrf": True}), 403
    flash("Sessão expirada. Tente novamente.", "erro")
    return redirect(request.full_path.rstrip("?"))

# ══════════════════════════════════════════
# HEADERS DE SEGURANÇA + CSP
# Nenhum script nem estilo inline: JS em /static/js e CSS em /static/css.
# ══════════════════════════════════════════
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "media-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
    "report-uri /api/csp-report",
] + ([] if DEV_MODE else ["upgrade-insecure-requests"]))

@app.after_request
def headers_seguranca(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    if not DEV_MODE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Dados pessoais (contatos, GPS, alertas) não devem ficar no cache HTTP
    p = request.path
    if p.startswith(("/api/", "/painel", "/relatorio", "/cadastro", "/historico", "/motorista", "/saude")):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# ══════════════════════════════════════════
# WHATSAPP Z-API
# ══════════════════════════════════════════
def enviar_whatsapp(numero, mensagem):
    if not ZAPI_INSTANCE or not ZAPI_TOKEN or not numero:
        print(f"[ZAPI] Ignorado — não configurado ou número vazio ({mascarar_telefone(numero)})")
        return False
    try:
        numero_limpo = "".join(filter(str.isdigit, numero))
        url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}/send-text"
        payload = json.dumps({"phone": numero_limpo, "message": mensagem}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Client-Token": ZAPI_CLIENT_TKN,
        }
        req  = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        resp.read()
        print(f"[ZAPI] ✅ Enviado para {mascarar_telefone(numero_limpo)} (HTTP {resp.status})")
        return True
    except urllib.error.HTTPError as e:
        print(f"[ZAPI] ❌ Erro ao enviar para {mascarar_telefone(numero)}: HTTP {e.code}")
        return False
    except Exception as e:
        # Nunca logar a URL: ela contém o token da instância
        print(f"[ZAPI] ❌ Erro ao enviar para {mascarar_telefone(numero)}: {type(e).__name__}")
        return False

def notificar_contatos(motorista_id, alerta, prefixo="🚨 ALERTA"):
    """Avisa SOMENTE os contatos de confiança do próprio motorista do alerta."""
    contacts = REPO.listar_contatos(motorista_id)
    motorista = alerta.get("motorista", "Motorista")
    ocorrencia = alerta.get("ocorrencia", "Emergência")
    ts = alerta.get("ts", "")
    lat = alerta.get("lat")
    lng = alerta.get("lng")

    maps_link = f"\n📍 *Localização:* https://www.google.com/maps?q={lat},{lng}&z=16" if lat and lng \
                else "\n📍 _Localização não disponível_"

    msg = (
        f"{prefixo} *DRIVER-SHIELD-360*\n\n"
        f"🚕 Motorista: *{motorista}*\n"
        f"⚠️ Ocorrência: *{ocorrencia}*\n"
        f"⏰ Horário: {ts}"
        f"{maps_link}\n\n"
        f"Acesse o painel: {URL_PUBLICA}/painel/login"
    )
    for c in contacts:
        numero = c.get("telefone", "")
        if numero:
            enviar_whatsapp(numero, msg)

# ══════════════════════════════════════════
# DEAD MAN'S SWITCH + AGENDADOR CENTRAL (sem threading.Timer)
# - A expiração fica no armazenamento (viagem.json / tabela viagens).
# - O disparo é atômico: só um worker/instância dispara cada viagem, e uma viagem
#   antiga (confirmada, cancelada ou substituída) nunca dispara.
# - Quem dispara: o agendador (thread embutida com lease, ou processo dedicado
#   `python agendador.py`) e, como rede de segurança, qualquer requisição (no máx. 1x/30s).
# ══════════════════════════════════════════
AGENDADOR_EMBUTIDO = os.environ.get("AGENDADOR_EMBUTIDO", "1") == "1"
AGENDADOR = Agendador(lambda nome, ttl, executor: REPO.adquirir_tarefa(nome, ttl, executor))
_dms_ultima_verificacao = 0.0

def registrar_alerta(alerta):
    """Compatibilidade (modo JSON): registra um alerta do motorista legado."""
    return REPO.registrar_alerta(LEGADO, alerta)

def _notificar_disparos(disparos):
    for motorista_id, alerta in disparos:
        try:
            notificar_contatos(motorista_id, alerta, prefixo="⏰ ALERTA AUTOMÁTICO")
        except Exception as e:
            print(f"[DMS] Erro na notificação: {type(e).__name__}")
        auditar("dms_disparado", motorista_id=motorista_id)
        print("[DMS] Disparado")

@AGENDADOR.tarefa("dead_mans_switch", intervalo=10)
def tarefa_dms():
    disparos = REPO.disparar_viagens_expiradas()
    _notificar_disparos(disparos)
    return len(disparos)

@AGENDADOR.tarefa("limpeza_seguranca", intervalo=600)
def tarefa_limpeza_seguranca():
    SEG_DB.limpar_expirados()

def _disparar_dms(viagem_id=None):
    """Dispara uma viagem específica (ou a atual, no modo JSON) se já expirou. Idempotente."""
    r = REPO.disparar_viagem(viagem_id)
    if not r:
        return False
    _notificar_disparos([r])
    return True

def cancelar_dms():
    """Compatibilidade: não existe mais timer em memória (o agendador é central)."""
    return None

def verificar_dms_ao_iniciar():
    """Ao (re)iniciar, dispara SOMENTE viagens que já expiraram (e ainda estão em andamento).
    Com AGENDADOR_EMBUTIDO=0 o serviço web não faz isso: é tarefa exclusiva do worker dedicado."""
    if not AGENDADOR_EMBUTIDO:
        return
    try:
        tarefa_dms()
    except Exception as e:
        print(f"[DMS] Verificação inicial falhou: {type(e).__name__}")

@app.before_request
def verificar_dms_periodico():
    """Modo serviço único (AGENDADOR_EMBUTIDO=1): thread do agendador + verificação de segurança.
    Modo com worker dedicado (AGENDADOR_EMBUTIDO=0): o web NÃO executa nenhuma tarefa agendada."""
    global _dms_ultima_verificacao
    if not AGENDADOR_EMBUTIDO:
        return None
    AGENDADOR.garantir_thread()
    agora = now_ts()
    if agora - _dms_ultima_verificacao < 30:
        return None
    _dms_ultima_verificacao = agora
    try:
        tarefa_dms()
    except Exception as e:
        print(f"[DMS] Erro na verificação periódica: {type(e).__name__}")
    return None

# ══════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════
login_required = pagina_auth("painel")

def _autenticar(tipo, login, senha):
    """Retorna {usuario_id, motorista_id} ou None."""
    if REPO.multiusuario:
        return REPO.autenticar(login, senha, tipo) if login else None
    if _iguais(senha, SENHA_POR_PAPEL[tipo]):
        return {"usuario_id": None, "motorista_id": None}
    return None

def _processar_login(tipo, template, destino_padrao):
    """Login comum a painel e motorista. CSRF já validado em protecao_csrf.
    Rate limit compartilhado: cada tentativa é reservada atomicamente; sucesso zera o contador."""
    destino = _destino_seguro(request.args.get("next"), destino_padrao)
    if request.method == "POST":
        escopo, ip = f"login:{tipo}", _ip_cliente()
        login = _texto(request.form.get("usuario"), "", 80).lower()
        bloqueado = not SEG_DB.consumir(escopo, ip, LOGIN_MAX_FALHAS, LOGIN_JANELA)
        if not bloqueado and REPO.multiusuario and login:
            bloqueado = not SEG_DB.consumir(f"login-conta:{tipo}", login, LOGIN_MAX_FALHAS_CONTA, LOGIN_JANELA)
        if bloqueado:
            print(f"[LOGIN] Bloqueado por excesso de tentativas ({tipo})")
            auditar("login_bloqueado", papel=tipo)
            flash("Muitas tentativas. Aguarde 15 minutos e tente novamente.", "erro")
            return render_template(template, destino=destino), 429
        conta = _autenticar(tipo, login, request.form.get("senha", ""))
        if conta:
            SEG_DB.limpar(escopo, ip)
            if login:
                SEG_DB.limpar(f"login-conta:{tipo}", login)
            _iniciar_sessao(tipo, conta["usuario_id"], conta["motorista_id"])
            auditar("login_ok", motorista_id=conta["motorista_id"], papel=tipo)
            return redirect(destino)
        print(f"[LOGIN] Falha de login ({tipo})")
        auditar("login_falha", papel=tipo)
        flash("Usuário ou senha incorretos." if REPO.multiusuario else "Senha incorreta.", "erro")
        return render_template(template, destino=destino), 401
    return render_template(template, destino=destino)

def _logout(papel, destino):
    """Logout só por POST + CSRF (evita logout forçado por terceiros) e revoga a sessão no servidor."""
    if request.method == "GET":
        return redirect(url_for(papel))
    auditar("logout", papel=papel)
    _encerrar_sessao(papel)
    resp = redirect(url_for(destino))
    resp.headers["Clear-Site-Data"] = '"cache"'
    return resp

# ══════════════════════════════════════════
# ROTAS PRINCIPAIS
# ══════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/motorista/login", methods=["GET", "POST"])
def motorista_login():
    return _processar_login("motorista", "motorista_login.html", url_for("motorista"))

@app.route("/motorista/logout", methods=["GET", "POST"])
def motorista_logout():
    return _logout("motorista", "motorista_login")

@app.route("/motorista")
@pagina_auth("motorista")
def motorista():
    return render_template("motorista.html", contacts=REPO.listar_contatos(motorista_da_sessao()))

@app.route("/cadastro", methods=["GET", "POST"])
@pagina_auth("motorista")
def cadastro():
    mid = motorista_da_sessao()
    if request.method == "POST":
        acao = request.form.get("acao", "add")
        if acao == "delete":
            contato_id = _id_inteiro(request.form.get("idx"))
            if contato_id is None:
                flash("Não foi possível remover.", "erro")
            elif REPO.remover_contato(mid, contato_id):
                flash("Contato removido.", "ok")
                auditar("contato_removido", motorista_id=mid)
            else:
                flash("Contato não encontrado.", "erro")
            return redirect(url_for("cadastro"))
        nome     = _texto(request.form.get("nome"), "", 60)
        telefone = _texto(request.form.get("telefone"), "", 20)
        relacao  = _texto(request.form.get("relacao"), "Contato", 30)
        if not nome or not telefone:
            flash("Preencha nome e telefone.", "erro")
        else:
            r = REPO.adicionar_contato(mid, nome, telefone, relacao)
            if r == "ok":
                flash("Contato cadastrado com sucesso!", "ok")
                auditar("contato_adicionado", motorista_id=mid)
            elif r == "limite":
                flash("Limite atingido: máximo 5 contatos.", "erro")
            elif r == "duplicado":
                flash("Esse telefone já está cadastrado.", "erro")
            else:
                flash("Não foi possível cadastrar.", "erro")
        return redirect(url_for("cadastro"))
    return render_template("cadastro.html", contacts=REPO.listar_contatos(mid))

@app.route("/checklist")
def checklist():
    return render_template("checklist.html")

@app.route("/historico")
@pagina_auth("motorista", "painel")
def historico_viagens():
    return render_template("historico.html", viagens=REPO.listar_historico(motoristas_visiveis()))

# ── Painel ──────────────────────────────
@app.route("/painel/login", methods=["GET", "POST"])
def painel_login():
    return _processar_login("painel", "painel_login.html", url_for("painel"))

@app.route("/painel/logout", methods=["GET", "POST"])
def painel_logout():
    return _logout("painel", "painel_login")

@app.route("/painel")
@login_required
def painel():
    return render_template("painel.html")

@app.route("/relatorio")
@login_required
def relatorio():
    alerts = list(reversed(REPO.listar_alertas(motoristas_do_painel_sessao())))
    return render_template("relatorio.html", alerts=alerts)

@app.route("/ia")
@pagina_auth("motorista")
def ia_seguranca():
    return render_template("ia_seguranca.html")

@app.route("/login")
def login():
    return redirect(url_for("painel_login"))

@app.route("/saude")
def saude():
    """Health check do Render: confirma que o armazenamento responde. Não expõe nenhum detalhe."""
    try:
        REPO.verificar_saude()
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[SAUDE] Armazenamento indisponível: {type(e).__name__}")
        return jsonify({"ok": False}), 503

@app.route("/pessoa_sair")
def pessoa_sair():
    return render_template("pessoa_sair.html")

# ══════════════════════════════════════════
# PWA
# ══════════════════════════════════════════
@app.route("/manifest.json")
def manifest():
    m = {
        "name": "Driver Shield 360",
        "short_name": "Shield 360",
        "description": "Segurança para motoristas de aplicativo",
        "start_url": "/motorista",
        "display": "standalone",
        "background_color": "#0a0e1a",
        "theme_color": "#0088ff",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    return Response(json.dumps(m), mimetype="application/json")

@app.route("/sw.js")
def service_worker():
    # Network-first: páginas autenticadas nunca são servidas do cache quando há rede,
    # redirecionamentos (login) nunca são cacheados e /api/ nunca passa pelo cache.
    sw = """
const CACHE = 'shield360-v5';
const PAGINAS_OFFLINE = ['/motorista', '/cadastro', '/checklist'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add('/checklist')).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  const cacheavel = PAGINAS_OFFLINE.includes(url.pathname) || url.pathname.startsWith('/static/');
  e.respondWith(
    fetch(req).then(resp => {
      if (cacheavel && resp.ok && !resp.redirected && resp.type === 'basic') {
        const copia = resp.clone();
        caches.open(CACHE).then(c => c.put(req, copia));
      }
      return resp;
    }).catch(() => caches.match(req))
  );
});
"""
    resp = Response(sw, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# ══════════════════════════════════════════
# RELATÓRIO PDF (somente alertas dos motoristas vinculados ao painel)
# ══════════════════════════════════════════
@app.route("/relatorio.pdf")
@login_required
def relatorio_pdf():
    alerts = list(reversed(REPO.listar_alertas(motoristas_do_painel_sessao())))
    buffer = BytesIO()
    pdf = pdf_canvas.Canvas(buffer, pagesize=A4)
    larg, alt = A4

    pdf.setTitle("Relatório — Driver Shield 360")
    pdf.setFillColorRGB(0.04, 0.05, 0.10)
    pdf.rect(0, 0, larg, alt, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 0.53, 1)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(50, alt - 55, "DRIVER-SHIELD-360 PREMIUM v3.0")
    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, alt - 75, f"Relatório de Ocorrências — Gerado em: {now_br_str()}")
    pdf.setFillColorRGB(0, 0.53, 1)
    pdf.rect(50, alt - 85, larg - 100, 2, fill=1, stroke=0)

    y = alt - 115
    pdf.setFillColorRGB(0.8, 0.8, 0.9)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de ocorrências: {len(alerts)}")
    y -= 25

    for i, a in enumerate(alerts):
        if y < 80:
            pdf.showPage()
            pdf.setFillColorRGB(0.04, 0.05, 0.10)
            pdf.rect(0, 0, larg, alt, fill=1, stroke=0)
            y = alt - 50
        tipo = a.get("tipo", "")
        cor = (1, 0.4, 0) if tipo == "dead_mans_switch" else (0, 0.53, 1)
        pdf.setFillColorRGB(0.1, 0.15, 0.25)
        pdf.rect(48, y - 12, larg - 96, 60, fill=1, stroke=0)
        pdf.setFillColorRGB(*cor)
        pdf.rect(48, y + 46, larg - 96, 2, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(58, y + 32, f"#{len(alerts)-i}  {a['ts']}  —  {a['ocorrencia'][:60]}")
        pdf.setFillColorRGB(0.7, 0.8, 0.9)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(58, y + 16, f"Motorista: {a['motorista']}   |   Status: {a.get('status','—')}")
        loc = f"Lat: {a['lat']}  Lng: {a['lng']}" if a.get('lat') and a.get('lng') else "Localização não disponível"
        pdf.drawString(58, y + 2, f"Local: {loc}")
        y -= 78

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    auditar("relatorio_pdf")
    return send_file(buffer, as_attachment=True,
        download_name=f"driver_shield_360_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S')}.pdf",
        mimetype="application/pdf")

# ══════════════════════════════════════════
# APIs — PROTEGIDAS
# ══════════════════════════════════════════
def _json_body():
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}

@app.route("/api/csrf")
@api_auth("motorista", "painel")
def api_csrf():
    """Token CSRF da sessão atual (legível apenas pela própria origem)."""
    return jsonify({"csrf": csrf_token()})

@app.route("/api/contacts")
@api_auth("motorista", "painel")
def api_contacts():
    contatos = []
    for mid in motoristas_visiveis():
        contatos += [{"nome": c["nome"], "telefone": c["telefone"], "relacao": c.get("relacao", "Contato")}
                     for c in REPO.listar_contatos(mid)]
    return jsonify(contatos)

@app.route("/api/panic", methods=["POST"])
@api_auth("motorista", aceita_token=True)
def api_panic():
    mid = motorista_alvo()
    data = _json_body()
    alerta = {
        "ts":         now_br_str(),
        "motorista":  _texto(data.get("motorista"), "Motorista", 60),
        "ocorrencia": _texto(data.get("ocorrencia"), "Emergência", 120),
        "lat":        _coord(data.get("lat"), 90),
        "lng":        _coord(data.get("lng"), 180),
        "status":     "Ativo",
        "tipo":       "manual"
    }
    alerta = REPO.registrar_alerta(mid, alerta)

    # Cancela DMS se viagem estava ativa
    REPO.cancelar_viagem_por_sos(mid)

    print(f"[ALERTA] {alerta['ts']} | {alerta['tipo']} | GPS: {'sim' if alerta['lat'] else 'não'}")
    try:
        notificar_contatos(mid, alerta)
    except Exception as e:
        print(f"[ALERTA] Erro na notificação: {type(e).__name__}")
    auditar("sos", motorista_id=mid, gps=bool(alerta["lat"]))

    return jsonify({"ok": True, "alerta": alerta})

@app.route("/api/alerts")
@api_auth("painel")
def api_alerts():
    return jsonify(REPO.listar_alertas(motoristas_do_painel_sessao()))

@app.route("/api/resolve", methods=["POST"])
@api_auth("painel")
def api_resolve():
    data = _json_body()
    alerta_id = _id_inteiro(data.get("id", data.get("idx")))
    if alerta_id is None:
        return jsonify({"ok": False, "erro": "Índice inválido"}), 400
    if not REPO.resolver_alerta(motoristas_do_painel_sessao(), alerta_id, usuario_da_sessao()):
        return jsonify({"ok": False, "erro": "Alerta não encontrado"}), 404
    auditar("alerta_resolvido", alvo=str(alerta_id))
    return jsonify({"ok": True})

@app.route("/api/clear_alerts", methods=["POST"])
@api_auth("painel")
def api_clear_alerts():
    n = REPO.limpar_alertas(motoristas_do_painel_sessao())
    print("[ALERTA] Histórico de alertas limpo pelo painel (backup/arquivo preservado)")
    auditar("alertas_limpos", quantidade=n)
    return jsonify({"ok": True})

# ── Dead Man's Switch ────────────────────
@app.route("/api/viagem/iniciar", methods=["POST"])
@api_auth("motorista", aceita_token=True)
def api_viagem_iniciar():
    mid = motorista_alvo()
    data = _json_body()
    motorista = _texto(data.get("motorista"), "Motorista", 60)
    try:
        minutos = int(data.get("minutos", 30))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "Tempo entre 5 e 180 minutos."}), 400
    lat = _coord(data.get("lat"), 90)
    lng = _coord(data.get("lng"), 180)

    if minutos < 5 or minutos > 180:
        return jsonify({"ok": False, "erro": "Tempo entre 5 e 180 minutos."})

    REPO.iniciar_viagem(mid, motorista, minutos, lat, lng)
    print(f"[DMS] Viagem iniciada: {minutos} min")
    auditar("viagem_iniciada", motorista_id=mid, minutos=minutos)
    return jsonify({"ok": True, "motorista": motorista, "minutos": minutos, "segundos": minutos * 60})

@app.route("/api/viagem/confirmar", methods=["POST"])
@api_auth("motorista", aceita_token=True)
def api_viagem_confirmar():
    mid = motorista_alvo()
    REPO.encerrar_viagem(mid, "chegou_segura")
    auditar("viagem_confirmada", motorista_id=mid)
    return jsonify({"ok": True})

@app.route("/api/viagem/status")
@api_auth("motorista", "painel")
def api_viagem_status():
    return jsonify(REPO.status_viagem(motoristas_visiveis()))

@app.route("/api/viagem/cancelar", methods=["POST"])
@api_auth("motorista", aceita_token=True)
def api_viagem_cancelar():
    mid = motorista_alvo()
    REPO.encerrar_viagem(mid, "cancelada")
    auditar("viagem_cancelada", motorista_id=mid)
    return jsonify({"ok": True})

# ── Passageiro suspeito ──────────────────
@app.route("/api/passageiro_suspeito", methods=["POST"])
@api_auth("motorista", aceita_token=True)
def api_passageiro_suspeito():
    mid = motorista_alvo()
    data = _json_body()
    descricao = _texto(data.get("descricao"), "Sem descrição", 300)
    alerta = {
        "ts":         now_br_str(),
        "motorista":  _texto(data.get("motorista"), "Motorista", 60),
        "ocorrencia": f"👤 Passageiro suspeito — {descricao}",
        "lat":        _coord(data.get("lat"), 90),
        "lng":        _coord(data.get("lng"), 180),
        "status":     "Ativo",
        "tipo":       "passageiro_suspeito"
    }
    alerta = REPO.registrar_alerta(mid, alerta, descricao=descricao)
    try:
        notificar_contatos(mid, alerta, prefixo="👤 PASSAGEIRO SUSPEITO")
    except Exception as e:
        print(f"[SUSPEITO] Erro: {type(e).__name__}")
    auditar("passageiro_suspeito", motorista_id=mid)
    return jsonify({"ok": True})

# ══════════════════════════════════════════
# SHIELD AI — PROXY NO BACKEND
# A chave ANTHROPIC_API_KEY nunca sai do servidor. O prompt de sistema fica aqui
# (o navegador não consegue alterá-lo). O conteúdo das conversas não é gravado
# nem registrado em log (LGPD).
# ══════════════════════════════════════════
IA_SISTEMA = """Você é o Shield AI, assistente de segurança do Driver Shield 360, um sistema de proteção para motoristas de aplicativo, especialmente mulheres.

Seu papel é:
- Orientar motoristas em situações de risco e emergência
- Dar dicas práticas de segurança no trânsito e durante as corridas
- Ajudar a identificar comportamentos suspeitos de passageiros
- Orientar sobre como agir em caso de assalto, perseguição, acidente
- Dar suporte emocional e orientações de prevenção
- Explicar como usar o sistema Driver Shield 360

Seja sempre:
- Direto, prático e objetivo
- Empático com situações de estresse
- Claro nas instruções (use listas numeradas quando necessário)
- Nunca dê informações que possam colocar a motorista em mais risco

Se a pessoa estiver em perigo imediato, oriente primeiro a acionar o botão SOS do aplicativo e ligar 190 (Polícia Militar) ou 192 (SAMU).

Responda sempre em português brasileiro. Seja conciso mas completo."""

IA_MAX_MENSAGENS = 20
IA_MAX_CARACTERES = 2000
IA_LIMITE_GLOBAL = 300           # pedidos por dia, somando todos os usuários (teto de custo)
MSG_IA_RECUSA = ("Não posso ajudar com esse pedido. Se você estiver em perigo, "
                 "acione o SOS do aplicativo ou ligue 190.")

def _validar_mensagens_ia(mensagens):
    """Aceita apenas [{role, content:str}] alternando user/assistant, começando e terminando em user."""
    if not isinstance(mensagens, list) or not 1 <= len(mensagens) <= IA_MAX_MENSAGENS:
        return None
    limpas = []
    for i, m in enumerate(mensagens):
        if not isinstance(m, dict) or m.get("role") != ("user" if i % 2 == 0 else "assistant"):
            return None
        conteudo = m.get("content")
        if not isinstance(conteudo, str) or not conteudo.strip():
            return None
        limpas.append({"role": m["role"], "content": conteudo.strip()[:IA_MAX_CARACTERES]})
    return limpas if limpas[-1]["role"] == "user" else None

_ia_cliente = None

def _cliente_ia():
    global _ia_cliente
    if _ia_cliente is None:
        import anthropic
        _ia_cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)
    return _ia_cliente

def _chamar_ia(mensagens):
    """Retorna (texto, status_http). Nunca registra o conteúdo da conversa."""
    import anthropic
    try:
        resp = _cliente_ia().beta.messages.create(
            model=IA_MODELO,
            max_tokens=2048,          # teto de custo: respostas curtas de chat
            system=IA_SISTEMA,
            messages=mensagens,
            output_config={"effort": "low"},
            # Recusa por classificador de segurança → o próprio servidor tenta o modelo recomendado
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
    except anthropic.RateLimitError:
        return "O assistente está ocupado. Tente novamente em instantes.", 503
    except anthropic.APIStatusError as e:
        print(f"[IA] Erro da API: HTTP {e.status_code}")
        return "O assistente está indisponível no momento.", 502
    except anthropic.APIConnectionError:
        print("[IA] Falha de conexão com a API")
        return "Sem conexão com o assistente. Tente novamente.", 504
    if resp.stop_reason == "refusal":
        return MSG_IA_RECUSA, 200
    texto = "".join(b.text for b in resp.content if b.type == "text").strip()
    if resp.stop_reason == "max_tokens":
        texto += "\n\n(Resposta interrompida por tamanho — peça para continuar.)"
    return texto or "Desculpe, não consegui processar sua mensagem.", 200

@app.route("/api/ia", methods=["POST"])
@api_auth("motorista")
def api_ia():
    if not ANTHROPIC_API_KEY:
        return jsonify({"ok": False, "erro": "Shield AI não está configurado no servidor."}), 503
    mensagens = _validar_mensagens_ia(_json_body().get("mensagens"))
    if mensagens is None:
        return jsonify({"ok": False, "erro": "Mensagem inválida."}), 400
    chave = session.get("motorista_sid") or _ip_cliente()
    if not SEG_DB.consumir("ia", chave, IA_LIMITE, IA_JANELA):
        return jsonify({"ok": False, "erro": "Muitas perguntas seguidas. Aguarde alguns minutos."}), 429
    if not SEG_DB.consumir("ia-global", "todos", IA_LIMITE_GLOBAL, 24 * 3600):
        return jsonify({"ok": False, "erro": "Limite diário do assistente atingido."}), 429
    texto, status = _chamar_ia(mensagens)
    return jsonify({"ok": status == 200, "resposta" if status == 200 else "erro": texto}), status

# ══════════════════════════════════════════
# RELATÓRIOS DE CSP — o navegador avisa quando algo foi bloqueado (monitoramento).
# Isento de CSRF (o navegador não envia token), não altera dados, com rate limit
# e log mínimo (sem a URL completa da página, que pode conter dados).
# ══════════════════════════════════════════
@app.route("/api/csp-report", methods=["POST"])
def api_csp_report():
    if SEG_DB.consumir("csp", _ip_cliente(), 30, 600):
        try:
            rel = json.loads(request.get_data(as_text=True) or "{}").get("csp-report", {})
            diretiva = _texto(rel.get("violated-directive") or rel.get("effective-directive"), "?", 80)
            bloqueado = _texto(str(rel.get("blocked-uri", "?")).split("?")[0], "?", 120)
            print(f"[CSP] Violação: {diretiva} | bloqueado: {bloqueado}")
        except (ValueError, AttributeError):
            pass
    return "", 204

api_csp_report.csrf_isento = True

# ══════════════════════════════════════════
# LGPD — somente leitura via web. Exclusão: apenas pela linha de comando (lgpd.py),
# em modo simulação por padrão e com backup. Nada é excluído automaticamente.
# ══════════════════════════════════════════
@app.route("/api/lgpd/exportar")
@api_auth("motorista")
def api_lgpd_exportar():
    mid = motorista_da_sessao()
    resp = jsonify(REPO.exportar_dados(mid))
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=lgpd_export_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S')}.json")
    auditar("lgpd_exportacao", motorista_id=mid)
    return resp

@app.route("/api/lgpd/retencao")
@api_auth("motorista", "painel")
def api_lgpd_retencao():
    return jsonify(REPO.relatorio_retencao(motoristas_visiveis()))

# ══════════════════════════════════════════
# RUN
# ══════════════════════════════════════════
if __name__ == "__main__":
    verificar_dms_ao_iniciar()
    print("=" * 55)
    print("🛡️  DRIVER-SHIELD-360 PREMIUM v3.0")
    print("=" * 55)
    zapi_ok = bool(ZAPI_INSTANCE and ZAPI_TOKEN)
    print(f"📱 WhatsApp Z-API:  {'✅ OK' if zapi_ok else '⚠️  NÃO CONFIGURADO'}")
    print(f"🔐 Secrets:         ✅ configurados{' (DEV)' if DEV_MODE else ''}")
    print(f"🗄️  Armazenamento:   {STORAGE_BACKEND}")
    print(f"⏰ DMS:             agendador {'embutido' if AGENDADOR_EMBUTIDO else 'externo (python agendador.py)'}")
    print(f"📍 GPS:             ✅")
    print(f"📲 PWA:             ✅")
    print("=" * 55)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

# Chamada ao importar pelo gunicorn também
verificar_dms_ao_iniciar()
