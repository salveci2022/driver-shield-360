"""
Gera uma cópia ANONIMIZADA de data/*.json para testar a importação na HOMOLOGAÇÃO.

- Os arquivos originais não são alterados (somente leitura).
- A pasta de saída precisa ser nova (nunca sobrescreve).
- Nomes → "Contato Teste N" / "Motorista Teste".
- Telefones dos contatos → os telefones DA EQUIPE informados em --telefone-teste
  (assim nenhum WhatsApp de teste chega a um contato real).
- GPS → coordenada fixa de teste (Esplanada dos Ministérios, Brasília).
- Ocorrências livres (ex.: descrição de passageiro) → texto genérico;
  apenas os tipos fixos do botão SOS são mantidos.
- Datas, quantidades, status e tipos são preservados (testam o importador de verdade).

Uso:
  python homologacao/anonimizar_para_staging.py --saida C:\\temp\\dados_staging --telefone-teste 5561999990000
"""
import argparse, json, os, sys

TIPOS_SOS = {"Emergência Geral", "Passageiro Suspeito", "Perseguição", "Acidente", "Assalto",
             "Pane / Parado", "Área Perigosa", "Outro"}
LAT_TESTE, LNG_TESTE = "-15.799800", "-47.864500"
ARQUIVOS = ("contacts.json", "alerts.json", "historico_viagens.json", "viagem.json")


def _ler(pasta, nome, padrao):
    caminho = os.path.join(pasta, nome)
    if not os.path.exists(caminho):
        return padrao
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _coord(item, chave_lat, chave_lng):
    if item.get(chave_lat) not in (None, "") and item.get(chave_lng) not in (None, ""):
        return LAT_TESTE, LNG_TESTE
    return None, None


def anonimizar(origem, destino, telefones_teste):
    if not telefones_teste:
        raise ValueError("Informe ao menos um --telefone-teste (telefone da equipe)")
    for t in telefones_teste:
        if sum(ch.isdigit() for ch in t) < 10:
            raise ValueError("Telefone de teste inválido (use DDI+DDD+número)")
    os.makedirs(destino, exist_ok=False)   # nunca sobrescreve

    contatos = _ler(origem, "contacts.json", [])
    contatos_anon = [{"nome": f"Contato Teste {i + 1}",
                      "telefone": telefones_teste[i % len(telefones_teste)],
                      "relacao": c.get("relacao", "Contato") if isinstance(c, dict) else "Contato"}
                     for i, c in enumerate(contatos if isinstance(contatos, list) else [])]
    # telefones repetidos (mais contatos que telefones de teste) viram duplicados → o importador ignora
    alertas_anon = []
    for a in _ler(origem, "alerts.json", []):
        if not isinstance(a, dict):
            continue
        lat, lng = _coord(a, "lat", "lng")
        oc = a.get("ocorrencia", "")
        novo = {"ts": a.get("ts"), "motorista": "Motorista Teste",
                "ocorrencia": oc if oc in TIPOS_SOS else "Ocorrência de teste",
                "lat": lat, "lng": lng, "status": a.get("status", "Ativo")}
        if a.get("tipo"):
            novo["tipo"] = a["tipo"]
        alertas_anon.append(novo)
    historico_anon = []
    for v in _ler(origem, "historico_viagens.json", []):
        if not isinstance(v, dict):
            continue
        lat, lng = _coord(v, "lat", "lng")
        novo = {"inicio": v.get("inicio"), "motorista": "Motorista Teste", "minutos": v.get("minutos"),
                "lat": lat, "lng": lng, "resultado": v.get("resultado")}
        if v.get("fim"):
            novo["fim"] = v["fim"]
        historico_anon.append(novo)

    saida = {"contacts.json": contatos_anon, "alerts.json": alertas_anon,
             "historico_viagens.json": historico_anon, "viagem.json": {"ativa": False}}
    for nome, dados in saida.items():
        with open(os.path.join(destino, nome), "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    return {nome: len(d) if isinstance(d, list) else 1 for nome, d in saida.items()}


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Cópia anonimizada de data/ para a homologação")
    ap.add_argument("--origem", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
    ap.add_argument("--saida", required=True, help="Pasta NOVA (fora do repositório)")
    ap.add_argument("--telefone-teste", action="append", required=True, help="Telefone da equipe (repita para vários)")
    a = ap.parse_args(argv)
    print(json.dumps(anonimizar(a.origem, a.saida, a.telefone_teste), indent=2))
    print("Pronto. Os arquivos originais não foram alterados.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
