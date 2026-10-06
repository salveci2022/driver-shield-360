# Checklist do Render: Driver Shield 360

> Nada aqui foi aplicado. Siga **na ordem** quando decidir publicar.
> Recomendação: faça primeiro num **ambiente de homologação** (serviços e banco separados, com nomes `-staging`) e só depois repita em produção.

---

## 0. Antes de tudo (máquina local)

- [ ] Repositório GitHub **privado**.
- [ ] `git status` **não** lista `data/`, `data_backup_*`, `backup_fase*`, `*.db`, `.env`, `*.lock`, `lgpd_backup_*`, `alerts_backup_*`. O `.gitignore` já cobre todos; o teste `test_git_nao_versiona_dados_pessoais` verifica isso.
- [ ] Se `data/` **já esteve** em algum commit: tratar como incidente de LGPD e remover do histórico antes de publicar.
- [ ] Suíte completa aprovada: `pytest tests` com `DS360_TEST_PG_ADMIN_URL` definida.
- [ ] Gerar os segredos, **um valor diferente para cada** variável:
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`

> ⚠️ O código novo **não inicia** sem as variáveis obrigatórias. Configure as variáveis **antes** de publicar o código no serviço web atual.

---

## 1. PostgreSQL
- [ ] Dashboard → **New → PostgreSQL**.
  - Região: **a mesma** do Web Service.
  - Versão: 16.
  - Plano: com backup e restauração (planos pagos).
- [ ] Anote a **Internal Database URL** (para os serviços) e a **External Database URL** (para migração e importação a partir da sua máquina; use com `?sslmode=require`).
- [ ] Em *Access Control*, libere a External URL só para o seu IP, e só durante a migração.

## 2. Migrations (antes de qualquer serviço usar o banco)
Na sua máquina:
```
set DATABASE_URL=<External Database URL>?sslmode=require
python migrar.py status
python migrar.py aplicar
python migrar.py status          → pendentes: []
```
Ou configure o Pre-Deploy Command, se o seu plano tiver essa opção (passo 4).

## 3. Importação dos dados atuais (data/*.json → PostgreSQL)
Na sua máquina, com a mesma `DATABASE_URL`:
```
python importar_json.py --login-motorista <login> --nome "<Nome>" --login-painel <login_painel>
```
- [ ] Simulação: confira o `resumo` e os `problemas` (inválidos e duplicados).
```
set MOTORISTA_SENHA=<senha inicial do motorista, mín. 10>
set PAINEL_SENHA=<senha inicial do painel, mín. 10>
python importar_json.py --login-motorista <login> --nome "<Nome>" --login-painel <login_painel> --executar
```
- [ ] Guarde a pasta `data/backup_importacao_<data>/`, que tem o manifesto SHA-256 e o relatório.
- [ ] Rodar o comando de novo deve mostrar `"importados": {"contatos": 0, "alertas": 0, "viagens": 0}` (idempotente).
- [ ] Os JSONs originais continuam intactos e são o rollback.

## 4. Web Service
| Campo | Valor |
|---|---|
| Runtime | Python |
| **Build Command** | `pip install --upgrade pip && pip install -r requirements.txt` |
| **Pre-Deploy Command** | `python migrar.py aplicar` |
| **Start Command** | `gunicorn app:app --workers 2 --timeout 60 --access-logfile -` (**sem** `--preload`) |
| Health Check Path | `/` |
| Instância | paga, sem hibernação (recomendado) |

### Variáveis do Web Service
| Variável | Valor | Obrigatória |
|---|---|---|
| `PYTHON_VERSION` | `3.13.7` (testado; 3.14 também testado) | recomendada |
| `SECRET_KEY` | segredo gerado | **sim** |
| `API_SECRET` | segredo gerado | **sim** |
| `STORAGE_BACKEND` | `postgres` | **sim** (sem ela: modo JSON) |
| `DATABASE_URL` | **Internal** Database URL | **sim** com postgres |
| `AGENDADOR_EMBUTIDO` | `0` (o worker dedicado agenda) | **sim** com worker |
| `ZAPI_INSTANCE` | da Z-API | sim, para o WhatsApp |
| `ZAPI_TOKEN` | da Z-API | sim, para o WhatsApp |
| `ZAPI_CLIENT_TOKEN` | da Z-API | sim, para o WhatsApp |
| `DS360_PROXY_HOPS` | `1` | opcional (padrão 1) |
| `ANTHROPIC_API_KEY` | chave da Anthropic | opcional (Shield AI) |
| `IA_MODELO` | `claude-opus-5-5` | opcional |
| `MOTORISTA_SENHA` / `PAINEL_SENHA` | — | **não** usar com postgres (só no modo JSON) |
| `DS360_DEV` | — | **NUNCA** definir |

## 5. Background Worker (agendador: Dead Man's Switch)
| Campo | Valor |
|---|---|
| Repositório e branch | os mesmos do Web Service |
| **Build Command** | `pip install --upgrade pip && pip install -r requirements.txt` |
| **Start Command** | `python agendador.py` |
| Instância | paga, sempre ativa |

Variáveis: as **mesmas** do Web Service (`SECRET_KEY`, `API_SECRET`, `STORAGE_BACKEND=postgres`, `DATABASE_URL` interna, `ZAPI_*`, `PYTHON_VERSION`). O `agendador.py` força `AGENDADOR_EMBUTIDO=0` no próprio processo.

> Crie **um único** worker. Vários não geram alertas duplicados (o disparo é atômico e há um índice único), mas são desnecessários.

## 6. Ordem de ativação
1. PostgreSQL criado → migrations aplicadas → dados importados (passos 1 a 3).
2. Variáveis do Web Service configuradas (passo 4) **antes** de publicar o código.
3. Deploy do Web Service. Confira nos logs: `Armazenamento: postgres` e nenhum erro.
4. Criar e iniciar o Background Worker. Confira no log: `[AGENDADOR] Iniciado (2 tarefas, ...)`.
5. Testes pós-deploy (passo 7).

## 7. Testes pós-deploy (num celular real, com HTTPS)
- [ ] `/motorista/login` → entrar com usuário e senha do motorista.
- [ ] O GPS pede permissão e mostra "GPS ativo".
- [ ] **SOS**: segurar 1,5 s → toast "Alerta enviado". O **WhatsApp chega** em todos os contatos, com o link do mapa.
- [ ] SOS pelo volume (3 toques, Android).
- [ ] Contatos: adicionar, remover, limite de 5, duplicado recusado.
- [ ] **Modo Viagem** de 10 min: confirmar chegada.
- [ ] **Modo Viagem** de 10 min **sem confirmar**: o alerta automático chega por WhatsApp em até ~10 s depois de expirar.
- [ ] Passageiro suspeito.
- [ ] `/painel/login` (usuário do painel): ativar painel, sirene, alertas aparecem, resolver, PDF baixa.
- [ ] Um segundo motorista de teste **não** vê nada do primeiro, e um painel de outra pessoa também não.
- [ ] Checklist; instalar como app (PWA); "Sair desta conta".
- [ ] Shield AI (se configurado): resposta chega; sem chave aparece "não configurado".
- [ ] Logs do Render: **nenhuma** senha, token, telefone completo ou URL do banco. Nenhuma linha `[CSP] Violação`.
- [ ] Headers (DevTools → Network): `Content-Security-Policy`, `Strict-Transport-Security`, cookie `__Host-ds360_sessao` com `Secure; HttpOnly; SameSite=Lax`.

## 8. Rollback
**Código com problema:** Render → Web Service → *Deploys* → **Rollback** para o deploy anterior. O banco não é afetado; as migrations são só aditivas.

**Voltar ao modo JSON**, sem perder o que foi registrado no PostgreSQL:
1. Localmente, com a External URL: `python rollback_pg_para_json.py --login-motorista <login>` → confira a pasta gerada.
2. `python rollback_pg_para_json.py --login-motorista <login> --aplicar` (faz backup de `data/` antes).
3. No Render: `STORAGE_BACKEND=json`, `MOTORISTA_SENHA` e `PAINEL_SENHA` definidas, `AGENDADOR_EMBUTIDO=1`. Suspender o worker.
4. Atenção: o modo JSON precisa de disco persistente para não perder dados a cada deploy.

**Nunca** use DROP, TRUNCATE ou "Delete database" como forma de rollback.
