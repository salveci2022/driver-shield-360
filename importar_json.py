"""
Importação controlada dos JSONs (data/) para o PostgreSQL.

Garantias:
  1. Os arquivos JSON originais NUNCA são alterados nem apagados.
  2. Antes de tudo, cópia de segurança de data/*.json + manifesto SHA-256.
  3. Validação completa: registros inválidos e duplicados são listados (por posição e motivo,
     sem expor dados pessoais) e NÃO são importados.
  4. Modo simulação por padrão; só grava com --executar.
  5. Uma ÚNICA transação: se qualquer etapa falhar, nada é gravado (rollback automático).
  6. Idempotente: rodar de novo não duplica nada (cada registro leva uma impressão digital).

Uso:
  python importar_json.py --login-motorista maria --nome "Maria"                 (simulação)
  python importar_json.py --login-motorista maria --nome "Maria" \\
         --login-painel familia --executar
Senhas das contas criadas: variáveis MOTORISTA_SENHA / PAINEL_SENHA (as mesmas do modo JSON),
ou digitadas no terminal. Requer DATABASE_URL e banco migrado (python migrar.py aplicar).
"""
import argparse, getpass, hashlib, json, os, shutil, sys
from datetime import datetime, timedelta, timezone

BR_TZ = timezone(timedelta(hours=-3))
FORMATO_DATA = "%d/%m/%Y %H:%M:%S"
ARQUIVOS = {"contatos": "contacts.json", "alertas": "alerts.json",
            "historico": "historico_viagens.json", "viagem": "viagem.json"}
TIPOS_ALERTA = {"manual", "dead_mans_switch", "passageiro_suspeito"}
STATUS_VIAGEM = {"chegou_segura", "cancelada", "dead_mans_switch", "em_andamento"}


# ── utilidades ───────────────────────────────────────────────────
def _sha256_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def _impressao(registro):
    """Identificador estável de um registro legado (torna a importação idempotente)."""
    return hashlib.sha256(json.dumps(registro, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _data_br(valor):
    try:
        return datetime.strptime(str(valor), FORMATO_DATA).replace(tzinfo=BR_TZ)
    except (TypeError, ValueError):
        return None


def _coord_valida(valor, limite):
    if valor is None or valor == "":
        return True, None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return False, None
    if f != f or abs(f) > limite:
        return False, None
    return True, round(f, 6)


def _texto(valor, maximo):
    if not isinstance(valor, str):
        return None
    s = " ".join(valor.split())
    return s[:maximo] if s else None


def _ler(data_dir, chave, padrao):
    caminho = os.path.join(data_dir, ARQUIVOS[chave])
    if not os.path.exists(caminho):
        return padrao, None
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return padrao, f"{ARQUIVOS[chave]}: JSON inválido ({type(e).__name__})"


# ── 1. backup ────────────────────────────────────────────────────
def fazer_backup(data_dir):
    destino = os.path.join(data_dir, f"backup_importacao_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S_%f')}")
    os.makedirs(destino)
    manifesto = {}
    for nome in ARQUIVOS.values():
        origem = os.path.join(data_dir, nome)
        if os.path.exists(origem):
            shutil.copy2(origem, destino)
            manifesto[nome] = _sha256_arquivo(origem)
            if _sha256_arquivo(os.path.join(destino, nome)) != manifesto[nome]:
                raise RuntimeError(f"Backup de {nome} não confere com o original")
    with open(os.path.join(destino, "MANIFESTO_SHA256.json"), "w", encoding="utf-8") as f:
        json.dump(manifesto, f, indent=2)
    return destino, manifesto


# ── 2. validação ─────────────────────────────────────────────────
def validar(data_dir):
    """Retorna {'validos': {...}, 'problemas': [...], 'resumo': {...}} sem alterar nada."""
    problemas = []
    validos = {"contatos": [], "alertas": [], "historico": [], "viagem": None}

    contatos, erro = _ler(data_dir, "contatos", [])
    if erro: problemas.append(erro)
    if not isinstance(contatos, list):
        problemas.append("contacts.json: estrutura inválida (esperada lista)"); contatos = []
    vistos = set()
    for i, c in enumerate(contatos):
        if not isinstance(c, dict):
            problemas.append(f"contatos[{i}]: não é um objeto"); continue
        nome, tel = _texto(c.get("nome"), 60), _texto(c.get("telefone"), 20)
        digitos = "".join(ch for ch in (tel or "") if ch.isdigit())
        if not nome:
            problemas.append(f"contatos[{i}]: nome ausente"); continue
        if not tel or len(digitos) < 8:
            problemas.append(f"contatos[{i}]: telefone inválido"); continue
        if digitos in vistos:
            problemas.append(f"contatos[{i}]: telefone duplicado (ignorado)"); continue
        vistos.add(digitos)
        validos["contatos"].append({"nome": nome, "telefone": tel,
                                    "relacao": _texto(c.get("relacao"), 30) or "Contato"})
    if len(validos["contatos"]) > 5:
        problemas.append(f"contatos: {len(validos['contatos'])} válidos (o limite do app é 5) — todos serão importados")

    alertas, erro = _ler(data_dir, "alertas", [])
    if erro: problemas.append(erro)
    if not isinstance(alertas, list):
        problemas.append("alerts.json: estrutura inválida (esperada lista)"); alertas = []
    vistos = set()
    for i, a in enumerate(alertas):
        if not isinstance(a, dict):
            problemas.append(f"alertas[{i}]: não é um objeto"); continue
        quando = _data_br(a.get("ts"))
        ok_lat, lat = _coord_valida(a.get("lat"), 90)
        ok_lng, lng = _coord_valida(a.get("lng"), 180)
        if quando is None:
            problemas.append(f"alertas[{i}]: data inválida"); continue
        if not (ok_lat and ok_lng) or (lat is None) != (lng is None):
            problemas.append(f"alertas[{i}]: coordenadas inválidas (importado sem localização)")
            lat = lng = None
        impressao = _impressao(a)
        if impressao in vistos:
            problemas.append(f"alertas[{i}]: duplicado exato (ignorado)"); continue
        vistos.add(impressao)
        tipo = a.get("tipo") if a.get("tipo") in TIPOS_ALERTA else "manual"
        status = a.get("status") if a.get("status") in ("Ativo", "Resolvido") else "Ativo"
        validos["alertas"].append({
            "criado_em": quando, "nome": _texto(a.get("motorista"), 60) or "Motorista",
            "ocorrencia": _texto(a.get("ocorrencia"), 400) or "Emergência",
            "lat": lat, "lng": lng, "tipo": tipo, "status": status, "origem": impressao})

    viagem, erro = _ler(data_dir, "viagem", {})
    if erro: problemas.append(erro)
    viagem_ativa = viagem if isinstance(viagem, dict) and viagem.get("ativa") else None

    historico, erro = _ler(data_dir, "historico", [])
    if erro: problemas.append(erro)
    if not isinstance(historico, list):
        problemas.append("historico_viagens.json: estrutura inválida (esperada lista)"); historico = []
    vistos = set()
    ultimo_em_andamento = max((i for i, v in enumerate(historico)
                               if isinstance(v, dict) and v.get("resultado") == "em_andamento"), default=None)
    for i, v in enumerate(historico):
        if not isinstance(v, dict):
            problemas.append(f"historico[{i}]: não é um objeto"); continue
        inicio = _data_br(v.get("inicio"))
        try:
            minutos = int(v.get("minutos"))
        except (TypeError, ValueError):
            minutos = None
        if inicio is None or minutos is None or not 1 <= minutos <= 1440:
            problemas.append(f"historico[{i}]: data ou duração inválida"); continue
        ok_lat, lat = _coord_valida(v.get("lat"), 90)
        ok_lng, lng = _coord_valida(v.get("lng"), 180)
        if not (ok_lat and ok_lng) or (lat is None) != (lng is None):
            problemas.append(f"historico[{i}]: coordenadas inválidas (importado sem localização)")
            lat = lng = None
        impressao = _impressao(v)
        if impressao in vistos:
            problemas.append(f"historico[{i}]: duplicado exato (ignorado)"); continue
        vistos.add(impressao)
        resultado = v.get("resultado") if v.get("resultado") in STATUS_VIAGEM else "interrompida"
        expira_em = None
        if resultado == "em_andamento":
            if i == ultimo_em_andamento and viagem_ativa and viagem_ativa.get("expira_em"):
                expira_em = datetime.fromtimestamp(float(viagem_ativa["expira_em"]), timezone.utc)
            else:
                resultado = "interrompida"   # viagens antigas que nunca foram encerradas
        validos["historico"].append({
            "inicio": inicio, "fim": _data_br(v.get("fim")), "nome": _texto(v.get("motorista"), 60) or "Motorista",
            "minutos": minutos, "lat": lat, "lng": lng, "status": resultado,
            "expira_em": expira_em, "origem": impressao})

    resumo = {
        "contatos":  {"lidos": len(contatos), "validos": len(validos["contatos"])},
        "alertas":   {"lidos": len(alertas), "validos": len(validos["alertas"])},
        "historico": {"lidos": len(historico), "validos": len(validos["historico"])},
        "viagem_ativa": bool(viagem_ativa),
    }
    return {"validos": validos, "problemas": problemas, "resumo": resumo}


# ── 3. importação (transação única) ──────────────────────────────
def _obter_ou_criar(cur, login, senha, papel, nome=None):
    from repositorio_pg import RepositorioPG
    cur.execute("SELECT u.id, u.papel, m.id AS motorista_id FROM usuarios u "
                "LEFT JOIN motoristas m ON m.usuario_id = u.id WHERE lower(u.login) = lower(%s)", (login,))
    r = cur.fetchone()
    if r:
        if r["papel"] != papel:
            raise ValueError(f"O login '{login}' já existe com outro papel ({r['papel']})")
        return {"usuario_id": r["id"], "motorista_id": r["motorista_id"], "criado": False}
    if not senha:
        raise ValueError(f"Senha não informada para criar '{login}'")
    conta = RepositorioPG.criar_usuario(None, login, senha, papel, nome=nome, cur=cur)
    return dict(conta, criado=True)


def _inserir_contatos(cur, mid, contatos):
    n = 0
    for c in contatos:
        cur.execute("INSERT INTO contatos_confianca (motorista_id, nome, telefone, relacao) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (motorista_id, telefone) DO NOTHING RETURNING id",
                    (mid, c["nome"], c["telefone"], c["relacao"]))
        n += cur.fetchone() is not None
    return n


def _inserir_alertas(cur, mid, alertas):
    n = 0
    for a in alertas:
        cur.execute("INSERT INTO alertas (motorista_id, tipo, status, nome_exibido, ocorrencia, lat, lng, "
                    "criado_em, resolvido_em, origem_legado) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (motorista_id, origem_legado) WHERE origem_legado IS NOT NULL DO NOTHING "
                    "RETURNING id",
                    (mid, a["tipo"], a["status"], a["nome"], a["ocorrencia"], a["lat"], a["lng"], a["criado_em"],
                     a["criado_em"] if a["status"] == "Resolvido" else None, a["origem"]))
        r = cur.fetchone()
        if r:
            n += 1
            if a["lat"] is not None:
                cur.execute("INSERT INTO localizacoes (motorista_id, alerta_id, lat, lng, origem, registrada_em) "
                            "VALUES (%s, %s, %s, %s, 'legado', %s)", (mid, r["id"], a["lat"], a["lng"], a["criado_em"]))
    return n


def _inserir_viagens(cur, mid, viagens):
    import secrets
    n = 0
    for v in viagens:
        if v["status"] == "em_andamento":
            # Se já houver uma viagem ativa para o motorista no banco, a legada vira 'interrompida'
            cur.execute("SELECT 1 FROM viagens WHERE motorista_id = %s AND status = 'em_andamento'", (mid,))
            if cur.fetchone():
                v = dict(v, status="interrompida", expira_em=None)
        cur.execute("INSERT INTO viagens (publico_id, motorista_id, nome_exibido, minutos, status, inicio, expira_em, "
                    "fim, lat_inicio, lng_inicio, origem_legado) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (motorista_id, origem_legado) WHERE origem_legado IS NOT NULL DO NOTHING RETURNING id",
                    (secrets.token_hex(8), mid, v["nome"], v["minutos"], v["status"], v["inicio"], v["expira_em"],
                     v["fim"], v["lat"], v["lng"], v["origem"]))
        r = cur.fetchone()
        if r:
            n += 1
            if v["lat"] is not None:
                cur.execute("INSERT INTO localizacoes (motorista_id, viagem_id, lat, lng, origem, registrada_em) "
                            "VALUES (%s, %s, %s, %s, 'legado', %s)", (mid, r["id"], v["lat"], v["lng"], v["inicio"]))
    return n


def importar(conn, data_dir, login_motorista, senha_motorista, nome_motorista=None,
             login_painel=None, senha_painel=None, executar=False):
    """Valida e (com executar=True) importa numa única transação. Retorna o relatório."""
    import psycopg2.extras
    relatorio = {"modo": "execucao" if executar else "simulacao"}
    if executar:
        relatorio["backup"], relatorio["manifesto"] = fazer_backup(data_dir)
    v = validar(data_dir)
    relatorio.update(resumo=v["resumo"], problemas=v["problemas"])
    if not executar:
        return relatorio

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            m = _obter_ou_criar(cur, login_motorista, senha_motorista, "motorista", nome_motorista)
            mid = m["motorista_id"]
            importados = {
                "contatos": _inserir_contatos(cur, mid, v["validos"]["contatos"]),
                "alertas": _inserir_alertas(cur, mid, v["validos"]["alertas"]),
                "viagens": _inserir_viagens(cur, mid, v["validos"]["historico"]),
            }
            if login_painel:
                p = _obter_ou_criar(cur, login_painel, senha_painel, "painel")
                cur.execute("INSERT INTO painel_acessos (usuario_id, motorista_id) VALUES (%s, %s) "
                            "ON CONFLICT DO NOTHING", (p["usuario_id"], mid))
            for chave, nome in ARQUIVOS.items():
                if nome in relatorio["manifesto"]:
                    cur.execute("INSERT INTO importacoes (motorista_id, arquivo, sha256, registros) VALUES (%s, %s, %s, %s)",
                                (mid, nome, relatorio["manifesto"][nome],
                                 importados.get({"historico": "viagens"}.get(chave, chave), 0)))
        conn.commit()
    except BaseException:
        conn.rollback()   # nada é gravado
        raise
    relatorio.update(motorista_id=mid, motorista_criado=m["criado"], importados=importados)
    with open(os.path.join(relatorio["backup"], "RELATORIO_IMPORTACAO.json"), "w", encoding="utf-8") as f:
        json.dump({k: relatorio[k] for k in ("modo", "resumo", "problemas", "importados", "motorista_criado")},
                  f, ensure_ascii=False, indent=2)
    return relatorio


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Importa data/*.json para o PostgreSQL (sem alterar os JSONs)")
    ap.add_argument("--login-motorista", required=True)
    ap.add_argument("--nome", help="Nome do motorista (padrão: o login)")
    ap.add_argument("--login-painel", help="Cria/vincula uma conta de painel (pessoa de confiança)")
    ap.add_argument("--data-dir", default=os.environ.get("DS360_DATA_DIR") or
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--executar", action="store_true", help="Grava no banco (sem isso: simulação)")
    a = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL não definida."); return 2
    senha_m = senha_p = None
    if a.executar:
        senha_m = os.environ.get("MOTORISTA_SENHA") or getpass.getpass("Senha do motorista (mín. 10): ")
        if a.login_painel:
            senha_p = os.environ.get("PAINEL_SENHA") or getpass.getpass("Senha do painel (mín. 10): ")

    import psycopg2, migrar
    conn = psycopg2.connect(url)
    try:
        st = migrar.status(conn)
        if st["pendentes"]:
            print("Banco não migrado. Execute: python migrar.py aplicar"); return 2
        rel = importar(conn, a.data_dir, a.login_motorista, senha_m, a.nome, a.login_painel, senha_p, a.executar)
    finally:
        conn.close()
    sys.stdout.reconfigure(encoding="utf-8")
    if not a.executar:
        print("MODO SIMULAÇÃO — nada foi gravado. Use --executar para importar.")
    print(json.dumps({k: v for k, v in rel.items() if k != "manifesto"}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
