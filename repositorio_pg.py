"""
Repositório PostgreSQL (multiusuário).

Isolamento anti-IDOR/BOLA: TODA consulta é filtrada por motorista_id, e esse valor vem
sempre da sessão autenticada (ou da lista de motoristas autorizados do painel), nunca
de um parâmetro enviado pelo cliente. Um id de outro motorista simplesmente "não existe"
para quem não tem acesso (a operação retorna False / lista vazia).

Formatos de saída idênticos ao modo JSON (ts "dd/mm/aaaa hh:mm:ss", coordenadas como
string com 6 casas), para que templates, painel e PDF funcionem sem mudanças.
"""
import contextlib, os, secrets, threading
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import psycopg2.pool
from werkzeug.security import check_password_hash, generate_password_hash

BR_TZ = timezone(timedelta(hours=-3))
MAX_CONTATOS = 5
DMS_TOLERANCIA = 2  # segundos
# Hash usado quando o login não existe: mantém o tempo de resposta parecido (anti-enumeração)
_HASH_FICTICIO = generate_password_hash("senha-ficticia-para-tempo-constante")

RESULTADO_EXIBICAO = {  # status da viagem → valor esperado pelo template de histórico
    "em_andamento": "em_andamento", "chegou_segura": "chegou_segura",
    "dead_mans_switch": "dead_mans_switch", "cancelada": "cancelada",
    "cancelada_por_sos": "cancelada", "interrompida": "cancelada",
}


def _fmt_ts(dt):
    return dt.astimezone(BR_TZ).strftime("%d/%m/%Y %H:%M:%S") if dt else None


def _fmt_coord(v):
    return f"{float(v):.6f}" if v is not None else None


class RepositorioPG:
    multiusuario = True

    def __init__(self, dsn, minconn=1, maxconn=8):
        self.dsn = dsn
        self._minconn, self._maxconn = minconn, maxconn
        self._pool = None
        self._pid = None
        self._lock = threading.Lock()
        # O pool do psycopg2 LANÇA erro quando esgotado; o semáforo faz a requisição ESPERAR
        # uma conexão livre (um pico de SOS nunca pode perder alertas).
        self._vagas = threading.BoundedSemaphore(maxconn)
        self.timeout_conexao = 30

    # ── Conexões (pool por processo: seguro com fork do gunicorn) ─
    def _pool_atual(self):
        if self._pool is None or self._pid != os.getpid():
            with self._lock:
                if self._pool is None or self._pid != os.getpid():
                    self._pool = psycopg2.pool.ThreadedConnectionPool(self._minconn, self._maxconn, self.dsn)
                    self._vagas = threading.BoundedSemaphore(self._maxconn)
                    self._pid = os.getpid()
        return self._pool

    @contextlib.contextmanager
    def tx(self):
        """Transação: commit ao final, rollback em qualquer erro."""
        pool = self._pool_atual()
        vagas = self._vagas
        if not vagas.acquire(timeout=self.timeout_conexao):
            raise TimeoutError("Banco de dados ocupado: nenhuma conexão livre")
        try:
            conn = pool.getconn()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    yield cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                pool.putconn(conn)
        finally:
            vagas.release()

    def verificar_saude(self):
        """Health check: o banco responde (SELECT 1), sem expor detalhes."""
        with self.tx() as c:
            c.execute("SELECT 1 AS ok")
            c.fetchone()

    def fechar(self):
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None

    # ── Usuários ─────────────────────────────────────────────────
    def criar_usuario(self, login, senha, papel, nome=None, cur=None):
        """Cria usuário (e o motorista, se papel='motorista'). Retorna {usuario_id, motorista_id}."""
        if len(senha) < 10:
            raise ValueError("A senha deve ter pelo menos 10 caracteres")
        def _criar(c):
            c.execute("INSERT INTO usuarios (login, senha_hash, papel) VALUES (%s, %s, %s) RETURNING id",
                      (login.strip(), generate_password_hash(senha), papel))
            uid = c.fetchone()["id"]
            mid = None
            if papel == "motorista":
                c.execute("INSERT INTO motoristas (usuario_id, nome) VALUES (%s, %s) RETURNING id",
                          (uid, (nome or login).strip()[:60]))
                mid = c.fetchone()["id"]
            return {"usuario_id": uid, "motorista_id": mid}
        if cur is not None:
            return _criar(cur)
        with self.tx() as c:
            return _criar(c)

    def vincular_painel(self, usuario_painel_id, motorista_id, cur=None):
        sql = ("INSERT INTO painel_acessos (usuario_id, motorista_id) "
               "SELECT u.id, %s FROM usuarios u WHERE u.id = %s AND u.papel = 'painel' "
               "ON CONFLICT DO NOTHING")
        if cur is not None:
            cur.execute(sql, (motorista_id, usuario_painel_id))
            return
        with self.tx() as c:
            c.execute(sql, (motorista_id, usuario_painel_id))

    def buscar_usuario(self, login):
        with self.tx() as c:
            c.execute("SELECT u.id AS usuario_id, u.papel, u.ativo, m.id AS motorista_id "
                      "FROM usuarios u LEFT JOIN motoristas m ON m.usuario_id = u.id "
                      "WHERE lower(u.login) = lower(%s)", (login.strip(),))
            return c.fetchone()

    def autenticar(self, login, senha, papel):
        with self.tx() as c:
            c.execute("SELECT u.id, u.senha_hash, m.id AS motorista_id FROM usuarios u "
                      "LEFT JOIN motoristas m ON m.usuario_id = u.id "
                      "WHERE lower(u.login) = lower(%s) AND u.papel = %s AND u.ativo", (login.strip(), papel))
            row = c.fetchone()
        if not row:
            check_password_hash(_HASH_FICTICIO, senha)
            return None
        if not check_password_hash(row["senha_hash"], senha):
            return None
        return {"usuario_id": row["id"], "motorista_id": row["motorista_id"]}

    def trocar_senha(self, usuario_id, nova_senha):
        """Também invalida todas as sessões existentes desse usuário (senha_alterada_em)."""
        if len(nova_senha) < 10:
            raise ValueError("A senha deve ter pelo menos 10 caracteres")
        with self.tx() as c:
            c.execute("UPDATE usuarios SET senha_hash = %s, senha_alterada_em = now() WHERE id = %s",
                      (generate_password_hash(nova_senha), usuario_id))
            return c.rowcount == 1

    def definir_ativo(self, usuario_id, ativo):
        with self.tx() as c:
            c.execute("UPDATE usuarios SET ativo = %s WHERE id = %s", (bool(ativo), usuario_id))
            return c.rowcount == 1

    def motoristas_do_painel(self, usuario_id):
        if usuario_id is None:
            return []
        with self.tx() as c:
            c.execute("SELECT pa.motorista_id FROM painel_acessos pa JOIN usuarios u ON u.id = pa.usuario_id "
                      "WHERE pa.usuario_id = %s AND u.ativo AND u.papel = 'painel' ORDER BY pa.motorista_id",
                      (usuario_id,))
            return [r["motorista_id"] for r in c.fetchall()]

    def motorista_existe(self, motorista_id):
        with self.tx() as c:
            c.execute("SELECT 1 FROM motoristas m JOIN usuarios u ON u.id = m.usuario_id "
                      "WHERE m.id = %s AND u.ativo", (motorista_id,))
            return c.fetchone() is not None

    # ── Contatos ─────────────────────────────────────────────────
    def listar_contatos(self, motorista_id):
        with self.tx() as c:
            c.execute("SELECT id, nome, telefone, relacao FROM contatos_confianca "
                      "WHERE motorista_id = %s ORDER BY id", (motorista_id,))
            return [dict(r) for r in c.fetchall()]

    def adicionar_contato(self, motorista_id, nome, telefone, relacao):
        with self.tx() as c:
            # Trava o motorista: o limite de 5 contatos vale mesmo com requisições simultâneas
            c.execute("SELECT id FROM motoristas WHERE id = %s FOR UPDATE", (motorista_id,))
            if c.fetchone() is None:
                return "invalido"
            c.execute("SELECT count(*) AS n FROM contatos_confianca WHERE motorista_id = %s", (motorista_id,))
            if c.fetchone()["n"] >= MAX_CONTATOS:
                return "limite"
            c.execute("INSERT INTO contatos_confianca (motorista_id, nome, telefone, relacao) "
                      "VALUES (%s, %s, %s, %s) ON CONFLICT (motorista_id, telefone) DO NOTHING RETURNING id",
                      (motorista_id, nome, telefone, relacao))
            return "ok" if c.fetchone() else "duplicado"

    def remover_contato(self, motorista_id, contato_id):
        with self.tx() as c:
            c.execute("DELETE FROM contatos_confianca WHERE id = %s AND motorista_id = %s",
                      (contato_id, motorista_id))
            return c.rowcount == 1

    # ── Veículos ─────────────────────────────────────────────────
    def listar_veiculos(self, motorista_id):
        with self.tx() as c:
            c.execute("SELECT id, placa, modelo, cor, ano FROM veiculos WHERE motorista_id = %s ORDER BY id",
                      (motorista_id,))
            return [dict(r) for r in c.fetchall()]

    def adicionar_veiculo(self, motorista_id, placa, modelo=None, cor=None, ano=None):
        with self.tx() as c:
            c.execute("INSERT INTO veiculos (motorista_id, placa, modelo, cor, ano) VALUES (%s, %s, %s, %s, %s) "
                      "ON CONFLICT (motorista_id, placa) DO NOTHING RETURNING id",
                      (motorista_id, placa.upper()[:10], modelo, cor, ano))
            r = c.fetchone()
            return r["id"] if r else None

    # ── Alertas ──────────────────────────────────────────────────
    @staticmethod
    def _alerta_saida(r):
        return {"id": r["id"], "ts": _fmt_ts(r["criado_em"]), "motorista": r["nome_exibido"],
                "ocorrencia": r["ocorrencia"], "lat": _fmt_coord(r["lat"]), "lng": _fmt_coord(r["lng"]),
                "status": r["status"], "tipo": r["tipo"]}

    def _inserir_alerta(self, c, motorista_id, alerta, viagem_id=None, descricao=None, origem_loc="sos"):
        c.execute("INSERT INTO alertas (motorista_id, viagem_id, tipo, status, nome_exibido, ocorrencia, lat, lng) "
                  "VALUES (%s, %s, %s, 'Ativo', %s, %s, %s, %s) "
                  "RETURNING id, criado_em, nome_exibido, ocorrencia, lat, lng, status, tipo",
                  (motorista_id, viagem_id, alerta.get("tipo", "manual"), alerta.get("motorista") or "Motorista",
                   alerta.get("ocorrencia") or "Emergência", alerta.get("lat"), alerta.get("lng")))
        r = c.fetchone()
        if r["lat"] is not None and r["lng"] is not None:
            c.execute("INSERT INTO localizacoes (motorista_id, viagem_id, alerta_id, lat, lng, origem) "
                      "VALUES (%s, %s, %s, %s, %s, %s)",
                      (motorista_id, viagem_id, r["id"], r["lat"], r["lng"], origem_loc))
        if descricao is not None:
            c.execute("INSERT INTO ocorrencias (motorista_id, alerta_id, categoria, descricao) "
                      "VALUES (%s, %s, %s, %s)", (motorista_id, r["id"], alerta.get("tipo", "manual"), descricao))
        return self._alerta_saida(r)

    def registrar_alerta(self, motorista_id, alerta, descricao=None):
        origem = "passageiro_suspeito" if alerta.get("tipo") == "passageiro_suspeito" else "sos"
        with self.tx() as c:
            return self._inserir_alerta(c, motorista_id, alerta, descricao=descricao, origem_loc=origem)

    def listar_alertas(self, motoristas):
        if not motoristas:
            return []
        with self.tx() as c:
            c.execute("SELECT * FROM (SELECT id, criado_em, nome_exibido, ocorrencia, lat, lng, status, tipo "
                      "FROM alertas WHERE motorista_id = ANY(%s) AND arquivado_em IS NULL "
                      "ORDER BY criado_em DESC, id DESC LIMIT 500) x ORDER BY criado_em, id",
                      (list(motoristas),))
            return [self._alerta_saida(r) for r in c.fetchall()]

    def resolver_alerta(self, motoristas, alerta_id, usuario_id=None):
        if not motoristas:
            return False
        with self.tx() as c:
            c.execute("UPDATE alertas SET status = 'Resolvido', resolvido_em = COALESCE(resolvido_em, now()), "
                      "resolvido_por = COALESCE(resolvido_por, %s) "
                      "WHERE id = %s AND motorista_id = ANY(%s) AND arquivado_em IS NULL",
                      (usuario_id, alerta_id, list(motoristas)))
            return c.rowcount == 1

    def limpar_alertas(self, motoristas):
        """No PostgreSQL 'limpar' ARQUIVA (os registros permanecem para auditoria/LGPD)."""
        if not motoristas:
            return 0
        with self.tx() as c:
            c.execute("UPDATE alertas SET arquivado_em = now() WHERE motorista_id = ANY(%s) AND arquivado_em IS NULL",
                      (list(motoristas),))
            return c.rowcount

    # ── Viagens / Dead Man's Switch ──────────────────────────────
    def iniciar_viagem(self, motorista_id, nome, minutos, lat, lng):
        with self.tx() as c:
            c.execute("SELECT id FROM motoristas WHERE id = %s FOR UPDATE", (motorista_id,))
            if c.fetchone() is None:
                raise ValueError("motorista inexistente")
            # Uma nova viagem substitui a anterior (como no modo JSON), registrando-a como interrompida
            c.execute("UPDATE viagens SET status = 'interrompida', fim = now() "
                      "WHERE motorista_id = %s AND status = 'em_andamento'", (motorista_id,))
            c.execute("INSERT INTO viagens (publico_id, motorista_id, nome_exibido, minutos, status, expira_em, "
                      "lat_inicio, lng_inicio) VALUES (%s, %s, %s, %s, 'em_andamento', "
                      "now() + make_interval(mins => %s), %s, %s) RETURNING id, publico_id, expira_em",
                      (secrets.token_hex(8), motorista_id, nome, minutos, minutos, lat, lng))
            v = c.fetchone()
            if lat is not None and lng is not None:
                c.execute("INSERT INTO localizacoes (motorista_id, viagem_id, lat, lng, origem) "
                          "VALUES (%s, %s, %s, %s, 'viagem_inicio')", (motorista_id, v["id"], lat, lng))
        return {"id": v["publico_id"], "expira_em": v["expira_em"].timestamp()}

    def encerrar_viagem(self, motorista_id, resultado):
        with self.tx() as c:
            c.execute("UPDATE viagens SET status = %s, fim = now() "
                      "WHERE motorista_id = %s AND status = 'em_andamento'", (resultado, motorista_id))
            return c.rowcount == 1

    def cancelar_viagem_por_sos(self, motorista_id):
        return self.encerrar_viagem(motorista_id, "cancelada_por_sos")

    def status_viagem(self, motoristas):
        if not motoristas:
            return {"ativa": False}
        with self.tx() as c:
            c.execute("SELECT nome_exibido, minutos, GREATEST(0, EXTRACT(EPOCH FROM expira_em - now()))::int AS restante "
                      "FROM viagens WHERE motorista_id = ANY(%s) AND status = 'em_andamento' "
                      "ORDER BY expira_em LIMIT 1", (list(motoristas),))
            r = c.fetchone()
        if not r:
            return {"ativa": False}
        return {"ativa": True, "motorista": r["nome_exibido"], "restante": r["restante"],
                "duracao": r["minutos"] * 60}

    def listar_historico(self, motoristas):
        if not motoristas:
            return []
        with self.tx() as c:
            c.execute("SELECT inicio, fim, nome_exibido, minutos, lat_inicio, lng_inicio, status FROM viagens "
                      "WHERE motorista_id = ANY(%s) ORDER BY inicio DESC, id DESC LIMIT 200", (list(motoristas),))
            return [{"inicio": _fmt_ts(r["inicio"]), "fim": _fmt_ts(r["fim"]), "motorista": r["nome_exibido"],
                     "minutos": r["minutos"], "lat": _fmt_coord(r["lat_inicio"]), "lng": _fmt_coord(r["lng_inicio"]),
                     "resultado": RESULTADO_EXIBICAO.get(r["status"], "cancelada")} for r in c.fetchall()]

    def _disparar(self, c, filtro_sql, params):
        """UPDATE ... WHERE status='em_andamento' garante que só UMA transação dispara cada viagem."""
        c.execute("UPDATE viagens SET status = 'dead_mans_switch', fim = now() WHERE id IN ("
                  "  SELECT id FROM viagens WHERE status = 'em_andamento' AND " + filtro_sql +
                  "  ORDER BY expira_em LIMIT 50 FOR UPDATE SKIP LOCKED) "
                  "RETURNING id, motorista_id, nome_exibido, lat_inicio, lng_inicio", params)
        disparos = []
        for v in c.fetchall():
            alerta = {"tipo": "dead_mans_switch", "motorista": v["nome_exibido"],
                      "ocorrencia": "⏰ ALERTA AUTOMÁTICO — Motorista não confirmou chegada",
                      "lat": v["lat_inicio"], "lng": v["lng_inicio"]}
            disparos.append((v["motorista_id"],
                             self._inserir_alerta(c, v["motorista_id"], alerta, viagem_id=v["id"],
                                                  origem_loc="dead_mans_switch")))
        return disparos

    def disparar_viagens_expiradas(self):
        with self.tx() as c:
            return self._disparar(c, "expira_em <= now()", ())

    def disparar_viagem(self, viagem_id=None):
        if viagem_id is None:
            r = self.disparar_viagens_expiradas()
            return r[0] if r else None
        with self.tx() as c:
            r = self._disparar(c, "publico_id = %s AND expira_em <= now() + make_interval(secs => %s)",
                               (viagem_id, DMS_TOLERANCIA))
        return r[0] if r else None

    # ── Agendador: lease no banco ────────────────────────────────
    def adquirir_tarefa(self, nome, ttl, executor):
        with self.tx() as c:
            c.execute("INSERT INTO tarefas_agendadas (nome) VALUES (%s) ON CONFLICT DO NOTHING", (nome,))
            c.execute("UPDATE tarefas_agendadas SET bloqueada_ate = now() + make_interval(secs => %s), "
                      "executor = %s, ultima_execucao = now() "
                      "WHERE nome = %s AND (bloqueada_ate IS NULL OR bloqueada_ate <= now()) RETURNING nome",
                      (float(ttl), executor, nome))
            return c.fetchone() is not None

    # ── Auditoria ────────────────────────────────────────────────
    def auditar(self, acao, usuario_id=None, motorista_id=None, alvo=None, ip_hash=None, detalhes=None):
        mid = motorista_id if isinstance(motorista_id, int) else None
        with self.tx() as c:
            c.execute("INSERT INTO auditoria (acao, usuario_id, motorista_id, alvo, ip_hash, detalhes) "
                      "VALUES (%s, %s, %s, %s, %s, %s)",
                      (acao, usuario_id, mid, alvo, ip_hash, psycopg2.extras.Json(detalhes or {})))

    # ── LGPD ─────────────────────────────────────────────────────
    def exportar_dados(self, motorista_id):
        """Todos os dados pessoais DESTE motorista (direito de acesso/portabilidade)."""
        consultas = {
            "motorista":    "SELECT m.nome, u.login, m.criado_em FROM motoristas m JOIN usuarios u ON u.id = m.usuario_id WHERE m.id = %s",
            "contatos":     "SELECT nome, telefone, relacao, criado_em FROM contatos_confianca WHERE motorista_id = %s ORDER BY id",
            "veiculos":     "SELECT placa, modelo, cor, ano FROM veiculos WHERE motorista_id = %s ORDER BY id",
            "viagens":      "SELECT inicio, fim, minutos, status, lat_inicio, lng_inicio FROM viagens WHERE motorista_id = %s ORDER BY inicio",
            "alertas":      "SELECT criado_em, tipo, status, ocorrencia, lat, lng, arquivado_em FROM alertas WHERE motorista_id = %s ORDER BY criado_em",
            "ocorrencias":  "SELECT criada_em, categoria, descricao FROM ocorrencias WHERE motorista_id = %s ORDER BY criada_em",
            "localizacoes": "SELECT registrada_em, origem, lat, lng FROM localizacoes WHERE motorista_id = %s ORDER BY registrada_em",
        }
        dados = {}
        with self.tx() as c:
            for chave, sql in consultas.items():
                c.execute(sql, (motorista_id,))
                dados[chave] = [{k: (v.isoformat() if isinstance(v, datetime) else
                                     float(v) if v.__class__.__name__ == "Decimal" else v)
                                 for k, v in r.items()} for r in c.fetchall()]
        return {"gerado_em": _fmt_ts(datetime.now(timezone.utc)), "dados": dados}

    def relatorio_retencao(self, motoristas, dias_alertas=365, dias_viagens=180):
        if not motoristas:
            return {}
        with self.tx() as c:
            c.execute("SELECT "
                      " (SELECT count(*) FROM contatos_confianca WHERE motorista_id = ANY(%(m)s)) AS contatos,"
                      " (SELECT count(*) FROM alertas WHERE motorista_id = ANY(%(m)s)) AS alertas,"
                      " (SELECT count(*) FROM alertas WHERE motorista_id = ANY(%(m)s) AND criado_em < now() - make_interval(days => %(da)s)) AS alertas_vencidos,"
                      " (SELECT count(*) FROM viagens WHERE motorista_id = ANY(%(m)s)) AS viagens,"
                      " (SELECT count(*) FROM viagens WHERE motorista_id = ANY(%(m)s) AND inicio < now() - make_interval(days => %(dv)s)) AS viagens_vencidas",
                      {"m": list(motoristas), "da": dias_alertas, "dv": dias_viagens})
            r = c.fetchone()
        return {
            "contatos":  {"total": r["contatos"], "retencao_dias": None, "vencidos_pelo_prazo_proposto": 0},
            "alertas":   {"total": r["alertas"], "retencao_dias": dias_alertas, "vencidos_pelo_prazo_proposto": r["alertas_vencidos"]},
            "historico": {"total": r["viagens"], "retencao_dias": dias_viagens, "vencidos_pelo_prazo_proposto": r["viagens_vencidas"]},
        }
