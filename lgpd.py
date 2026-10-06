"""
LGPD — inventário, retenção, exportação e exclusão de dados pessoais.

NADA aqui é executado automaticamente. A exclusão:
  - roda em modo simulação por padrão (mostra o que seria removido);
  - só remove com --executar;
  - sempre salva um backup completo antes (data/lgpd_backup_<data>/).

Uso (a partir da pasta do projeto):
  python lgpd.py inventario
  python lgpd.py retencao
  python lgpd.py exportar [--saida arquivo.json]
  python lgpd.py excluir --categoria alertas --antes-de-dias 365            (simulação)
  python lgpd.py excluir --categoria alertas --antes-de-dias 365 --executar (exclui de fato)

Variáveis opcionais: DS360_DATA_DIR, RETENCAO_ALERTAS_DIAS, RETENCAO_HISTORICO_DIAS.
"""
import argparse, json, os, shutil, sys
from datetime import datetime, timedelta, timezone

from armazenamento import ler_json, transacao

BR_TZ = timezone(timedelta(hours=-3))
FORMATO_DATA = "%d/%m/%Y %H:%M:%S"

# Prazos PROPOSTOS (não aplicados automaticamente). Ajustáveis por variável de ambiente.
def _dias(nome, padrao):
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao

INVENTARIO = {
    "contatos": {
        "arquivo": "contacts.json",
        "campos": ["nome", "telefone", "relacao"],
        "titulares": "Contatos de confiança indicados pelo motorista (terceiros)",
        "finalidade": "Enviar alertas de emergência (SOS, Dead Man's Switch, passageiro suspeito) via WhatsApp",
        "base_legal_sugerida": "Proteção da vida e da incolumidade física (art. 7º, VII) / legítimo interesse — validar com jurídico",
        "compartilhamento": "Z-API (envio de WhatsApp): telefone e conteúdo do alerta",
        "retencao_dias": None,   # enquanto o motorista mantiver o contato cadastrado
        "campo_data": None,
    },
    "alertas": {
        "arquivo": "alerts.json",
        "campos": ["ts", "motorista", "ocorrencia", "lat", "lng", "status", "tipo"],
        "titulares": "Motorista (nome, localização GPS); passageiro descrito em 'passageiro suspeito'",
        "finalidade": "Acionar e acompanhar ocorrências de segurança; relatório PDF para o painel de confiança",
        "base_legal_sugerida": "Proteção da vida (art. 7º, VII; art. 11, II, e quando houver dado sensível) — validar com jurídico",
        "compartilhamento": "Z-API (mensagem com nome, ocorrência e link do Google Maps) para os contatos",
        "retencao_dias": _dias("RETENCAO_ALERTAS_DIAS", 365),
        "campo_data": "ts",
    },
    "historico": {
        "arquivo": "historico_viagens.json",
        "campos": ["inicio", "fim", "motorista", "minutos", "lat", "lng", "resultado"],
        "titulares": "Motorista (nome, localização de início da viagem)",
        "finalidade": "Histórico de viagens do Modo Viagem / Dead Man's Switch",
        "base_legal_sugerida": "Execução do serviço contratado (art. 7º, V) — validar com jurídico",
        "compartilhamento": "Nenhum",
        "retencao_dias": _dias("RETENCAO_HISTORICO_DIAS", 180),
        "campo_data": "inicio",
    },
    "viagem": {
        "arquivo": "viagem.json",
        "campos": ["motorista", "inicio", "expira_em", "lat_inicio", "lng_inicio"],
        "titulares": "Motorista",
        "finalidade": "Estado da viagem em andamento (disparo automático do alerta)",
        "base_legal_sugerida": "Execução do serviço / proteção da vida",
        "compartilhamento": "Nenhum (os dados entram no alerta se o DMS disparar)",
        "retencao_dias": 0,      # sobrescrito ao fim de cada viagem
        "campo_data": None,
    },
}

# Dados pessoais fora dos JSONs (documentação — sem ação automática necessária)
OUTROS_DADOS = [
    {"onde": "data/seguranca.db (tentativas)", "dado": "IP do cliente, somente como HMAC-SHA256",
     "finalidade": "Bloquear força bruta no login e abuso da IA", "retencao": "apagado após a janela (15 min / 24 h)"},
    {"onde": "data/seguranca.db (sessoes)", "dado": "hash do identificador de sessão",
     "finalidade": "Autenticação e logout/revogação", "retencao": "até expirar (12 h painel / 30 dias motorista)"},
    {"onde": "Navegador do motorista (localStorage)", "dado": "nome do motorista, itens do checklist",
     "finalidade": "Conveniência (preencher o nome)", "retencao": "até o motorista limpar os dados do app"},
    {"onde": "Shield AI (/api/ia)", "dado": "texto digitado pelo motorista",
     "finalidade": "Responder perguntas de segurança",
     "retencao": "não gravado pelo sistema; enviado à Anthropic (transferência internacional — informar na política de privacidade)"},
    {"onde": "Logs do servidor (Render/gunicorn)", "dado": "IP e caminho das requisições (log de acesso)",
     "finalidade": "Operação e segurança", "retencao": "conforme a plataforma de hospedagem"},
]


def _caminho(data_dir, categoria):
    return os.path.join(data_dir, INVENTARIO[categoria]["arquivo"])


def _data_do_item(item, campo):
    try:
        return datetime.strptime(str(item.get(campo, "")), FORMATO_DATA).replace(tzinfo=BR_TZ)
    except ValueError:
        return None


def relatorio_retencao(data_dir, agora=None):
    """Somente leitura: quantos registros existem e quantos passaram do prazo proposto."""
    agora = agora or datetime.now(BR_TZ)
    rel = {}
    for cat, info in INVENTARIO.items():
        dados = ler_json(_caminho(data_dir, cat), [] if cat != "viagem" else {})
        itens = dados if isinstance(dados, list) else ([dados] if dados else [])
        vencidos = 0
        if info["retencao_dias"] and info["campo_data"]:
            limite = agora - timedelta(days=info["retencao_dias"])
            vencidos = sum(1 for i in itens
                           if (d := _data_do_item(i, info["campo_data"])) and d < limite)
        rel[cat] = {"total": len(itens), "retencao_dias": info["retencao_dias"],
                    "vencidos_pelo_prazo_proposto": vencidos}
    return rel


def exportar(data_dir):
    """Todos os dados pessoais armazenados, em formato portável (direito de acesso/portabilidade)."""
    return {
        "gerado_em": datetime.now(BR_TZ).strftime(FORMATO_DATA),
        "dados": {cat: ler_json(_caminho(data_dir, cat), [] if cat != "viagem" else {})
                  for cat in INVENTARIO},
        "inventario": INVENTARIO,
        "outros_dados": OUTROS_DADOS,
    }


def excluir(data_dir, categoria, antes_de_dias=None, executar=False):
    """Remove registros de uma categoria (todos, ou apenas os anteriores a N dias).
    Modo simulação por padrão. Com executar=True, faz backup completo antes."""
    if categoria not in ("alertas", "historico", "contatos"):
        raise ValueError("Categoria inválida (use: alertas, historico, contatos)")
    info = INVENTARIO[categoria]
    if antes_de_dias is not None and not info["campo_data"]:
        raise ValueError(f"A categoria '{categoria}' não tem data; use exclusão total")
    caminho = _caminho(data_dir, categoria)
    limite = datetime.now(BR_TZ) - timedelta(days=antes_de_dias) if antes_de_dias is not None else None

    def remover(item):
        if limite is None:
            return True
        d = _data_do_item(item, info["campo_data"])
        return d is not None and d < limite

    with transacao(caminho, []) as t:
        manter = [i for i in t.dados if not remover(i)]
        removidos = len(t.dados) - len(manter)
        resultado = {"categoria": categoria, "removeria": removidos, "manteria": len(manter),
                     "executado": False, "backup": None}
        if executar and removidos:
            pasta = os.path.join(data_dir, f"lgpd_backup_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S_%f')}")
            os.makedirs(pasta)
            for cat in INVENTARIO:
                origem = _caminho(data_dir, cat)
                if os.path.exists(origem):
                    shutil.copy2(origem, pasta)
            t.salvar(manter)
            resultado.update(executado=True, backup=pasta)
    return resultado


def _main(argv=None):
    data_dir = os.environ.get("DS360_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    ap = argparse.ArgumentParser(description="Ferramentas LGPD do Driver Shield 360")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventario")
    sub.add_parser("retencao")
    pe = sub.add_parser("exportar")
    pe.add_argument("--saida")
    px = sub.add_parser("excluir")
    px.add_argument("--categoria", required=True)
    px.add_argument("--antes-de-dias", type=int)
    px.add_argument("--executar", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "inventario":
        out = {"json": INVENTARIO, "outros": OUTROS_DADOS}
    elif a.cmd == "retencao":
        out = relatorio_retencao(data_dir)
    elif a.cmd == "exportar":
        out = exportar(data_dir)
        if a.saida:
            with open(a.saida, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"Exportado para {a.saida}")
            return 0
    else:
        out = excluir(data_dir, a.categoria, a.antes_de_dias, a.executar)
        if not a.executar:
            print("MODO SIMULAÇÃO — nada foi excluído. Use --executar para excluir (com backup).")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
