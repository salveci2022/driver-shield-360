# Driver Shield 360: arquitetura de produção (FASE 3)

## Decisão: PostgreSQL (definitivo), com JSON como padrão e rollback

| | Render Persistent Disk | PostgreSQL (escolhido) |
|---|---|---|
| Sobrevive a deploy e reinício | Sim | Sim |
| Vários workers, várias instâncias, worker separado | **Não**: o disco prende o serviço a uma instância | Sim |
| Backup e restauração | Snapshots do disco | Backups gerenciados com restauração para um ponto no tempo (planos pagos) |
| Isolamento por motorista e integridade | No código, sobre arquivos | Chaves estrangeiras, CHECK, índices únicos e transações |
| Migração | — | Incremental: `STORAGE_BACKEND` escolhe o modo; os JSONs ficam intactos |

O modo é escolhido por `STORAGE_BACKEND`:
- `json` (padrão): um motorista, senhas compartilhadas `MOTORISTA_SENHA` / `PAINEL_SENHA`. É também o caminho de rollback.
- `postgres`: multiusuário. Cada motorista e cada pessoa de confiança tem login e senha própria (hash scrypt).

## Componentes

| Arquivo | Papel |
|---|---|
| `repositorio_json.py` / `repositorio_pg.py` | Mesmo contrato de dados nos dois modos. **Toda consulta filtra por `motorista_id`, que vem da sessão** |
| `seguranca_db.py` | Sessões revogáveis e rate limit: SQLite no modo JSON, PostgreSQL no modo multiusuário |
| `agendador.py` | Agendador central com *lease* no banco. Substitui o `threading.Timer` |
| `db/migracoes/*.sql` + `migrar.py` | Migrations versionadas e aditivas. DROP, TRUNCATE e DELETE são recusados |
| `importar_json.py` | JSON → PostgreSQL: backup, validação, simulação, transação única, idempotente |
| `rollback_pg_para_json.py` | PostgreSQL → JSON numa pasta nova (ou em `data/`, com backup) |
| `gerenciar.py` | Contas: criar, vincular painel, trocar senha, desativar |

## Tabelas
`usuarios`, `motoristas`, `painel_acessos`, `contatos_confianca`, `veiculos`, `viagens`, `alertas`,
`ocorrencias`, `localizacoes`, `sessoes`, `tentativas`, `auditoria`, `tarefas_agendadas`, `importacoes`,
`schema_migrations`.

Garantias no próprio banco:
- no máximo **uma viagem em andamento** por motorista (índice único parcial);
- telefone único por motorista;
- coordenadas e tamanhos com CHECK;
- "Limpar" no painel **arquiva** os alertas (não apaga).

## Isolamento entre motoristas (IDOR/BOLA)
- O `motorista_id` de qualquer operação vem da **sessão no servidor**. Campos como `motorista_id` no corpo da requisição são ignorados.
- O painel só enxerga os motoristas de `painel_acessos`. Resolver ou limpar um alerta de outro motorista responde 404.
- A integração por `X-Shield-Token` exige o cabeçalho `X-Shield-Motorista` explícito.
- O WhatsApp só é enviado aos contatos **do próprio** motorista do alerta.
- Há testes para tudo isso em `tests/test_fase3_pg.py`.

## Dead Man's Switch com vários workers
1. A expiração fica no banco (`viagens.expira_em`).
2. O disparo é `UPDATE ... WHERE status='em_andamento' ... FOR UPDATE SKIP LOCKED RETURNING`: **só uma** transação dispara cada viagem, mesmo com vários processos ou instâncias.
3. Quem chama o disparo:
   - o **agendador**, a cada 10 s, com lease em `tarefas_agendadas`;
   - como rede de segurança, qualquer requisição (no máximo 1 vez a cada 30 s por worker).
4. O agendador pode rodar **embutido** (`AGENDADOR_EMBUTIDO=1`, uma thread por worker iniciada na primeira requisição) ou como **processo dedicado**: `python agendador.py`, com `AGENDADOR_EMBUTIDO=0` no serviço web.

> ⚠️ Se o serviço web hibernar (plano gratuito do Render) e não houver um processo dedicado sempre ativo, o DMS só dispara quando o serviço acordar. Para proteção real, use um **Background Worker** com `python agendador.py`.

---

## Configuração futura no Render (NÃO aplicada; seguir quando decidir publicar)

### 1. Criar o banco
Dashboard → New → **PostgreSQL** (mesma região do serviço web). Copie a **Internal Database URL**.

### 2. Variáveis de ambiente

| Variável | Serviço web | Worker (`agendador.py`) | Observação |
|---|---|---|---|
| `SECRET_KEY` | ✅ | ✅ (mesmo valor) | obrigatória |
| `API_SECRET` | ✅ | ✅ | obrigatória |
| `STORAGE_BACKEND` | `postgres` | `postgres` | padrão `json` |
| `DATABASE_URL` | ✅ | ✅ | Internal Database URL |
| `AGENDADOR_EMBUTIDO` | `0` (se houver worker) | — | padrão `1` |
| `ZAPI_INSTANCE`, `ZAPI_TOKEN`, `ZAPI_CLIENT_TOKEN` | ✅ | ✅ | o worker envia o WhatsApp do DMS |
| `ANTHROPIC_API_KEY`, `IA_MODELO` | opcional | — | Shield AI |
| `DS360_PROXY_HOPS` | `1` | — | padrão `1` |
| `MOTORISTA_SENHA`, `PAINEL_SENHA` | só no modo `json` | — | no PostgreSQL, só para a importação inicial |

**Nunca** defina `DS360_DEV` no Render.

### 3. Comandos
- Build: `pip install -r requirements.txt`
- **Pre-Deploy Command:** `python migrar.py aplicar`. O app **recusa iniciar** com migrations pendentes.
- Start (web): `gunicorn app:app --workers 2 --timeout 60` (**sem** `--preload`)
- Start (Background Worker): `python agendador.py`

### 4. Migração dos dados (uma vez, num Shell do Render ou localmente apontando para o banco)
```
python migrar.py aplicar
python importar_json.py --login-motorista <login> --nome "<Nome>" --login-painel <login_painel>            # simulação
python importar_json.py --login-motorista <login> --nome "<Nome>" --login-painel <login_painel> --executar
```
Depois, `STORAGE_BACKEND=postgres` e reiniciar.

O motorista e a pessoa de confiança passam a entrar com **usuário + senha**. As senhas iniciais vêm de `MOTORISTA_SENHA` e `PAINEL_SENHA`, se definidas; troque-as com `python gerenciar.py trocar-senha --login <login>`.

### 5. Rollback (se algo der errado)
1. `python rollback_pg_para_json.py --login-motorista <login>`: gera os JSONs numa pasta nova e confere.
2. `python rollback_pg_para_json.py --login-motorista <login> --aplicar`: grava em `data/`, com backup.
3. `STORAGE_BACKEND=json` e reiniciar. O banco não é alterado.

### 6. Dados pessoais e Git
`data/`, `data_backup_*`, `backup_fase*`, `*.db` e `.env` estão no `.gitignore`. Antes de qualquer `git push`, confira com `git status` que nenhum desses aparece.
