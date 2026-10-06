"""
Repositório JSON (modo legado, um único motorista).

Mantém exatamente o comportamento e o formato dos arquivos em data/. É o backend
padrão e o caminho de ROLLBACK caso a migração para PostgreSQL precise ser desfeita.

Contrato comum com repositorio_pg.RepositorioPG: as rotas recebem sempre o
motorista da SESSÃO (nunca um id vindo do cliente). Aqui existe só um motorista,
identificado por LEGADO.
"""
import json, os, secrets, threading
from datetime import datetime, timedelta, timezone

from armazenamento import ler_json, gravar_json, gravar_json_novo, transacao, trava

LEGADO = "legado"
BR_TZ = timezone(timedelta(hours=-3))
MAX_CONTATOS = 5
DMS_TOLERANCIA = 2  # segundos


def agora_br_str():
    return datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")


def agora_ts():
    return datetime.now(timezone.utc).timestamp()


class RepositorioJSON:
    multiusuario = False

    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.contatos_arq  = os.path.join(data_dir, "contacts.json")
        self.alertas_arq   = os.path.join(data_dir, "alerts.json")
        self.viagem_arq    = os.path.join(data_dir, "viagem.json")
        self.historico_arq = os.path.join(data_dir, "historico_viagens.json")
        self.agenda_arq    = os.path.join(data_dir, "agendador.json")
        self.auditoria_arq = os.path.join(data_dir, "auditoria.jsonl")

    def verificar_saude(self):
        """Health check: a pasta de dados existe e aceita escrita."""
        if not os.access(self.data_dir, os.W_OK):
            raise OSError("pasta de dados sem permissão de escrita")

    # ── Motoristas / painel ──────────────────────────────────────
    def motoristas_do_painel(self, usuario_id):
        return [LEGADO]

    def motorista_existe(self, motorista_id):
        return motorista_id == LEGADO

    # ── Contatos ─────────────────────────────────────────────────
    def listar_contatos(self, motorista_id):
        return [dict(c, id=i) for i, c in enumerate(ler_json(self.contatos_arq, []))]

    def adicionar_contato(self, motorista_id, nome, telefone, relacao):
        """Retorna 'ok', 'limite' ou 'duplicado'."""
        with transacao(self.contatos_arq, []) as t:
            if len(t.dados) >= MAX_CONTATOS:
                return "limite"
            if any(c.get("telefone") == telefone for c in t.dados):
                return "duplicado"
            t.salvar(t.dados + [{"nome": nome, "telefone": telefone, "relacao": relacao}])
            return "ok"

    def remover_contato(self, motorista_id, contato_id):
        with transacao(self.contatos_arq, []) as t:
            if isinstance(contato_id, int) and 0 <= contato_id < len(t.dados):
                t.dados.pop(contato_id)
                t.salvar()
                return True
        return False

    # ── Alertas ──────────────────────────────────────────────────
    # No modo JSON o id de um alerta é a sua posição no arquivo (comportamento legado).
    def registrar_alerta(self, motorista_id, alerta, descricao=None):
        with transacao(self.alertas_arq, []) as t:
            novos = (t.dados + [alerta])[-500:]
            t.salvar(novos)
        return dict(alerta, id=len(novos) - 1)

    def listar_alertas(self, motoristas):
        if LEGADO not in motoristas:
            return []
        return [dict(a, id=i) for i, a in enumerate(ler_json(self.alertas_arq, []))]

    def resolver_alerta(self, motoristas, alerta_id, usuario_id=None):
        if LEGADO not in motoristas:
            return False
        with transacao(self.alertas_arq, []) as t:
            if 0 <= alerta_id < len(t.dados):
                t.dados[alerta_id]["status"] = "Resolvido"
                t.salvar()
                return True
        return False

    def limpar_alertas(self, motoristas):
        """Mantém uma cópia antes de limpar: a ação não é irreversível."""
        if LEGADO not in motoristas:
            return 0
        with transacao(self.alertas_arq, []) as t:
            n = len(t.dados)
            if t.dados:
                backup = os.path.join(self.data_dir,
                                      f"alerts_backup_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S_%f')}.json")
                gravar_json_novo(backup, t.dados)
            t.salvar([])
        return n

    # ── Viagens / Dead Man's Switch ──────────────────────────────
    # Ordem de travas: viagem → historico → alerts → contacts
    def iniciar_viagem(self, motorista_id, nome, minutos, lat, lng):
        viagem = {
            "id":          secrets.token_hex(8),
            "ativa":       True,
            "motorista":   nome,
            "inicio":      agora_ts(),
            "duracao":     minutos * 60,
            "expira_em":   agora_ts() + minutos * 60,
            "lat_inicio":  lat,
            "lng_inicio":  lng,
        }
        gravar_json(self.viagem_arq, viagem)
        with transacao(self.historico_arq, []) as th:
            th.salvar((th.dados + [{
                "inicio": agora_br_str(), "motorista": nome, "minutos": minutos,
                "lat": lat, "lng": lng, "resultado": "em_andamento",
            }])[-200:])
        return viagem

    @staticmethod
    def _encerrar_no_historico(hist, resultado):
        for v in reversed(hist):
            if v.get("resultado") == "em_andamento":
                v["resultado"] = resultado
                v["fim"] = agora_br_str()
                break
        return hist

    def encerrar_viagem(self, motorista_id, resultado):
        """resultado: 'chegou_segura' ou 'cancelada'."""
        with transacao(self.viagem_arq, {"ativa": False}) as tv:
            with transacao(self.historico_arq, []) as th:
                th.salvar(self._encerrar_no_historico(th.dados, resultado))
            tv.salvar({"ativa": False, "ultimo_fim": agora_br_str()} if resultado == "chegou_segura"
                      else {"ativa": False})
        return True

    def cancelar_viagem_por_sos(self, motorista_id):
        with transacao(self.viagem_arq, {"ativa": False}) as tv:
            if tv.dados.get("ativa"):
                tv.salvar({"ativa": False, "cancelado_por": "panic"})

    def status_viagem(self, motoristas):
        if LEGADO not in motoristas:
            return {"ativa": False}
        viagem = ler_json(self.viagem_arq, {"ativa": False})
        if not viagem.get("ativa"):
            return {"ativa": False}
        return {
            "ativa":     True,
            "motorista": viagem.get("motorista"),
            "restante":  max(0, int(viagem.get("expira_em", 0) - agora_ts())),
            "duracao":   viagem.get("duracao", 0),
        }

    def listar_historico(self, motoristas):
        if LEGADO not in motoristas:
            return []
        return list(reversed(ler_json(self.historico_arq, [])))

    def disparar_viagem(self, viagem_id=None):
        """Dispara o DMS da viagem atual se expirou. Atômico: só um chamador dispara.
        Retorna (motorista_id, alerta) ou None."""
        with transacao(self.viagem_arq, {"ativa": False}) as tv:
            viagem = tv.dados
            if not viagem.get("ativa"):
                return None
            if viagem_id is not None and viagem.get("id") != viagem_id:
                return None   # viagem que não é mais a atual
            if viagem.get("expira_em", 0) > agora_ts() + DMS_TOLERANCIA:
                return None   # ainda não expirou
            tv.salvar({"ativa": False, "ultimo_disparo": agora_br_str()})
        alerta = {
            "ts":         agora_br_str(),
            "motorista":  viagem.get("motorista", "Motorista"),
            "ocorrencia": "⏰ ALERTA AUTOMÁTICO — Motorista não confirmou chegada",
            "lat":        viagem.get("lat_inicio"),
            "lng":        viagem.get("lng_inicio"),
            "status":     "Ativo",
            "tipo":       "dead_mans_switch",
        }
        return LEGADO, self.registrar_alerta(LEGADO, alerta)

    def disparar_viagens_expiradas(self):
        viagem = ler_json(self.viagem_arq, {"ativa": False})
        if viagem.get("ativa") and viagem.get("expira_em", 0) <= agora_ts():
            r = self.disparar_viagem(viagem.get("id"))
            return [r] if r else []
        return []

    # ── Agendador: lease entre processos (arquivo + trava) ───────
    def adquirir_tarefa(self, nome, ttl, executor):
        with transacao(self.agenda_arq, {}) as t:
            if t.dados.get(nome, 0) > agora_ts():
                return False
            t.dados[nome] = agora_ts() + ttl
            t.salvar()
            return True

    # ── Auditoria (sem dados sensíveis) ──────────────────────────
    def auditar(self, acao, usuario_id=None, motorista_id=None, alvo=None, ip_hash=None, detalhes=None):
        linha = json.dumps({"em": agora_br_str(), "acao": acao, "usuario_id": usuario_id,
                            "motorista_id": motorista_id, "alvo": alvo, "ip_hash": ip_hash,
                            "detalhes": detalhes or {}}, ensure_ascii=False)
        with trava(self.auditoria_arq):
            with open(self.auditoria_arq, "a", encoding="utf-8") as f:
                f.write(linha + "\n")

    # ── LGPD ─────────────────────────────────────────────────────
    def exportar_dados(self, motorista_id):
        import lgpd
        return lgpd.exportar(self.data_dir)

    def relatorio_retencao(self, motoristas):
        import lgpd
        return lgpd.relatorio_retencao(self.data_dir)
