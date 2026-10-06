"""
Estado de segurança compartilhado entre workers/processos (SQLite, sem dependências):

- Rate limit (login, IA): reserva atômica de tentativas via BEGIN IMMEDIATE.
- Sessões revogáveis: o cookie guarda apenas um identificador aleatório; o servidor guarda
  o hash dele, o papel, a expiração e a impressão digital da senha vigente. Logout ou troca
  de senha nas variáveis de ambiente invalidam a sessão no servidor.

LGPD: chaves de rate limit (IP) e identificadores de sessão são gravados apenas como
HMAC-SHA256; registros expirados são removidos automaticamente.
"""
import contextlib, hashlib, hmac, secrets, sqlite3, time


class SegurancaDB:
    def __init__(self, path, pepper):
        self.path = path
        self._pepper = pepper.encode("utf-8")
        with self._conexao() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tentativas (
                    escopo TEXT NOT NULL, chave TEXT NOT NULL, ts REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_tentativas ON tentativas(escopo, chave, ts);
                CREATE TABLE IF NOT EXISTS sessoes (
                    sid_hash TEXT PRIMARY KEY, papel TEXT NOT NULL,
                    criada REAL NOT NULL, expira REAL NOT NULL, senha_fp TEXT NOT NULL);
            """)
            # FASE 3: colunas adicionadas sem recriar a tabela (bancos antigos continuam válidos)
            colunas = {r[1] for r in c.execute("PRAGMA table_info(sessoes)")}
            if "usuario_id" not in colunas:
                c.execute("ALTER TABLE sessoes ADD COLUMN usuario_id TEXT")
            if "motorista_id" not in colunas:
                c.execute("ALTER TABLE sessoes ADD COLUMN motorista_id TEXT")

    def _con(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.execute("PRAGMA busy_timeout=15000")
        return c

    @contextlib.contextmanager
    def _conexao(self):
        c = self._con()
        try:
            yield c
        finally:
            c.close()

    def _h(self, valor):
        return hmac.new(self._pepper, str(valor).encode("utf-8"), hashlib.sha256).hexdigest()

    def impressao_senha(self, senha):
        return self._h("senha:" + senha)[:32]

    # ── Rate limit ──────────────────────────────────────────────
    def consumir(self, escopo, chave, limite, janela):
        """Reserva uma tentativa. Retorna False (sem registrar) se o limite já foi atingido."""
        agora = time.time()
        k = self._h(chave)
        c = self._con()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM tentativas WHERE escopo=? AND ts<?", (escopo, agora - janela))
            n = c.execute("SELECT COUNT(*) FROM tentativas WHERE escopo=? AND chave=?",
                          (escopo, k)).fetchone()[0]
            if n >= limite:
                c.execute("COMMIT")
                return False
            c.execute("INSERT INTO tentativas VALUES (?,?,?)", (escopo, k, agora))
            c.execute("COMMIT")
            return True
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def limpar(self, escopo, chave):
        with self._conexao() as c:
            c.execute("DELETE FROM tentativas WHERE escopo=? AND chave=?", (escopo, self._h(chave)))

    def limpar_tudo(self):
        with self._conexao() as c:
            c.execute("DELETE FROM tentativas")

    # ── Sessões ─────────────────────────────────────────────────
    def criar_sessao(self, papel, ttl, senha_fp, usuario_id=None, motorista_id=None):
        sid = secrets.token_urlsafe(32)
        agora = time.time()
        with self._conexao() as c:
            c.execute("DELETE FROM sessoes WHERE expira<?", (agora,))
            c.execute("INSERT INTO sessoes (sid_hash, papel, criada, expira, senha_fp, usuario_id, motorista_id) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (self._h(sid), papel, agora, agora + ttl, senha_fp,
                       None if usuario_id is None else str(usuario_id),
                       None if motorista_id is None else str(motorista_id)))
        return sid

    def sessao_valida(self, sid, papel, senha_fp):
        """Retorna {expira, usuario_id, motorista_id} ou None."""
        if not sid:
            return None
        with self._conexao() as c:
            row = c.execute("SELECT expira, senha_fp, usuario_id, motorista_id FROM sessoes "
                            "WHERE sid_hash=? AND papel=?", (self._h(sid), papel)).fetchone()
        if not row or row[0] <= time.time() or not hmac.compare_digest(row[1], senha_fp):
            return None
        return {"expira": row[0], "usuario_id": row[2], "motorista_id": row[3]}

    def limpar_expirados(self, janela_tentativas=24 * 3600):
        agora = time.time()
        with self._conexao() as c:
            c.execute("DELETE FROM sessoes WHERE expira<?", (agora,))
            c.execute("DELETE FROM tentativas WHERE ts<?", (agora - janela_tentativas,))

    def renovar_sessao(self, sid, ttl):
        with self._conexao() as c:
            c.execute("UPDATE sessoes SET expira=? WHERE sid_hash=?", (time.time() + ttl, self._h(sid)))

    def revogar_sessao(self, sid):
        if sid:
            with self._conexao() as c:
                c.execute("DELETE FROM sessoes WHERE sid_hash=?", (self._h(sid),))

    def revogar_todas(self, papel=None):
        with self._conexao() as c:
            if papel:
                c.execute("DELETE FROM sessoes WHERE papel=?", (papel,))
            else:
                c.execute("DELETE FROM sessoes")

    def expirar_sessao(self, sid):
        """Apenas para testes/administração: força a expiração imediata."""
        with self._conexao() as c:
            c.execute("UPDATE sessoes SET expira=0 WHERE sid_hash=?", (self._h(sid),))


class SegurancaPG:
    """Mesma interface de SegurancaDB, no PostgreSQL: compartilhada entre workers E instâncias.
    Sessões são invalidadas se o usuário for desativado ou trocar a senha."""

    def __init__(self, repo, pepper):
        self.repo = repo
        self._pepper = pepper.encode("utf-8")

    def _h(self, valor):
        return hmac.new(self._pepper, str(valor).encode("utf-8"), hashlib.sha256).hexdigest()

    def impressao_senha(self, senha):
        return self._h("senha:" + senha)[:32]

    # ── Rate limit ──
    def consumir(self, escopo, chave, limite, janela):
        k = self._h(chave)
        with self.repo.tx() as c:
            # Serializa só esta chave (escopo+chave) entre todos os workers/instâncias
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (escopo + ":" + k,))
            c.execute("DELETE FROM tentativas WHERE escopo = %s AND chave = %s "
                      "AND ts < now() - make_interval(secs => %s)", (escopo, k, float(janela)))
            c.execute("SELECT count(*) AS n FROM tentativas WHERE escopo = %s AND chave = %s", (escopo, k))
            if c.fetchone()["n"] >= limite:
                return False
            c.execute("INSERT INTO tentativas (escopo, chave) VALUES (%s, %s)", (escopo, k))
            return True

    def limpar(self, escopo, chave):
        with self.repo.tx() as c:
            c.execute("DELETE FROM tentativas WHERE escopo = %s AND chave = %s", (escopo, self._h(chave)))

    # ── Sessões ──
    def criar_sessao(self, papel, ttl, senha_fp, usuario_id=None, motorista_id=None):
        sid = secrets.token_urlsafe(32)
        with self.repo.tx() as c:
            c.execute("INSERT INTO sessoes (sid_hash, papel, usuario_id, motorista_id, expira, senha_fp) "
                      "VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s), %s)",
                      (self._h(sid), papel, usuario_id, motorista_id, float(ttl), senha_fp))
        return sid

    def sessao_valida(self, sid, papel, senha_fp):
        if not sid:
            return None
        with self.repo.tx() as c:
            c.execute("SELECT EXTRACT(EPOCH FROM s.expira) AS expira, s.usuario_id, s.motorista_id, s.senha_fp "
                      "FROM sessoes s LEFT JOIN usuarios u ON u.id = s.usuario_id "
                      "WHERE s.sid_hash = %s AND s.papel = %s AND s.expira > now() "
                      "AND (s.usuario_id IS NULL OR (u.ativo AND u.senha_alterada_em <= s.criada))",
                      (self._h(sid), papel))
            r = c.fetchone()
        if not r or not hmac.compare_digest(r["senha_fp"], senha_fp):
            return None
        return {"expira": float(r["expira"]), "usuario_id": r["usuario_id"], "motorista_id": r["motorista_id"]}

    def renovar_sessao(self, sid, ttl):
        with self.repo.tx() as c:
            c.execute("UPDATE sessoes SET expira = now() + make_interval(secs => %s) WHERE sid_hash = %s",
                      (float(ttl), self._h(sid)))

    def revogar_sessao(self, sid):
        if sid:
            with self.repo.tx() as c:
                c.execute("DELETE FROM sessoes WHERE sid_hash = %s", (self._h(sid),))

    def revogar_todas(self, papel=None, usuario_id=None):
        with self.repo.tx() as c:
            if usuario_id is not None:
                c.execute("DELETE FROM sessoes WHERE usuario_id = %s", (usuario_id,))
            elif papel:
                c.execute("DELETE FROM sessoes WHERE papel = %s", (papel,))
            else:
                c.execute("DELETE FROM sessoes")

    def expirar_sessao(self, sid):
        with self.repo.tx() as c:
            c.execute("UPDATE sessoes SET expira = now() - interval '1 second' WHERE sid_hash = %s", (self._h(sid),))

    def limpar_expirados(self, janela_tentativas=24 * 3600):
        with self.repo.tx() as c:
            c.execute("DELETE FROM sessoes WHERE expira < now()")
            c.execute("DELETE FROM tentativas WHERE ts < now() - make_interval(secs => %s)",
                      (float(janela_tentativas),))
