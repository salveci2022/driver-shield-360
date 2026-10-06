"""
Migrations controladas do PostgreSQL.

- Arquivos em db/migracoes/NNNN_nome.sql, aplicados em ordem, uma única vez cada
  (registro em schema_migrations), cada um em sua própria transação.
- pg_advisory_lock impede duas execuções simultâneas (ex.: dois deploys).
- Recusa qualquer arquivo com comando destrutivo (DROP, TRUNCATE, DELETE, ALTER ... DROP).

Uso:
  python migrar.py status      (padrão: mostra pendentes, não altera nada)
  python migrar.py aplicar
Requer DATABASE_URL.
"""
import glob, hashlib, os, re, sys

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "migracoes")
TRAVA_MIGRACAO = 360_360_001   # chave do pg_advisory_lock

PROIBIDOS = re.compile(r"\b(DROP|TRUNCATE|DELETE)\b|\bALTER\b[^;]*\bDROP\b", re.IGNORECASE)


def _sem_comentarios(sql):
    return re.sub(r"--[^\n]*", "", sql)


def listar_migracoes():
    itens = []
    for caminho in sorted(glob.glob(os.path.join(PASTA, "*.sql"))):
        nome = os.path.basename(caminho)
        m = re.match(r"^(\d{4})_[a-z0-9_]+\.sql$", nome)
        if not m:
            raise ValueError(f"Nome de migration inválido: {nome}")
        sql = open(caminho, encoding="utf-8").read()
        if PROIBIDOS.search(_sem_comentarios(sql)):
            raise ValueError(f"Migration {nome} contém comando destrutivo — recusada")
        itens.append((int(m.group(1)), nome, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    return itens


def _garantir_controle(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                       versao      INTEGER PRIMARY KEY,
                       nome        TEXT NOT NULL,
                       sha256      TEXT NOT NULL,
                       aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now())""")


def status(conn):
    with conn.cursor() as cur:
        _garantir_controle(cur)
        conn.commit()
        cur.execute("SELECT versao, sha256 FROM schema_migrations")
        aplicadas = dict(cur.fetchall())
    pendentes, alteradas = [], []
    for versao, nome, _sql, sha in listar_migracoes():
        if versao not in aplicadas:
            pendentes.append(nome)
        elif aplicadas[versao] != sha:
            alteradas.append(nome)
    return {"aplicadas": len(aplicadas), "pendentes": pendentes, "alteradas_apos_aplicar": alteradas}


def aplicar(conn):
    """Aplica as pendentes. Retorna a lista de nomes aplicados."""
    aplicadas_agora = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (TRAVA_MIGRACAO,))
    try:
        st = status(conn)
        if st["alteradas_apos_aplicar"]:
            raise RuntimeError("Migrations já aplicadas foram alteradas: " + ", ".join(st["alteradas_apos_aplicar"]))
        for versao, nome, sql, sha in listar_migracoes():
            if nome not in st["pendentes"]:
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (versao, nome, sha256) VALUES (%s, %s, %s)",
                            (versao, nome, sha))
            conn.commit()
            aplicadas_agora.append(nome)
    except BaseException:
        conn.rollback()
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (TRAVA_MIGRACAO,))
        conn.commit()
    return aplicadas_agora


def _main(argv):
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL não definida.")
        return 2
    cmd = argv[1] if len(argv) > 1 else "status"
    conn = psycopg2.connect(url)
    try:
        if cmd == "aplicar":
            feitas = aplicar(conn)
            print("Aplicadas:", ", ".join(feitas) if feitas else "nenhuma (banco já atualizado)")
        else:
            print(status(conn))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
