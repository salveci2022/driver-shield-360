"""
Administração de contas (modo PostgreSQL). Não existe cadastro público: contas são
criadas por um administrador por aqui.

  python gerenciar.py criar-motorista --login maria --nome "Maria Silva"
  python gerenciar.py criar-painel --login familia
  python gerenciar.py vincular-painel --painel familia --motorista maria
  python gerenciar.py trocar-senha --login maria          (invalida as sessões abertas)
  python gerenciar.py desativar --login maria             (bloqueia o acesso, mantém os dados)

A senha é digitada no terminal (não aparece) ou lida de GERENCIAR_SENHA. Mínimo 10 caracteres.
Requer DATABASE_URL.
"""
import argparse, getpass, os, sys


def _senha():
    s = os.environ.get("GERENCIAR_SENHA") or getpass.getpass("Senha (mín. 10 caracteres): ")
    if len(s) < 10:
        raise SystemExit("Senha muito curta.")
    return s


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Contas do Driver Shield 360 (PostgreSQL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("criar-motorista"); p.add_argument("--login", required=True); p.add_argument("--nome")
    p = sub.add_parser("criar-painel"); p.add_argument("--login", required=True)
    p = sub.add_parser("vincular-painel"); p.add_argument("--painel", required=True); p.add_argument("--motorista", required=True)
    p = sub.add_parser("trocar-senha"); p.add_argument("--login", required=True)
    p = sub.add_parser("desativar"); p.add_argument("--login", required=True)
    p = sub.add_parser("reativar"); p.add_argument("--login", required=True)
    a = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL não definida."); return 2
    from repositorio_pg import RepositorioPG
    repo = RepositorioPG(url, minconn=1, maxconn=2)
    try:
        if a.cmd == "criar-motorista":
            print(repo.criar_usuario(a.login, _senha(), "motorista", nome=a.nome))
        elif a.cmd == "criar-painel":
            print(repo.criar_usuario(a.login, _senha(), "painel"))
        else:
            alvo = repo.buscar_usuario(a.painel if a.cmd == "vincular-painel" else a.login)
            if not alvo:
                print("Usuário não encontrado."); return 2
            if a.cmd == "vincular-painel":
                m = repo.buscar_usuario(a.motorista)
                if not m or m["motorista_id"] is None or alvo["papel"] != "painel":
                    print("Painel ou motorista inválido."); return 2
                repo.vincular_painel(alvo["usuario_id"], m["motorista_id"])
            elif a.cmd == "trocar-senha":
                repo.trocar_senha(alvo["usuario_id"], _senha())
            elif a.cmd == "desativar":
                repo.definir_ativo(alvo["usuario_id"], False)
            elif a.cmd == "reativar":
                repo.definir_ativo(alvo["usuario_id"], True)
            print("OK")
    finally:
        repo.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
