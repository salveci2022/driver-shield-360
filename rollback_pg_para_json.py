"""
ROLLBACK: PostgreSQL → JSON (volta ao modo STORAGE_BACKEND=json sem perder o que foi
registrado depois da migração).

Passos de rollback completo:
  1. python rollback_pg_para_json.py --login-motorista maria            (gera JSONs numa pasta NOVA)
  2. conferir os arquivos gerados
  3. python rollback_pg_para_json.py --login-motorista maria --aplicar  (copia para data/, com backup)
  4. definir STORAGE_BACKEND=json e reiniciar o app

Nada é apagado do PostgreSQL. --aplicar sempre salva data/*.json atuais em
data/backup_rollback_<data>/ antes de substituí-los.
"""
import argparse, json, os, shutil, sys
from datetime import datetime, timedelta, timezone

import psycopg2.extras

BR_TZ = timezone(timedelta(hours=-3))
ARQUIVOS = ("contacts.json", "alerts.json", "historico_viagens.json", "viagem.json")
RESULTADO_JSON = {"em_andamento": "em_andamento", "chegou_segura": "chegou_segura",
                  "dead_mans_switch": "dead_mans_switch", "cancelada": "cancelada",
                  "cancelada_por_sos": "cancelada", "interrompida": "cancelada"}


def _br(dt):
    return dt.astimezone(BR_TZ).strftime("%d/%m/%Y %H:%M:%S") if dt else None


def _c(v):
    return f"{float(v):.6f}" if v is not None else None


def exportar_formato_json(conn, motorista_id):
    """Reconstrói os 4 arquivos do modo JSON a partir do banco (somente leitura)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT nome, telefone, relacao FROM contatos_confianca WHERE motorista_id = %s ORDER BY id",
                    (motorista_id,))
        contatos = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM (SELECT criado_em, id, nome_exibido, ocorrencia, lat, lng, status, tipo FROM alertas "
                    "WHERE motorista_id = %s AND arquivado_em IS NULL ORDER BY criado_em DESC, id DESC LIMIT 500) x "
                    "ORDER BY criado_em, id", (motorista_id,))
        alertas = [{"ts": _br(r["criado_em"]), "motorista": r["nome_exibido"], "ocorrencia": r["ocorrencia"],
                    "lat": _c(r["lat"]), "lng": _c(r["lng"]), "status": r["status"], "tipo": r["tipo"]}
                   for r in cur.fetchall()]
        cur.execute("SELECT * FROM (SELECT inicio, id, fim, nome_exibido, minutos, lat_inicio, lng_inicio, status "
                    "FROM viagens WHERE motorista_id = %s ORDER BY inicio DESC, id DESC LIMIT 200) x "
                    "ORDER BY inicio, id", (motorista_id,))
        historico = []
        for r in cur.fetchall():
            item = {"inicio": _br(r["inicio"]), "motorista": r["nome_exibido"], "minutos": r["minutos"],
                    "lat": _c(r["lat_inicio"]), "lng": _c(r["lng_inicio"]),
                    "resultado": RESULTADO_JSON.get(r["status"], "cancelada")}
            if r["fim"]:
                item["fim"] = _br(r["fim"])
            historico.append(item)
        cur.execute("SELECT publico_id, nome_exibido, inicio, minutos, expira_em, lat_inicio, lng_inicio FROM viagens "
                    "WHERE motorista_id = %s AND status = 'em_andamento'", (motorista_id,))
        r = cur.fetchone()
        viagem = {"ativa": False} if not r else {
            "id": r["publico_id"], "ativa": True, "motorista": r["nome_exibido"],
            "inicio": r["inicio"].timestamp(), "duracao": r["minutos"] * 60,
            "expira_em": r["expira_em"].timestamp(),
            "lat_inicio": _c(r["lat_inicio"]), "lng_inicio": _c(r["lng_inicio"])}
    return {"contacts.json": contatos, "alerts.json": alertas,
            "historico_viagens.json": historico, "viagem.json": viagem}


def gravar_pasta(arquivos, destino):
    os.makedirs(destino, exist_ok=False)   # nunca sobrescreve uma pasta existente
    for nome, dados in arquivos.items():
        with open(os.path.join(destino, nome), "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    return destino


def aplicar_em_data(arquivos, data_dir):
    """Substitui data/*.json pelos arquivos do rollback, com backup prévio dos atuais."""
    from armazenamento import gravar_json
    backup = os.path.join(data_dir, f"backup_rollback_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S_%f')}")
    os.makedirs(backup)
    for nome in ARQUIVOS:
        atual = os.path.join(data_dir, nome)
        if os.path.exists(atual):
            shutil.copy2(atual, backup)
    for nome, dados in arquivos.items():
        gravar_json(os.path.join(data_dir, nome), dados)   # escrita atômica com trava
    return backup


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Rollback PostgreSQL → JSON")
    ap.add_argument("--login-motorista", required=True)
    ap.add_argument("--data-dir", default=os.environ.get("DS360_DATA_DIR") or
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--saida", help="Pasta nova para os JSONs gerados (padrão: data/rollback_<data>)")
    ap.add_argument("--aplicar", action="store_true", help="Substitui data/*.json (com backup)")
    a = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL não definida."); return 2
    import psycopg2
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT m.id FROM motoristas m JOIN usuarios u ON u.id = m.usuario_id "
                        "WHERE lower(u.login) = lower(%s)", (a.login_motorista,))
            r = cur.fetchone()
        if not r:
            print("Motorista não encontrado."); return 2
        arquivos = exportar_formato_json(conn, r[0])
    finally:
        conn.close()
    if a.aplicar:
        print("Backup dos JSONs atuais em:", aplicar_em_data(arquivos, a.data_dir))
        print("data/ atualizado. Defina STORAGE_BACKEND=json e reinicie o app.")
    else:
        destino = a.saida or os.path.join(a.data_dir, f"rollback_{datetime.now(BR_TZ).strftime('%Y%m%d_%H%M%S')}")
        print("JSONs gerados em:", gravar_pasta(arquivos, destino), "(data/ não foi alterado)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
