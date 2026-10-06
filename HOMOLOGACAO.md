# Homologação (staging) no Render: configuração para revisão

> **Status: PREPARADO, NADA CRIADO.** Nenhum serviço, banco, variável, branch ou DNS foi criado ou alterado.
> Cada passo marcado **🔒 APROVAÇÃO** só deve ser executado depois da sua confirmação explícita.

## Princípios
- **Tudo separado da produção:** banco, Web Service e Worker novos, com sufixo `-staging`. O serviço atual (`driver-shield-360`) continua intocado.
- **Sem DNS:** a homologação usa só o endereço `*.onrender.com`.
- **Sem dados reais:** contas de teste e, opcionalmente, uma cópia **anonimizada** dos JSONs. Os contatos de confiança são **telefones da equipe**, para que nenhum alerta de teste chegue a um contato real.
- **Segredos próprios:** `SECRET_KEY` e `API_SECRET` são gerados pelo Render, diferentes da produção. A Z-API idealmente usa uma **instância só de testes**.
- **Nada irreversível:** banco novo; migrations só aditivas; `autoDeployTrigger: off`.

## Custo estimado (preços do Render na data deste documento)
| Recurso | Plano | Motivo |
|---|---|---|
| Web Service | **Starter** (pago) | O Pre-Deploy Command só existe em instâncias pagas; não hiberna; tem Shell |
| Background Worker | **Starter** (pago) | Worker não tem plano gratuito |
| PostgreSQL | **Free** | Suficiente para homologação: **expira em 30 dias e não tem backup** |

---

## Passo 1: preparar o código no GitHub 🔒 APROVAÇÃO
O Render publica a partir do GitHub, mas esta pasta local **não é um repositório Git**. Será preciso:
1. Clonar o repositório privado existente numa pasta separada.
2. Criar a branch **`homologacao`** a partir da principal (a produção continua na branch atual).
3. Copiar para o clone **somente o código**: o `.gitignore` já exclui `data/`, backups, `*.db`, `.env` e `*.lock`.
4. Antes do commit, rodar `git status` e conferir que **nenhum** desses aparece.
5. `git push origin homologacao`. Isso **não** dispara deploy: a produção não segue essa branch, e os serviços de staging terão auto-deploy desligado.

> ⚠️ Se `data/` já esteve em commits antigos do repositório, os dados estão no histórico: precisa de limpeza separada (tema de LGPD).

## Passo 2: criar os recursos 🔒 APROVAÇÃO (gera custo)
**Opção A (recomendada): Blueprint**
Dashboard → **New → Blueprint** → repositório → branch `homologacao` → **Blueprint path: `render-staging.yaml`**.
O Render mostra tudo o que vai criar **antes** de confirmar. Na tela, digite os valores marcados `sync: false`: os três `ZAPI_*` da **instância de teste**.

**Opção B: manual pelo Dashboard**, com exatamente os valores abaixo.

### PostgreSQL
| Campo | Valor |
|---|---|
| Nome | `driver-shield-360-db-staging` |
| Database / User | `ds360_staging` / `ds360_staging` |
| Região | **Virginia** (US East, a mais próxima do Brasil) |
| Versão | **16** (a testada) |
| Plano | Free |
| Acesso externo | **nenhum** (lista de IPs vazia) |

### Web Service
| Campo | Valor |
|---|---|
| Nome | `driver-shield-360-staging` |
| Branch | `homologacao` |
| Região | Virginia (**a mesma do banco**) |
| Runtime / Plano | Python / Starter |
| Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
| Pre-Deploy Command | `python migrar.py aplicar` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60 --access-logfile -` |
| Health Check Path | `/saude` (responde 503 se o banco não responder) |
| Auto-Deploy | **Off** |

### Background Worker
| Campo | Valor |
|---|---|
| Nome | `driver-shield-360-agendador-staging` |
| Branch / Região / Plano | `homologacao` / Virginia / Starter |
| Build Command | o mesmo do Web Service |
| Start Command | `python agendador.py` |
| Auto-Deploy | **Off** |

### Variáveis de ambiente
| Variável | Web | Worker | Valor |
|---|---|---|---|
| `SECRET_KEY` | ✅ | ✅ | **gerado pelo Render** (o mesmo nos dois, via grupo `ds360-staging-comum`) |
| `API_SECRET` | ✅ | ✅ | gerado pelo Render (grupo) |
| `STORAGE_BACKEND` | ✅ | ✅ | `postgres` |
| `PYTHON_VERSION` | ✅ | ✅ | `3.14.3` |
| `DATABASE_URL` | ✅ | ✅ | **Internal** URL do `driver-shield-360-db-staging` |
| `ZAPI_INSTANCE` / `ZAPI_TOKEN` / `ZAPI_CLIENT_TOKEN` | ✅ | ✅ | digitados: **instância Z-API separada, só para testes** (decisão aprovada) |
| `URL_PUBLICA` | ✅ | ✅ | `https://driver-shield-360-staging.onrender.com` (**confira a URL real** depois de criar) |
| `AGENDADOR_EMBUTIDO` | `0` | — | o web **não** agenda; só o worker |
| `DS360_PROXY_HOPS` | `1` | — | |
| `ANTHROPIC_API_KEY` | ❌ | ❌ | **não definir**: Shield AI desligado nesta homologação (decisão aprovada) |
| `MOTORISTA_SENHA`, `PAINEL_SENHA` | — | — | **não usar** (cada conta tem a própria senha) |
| `DS360_DEV` | ❌ | ❌ | **nunca** |

## Passo 3: primeiro deploy 🔒 APROVAÇÃO
1. **Primeiro o Web Service:** o Pre-Deploy aplica as migrations (`0001` e `0002`). No log: `Aplicadas: 0001_esquema_inicial.sql, 0002_idempotencia_dms.sql`.
2. **Depois o Worker:** se ele subir antes das migrations, recusa iniciar e o Render tenta de novo sozinho. No log: `[AGENDADOR] Iniciado (2 tarefas, passo 5.0s)`.
3. Abra `https://<url-staging>/saude` → `{"ok": true}`.
4. Se a URL real for diferente da prevista, ajuste `URL_PUBLICA` nos dois serviços e reinicie.

## Passo 4: contas de teste 🔒 APROVAÇÃO
No **Shell** do Web Service de staging (o plano Starter tem Shell):
```
python gerenciar.py criar-motorista --login teste_motorista_a --nome "Motorista Teste A"
python gerenciar.py criar-motorista --login teste_motorista_b --nome "Motorista Teste B"
python gerenciar.py criar-painel    --login teste_painel_a
python gerenciar.py vincular-painel --painel teste_painel_a --motorista teste_motorista_a
```
A senha é digitada sem aparecer na tela; mínimo de 10 caracteres.

**Opcional: testar o importador com o formato real dos dados, sem dados pessoais**
1. Localmente: `python homologacao/anonimizar_para_staging.py --saida C:\temp\dados_staging --telefone-teste 55DDDNUMERO_DA_EQUIPE`
2. Libere **temporariamente** o seu IP no acesso externo do banco de staging e use a External URL com `?sslmode=require`:
   `python importar_json.py --login-motorista teste_importado --data-dir C:\temp\dados_staging` (simulação) → depois com `--executar`
3. Rode de novo: deve mostrar `importados: 0` (idempotência). **Remova o seu IP da lista** em seguida.

---

## Passo 5: checklist de testes no celular
Material: **celular 1** (motorista A; Android, se possível, para o botão de volume), **celular 2** (painel), **celular 3 ou WhatsApp da equipe** (contato de confiança). Use os dados móveis num dos testes.
Anote ✅ ou ❌, o horário e um print de cada item.

### A. Acesso e sessão
| # | Teste | Esperado |
|---|---|---|
| A1 | Abrir `/motorista` sem login | redireciona para `/motorista/login` |
| A2 | Login com senha errada 5 vezes | 6ª tentativa: "Muitas tentativas" (429) |
| A3 | Login correto (motorista A) | tela SOS, GPS pedindo permissão |
| A4 | Fechar e reabrir o navegador | continua logado (sessão de 30 dias) |
| A5 | "Sair desta conta" | volta ao login; "voltar" do navegador não mostra dados |

### B. GPS e SOS
| # | Teste | Esperado |
|---|---|---|
| B1 | Permitir localização | faixa verde "GPS ativo — lat, lng" |
| B2 | Toque rápido no SOS | **não** dispara (precisa segurar 1,5 s) |
| B3 | Segurar o SOS 1,5 s | botão gira, vibra, "Alerta enviado com GPS!" |
| B4 | WhatsApp do contato | chega em até ~10 s, com tipo, horário, link do mapa no local certo e link **do staging** |
| B5 | Painel (celular 2) | sirene toca, alerta aparece com o mapa |
| B6 | SOS com o GPS negado | alerta "sem localização"; WhatsApp diz "Localização não disponível" |
| B7 | SOS pelo volume, 3 toques rápidos (Android) | dispara o SOS |
| B8 | SOS sem internet (modo avião) | mensagem "Sem conexão"; ao voltar a rede, disparar de novo funciona |

### C. Modo Viagem / Dead Man's Switch
| # | Teste | Esperado |
|---|---|---|
| C1 | Iniciar 10 min e confirmar chegada | timer some; histórico "chegou segura"; **nenhum** WhatsApp |
| C2 | Iniciar 10 min e cancelar | histórico "cancelada"; nenhum alerta |
| C3 | Iniciar 10 min e **não** confirmar (pode fechar o app) | entre 10:00 e ~10:15: **um único** WhatsApp "ALERTA AUTOMÁTICO" e alerta no painel |
| C4 | No meio do C3, reiniciar o Web Service no Render | o alerta automático chega no horário, **uma vez só** (é o worker que dispara) |
| C5 | Iniciar viagem e disparar SOS | a viagem é cancelada e **não** gera alerta automático depois |
| C6 | Painel durante a viagem | faixa com o nome e a contagem regressiva |

### D. Contatos, passageiro suspeito, histórico
| # | Teste | Esperado |
|---|---|---|
| D1 | Cadastrar o 1º contato (telefone da equipe) | "Contato cadastrado" |
| D2 | Cadastrar o mesmo telefone de novo | "Esse telefone já está cadastrado" |
| D3 | Cadastrar até 5; tentar o 6º | "Limite atingido" |
| D4 | Remover um contato | pede confirmação; contato removido |
| D5 | Passageiro suspeito (se houver botão ou integração) | alerta "Passageiro suspeito" e WhatsApp |
| D6 | Histórico | viagens dos testes C1 a C3 com o resultado certo |

### E. Painel
| # | Teste | Esperado |
|---|---|---|
| E1 | Login do painel A | "Ativar painel" → sirene liberada |
| E2 | Filtros Ativos / Resolvidos / Automáticos | listas corretas |
| E3 | Resolver um alerta | passa para "Resolvido" e a sirene para |
| E4 | Relatório e **PDF** | o PDF baixa e abre com os alertas |
| E5 | Limpar | lista vazia (no banco os alertas ficam **arquivados**, não apagados) |
| E6 | "Sair" | volta ao login |

### F. Isolamento (IDOR/BOLA) com duas contas
| # | Teste | Esperado |
|---|---|---|
| F1 | Motorista B faz SOS | o painel A **não** vê; o contato do A **não** recebe |
| F2 | Motorista B: tela Contatos | não vê os contatos do A |
| F3 | Motorista B: histórico | não vê as viagens do A |

### G. PWA, checklist, IA
| # | Teste | Esperado |
|---|---|---|
| G1 | "Adicionar à tela inicial" / "Instalar" | o ícone abre `/motorista` em tela cheia |
| G2 | Checklist: marcar os 12 itens | barra a 100% → "Partir com segurança" leva ao SOS |
| G3 | Shield AI **sem** chave | "Shield AI não está configurado no servidor." |
| G4 | Shield AI com chave | **fora do escopo desta homologação** (IA desligada); TESTE REAL DA IA continua pendente |

### H. Verificações técnicas (num computador)
| # | Teste | Esperado |
|---|---|---|
| H1 | `/saude` | `{"ok": true}` |
| H2 | DevTools → cookie | `__Host-ds360_sessao`, `Secure`, `HttpOnly`, `SameSite=Lax` |
| H3 | Headers | `Content-Security-Policy` sem `unsafe-inline`, `Strict-Transport-Security`, `X-Frame-Options: DENY` |
| H4 | Console do navegador nas 8 telas | nenhum erro e nenhuma violação de CSP |
| H5 | Logs do web e do worker no Render | **nenhuma** senha, token, URL do banco, telefone completo ou nome digitado; nenhum `[CSP] Violação` |
| H6 | `/api/token` | 404 |
| H7 | Contadores no log do worker | nenhum erro `[AGENDADOR] Erro` |

**Critério de APTO para produção:** todos os itens de A a F e H com ✅, mais G1 e G2. O G4 é necessário só se a IA for ligada em produção.

---

## Desfazer a homologação (sem afetar a produção)
- **Pausar:** Suspend nos dois serviços de staging (para a cobrança deles).
- **Encerrar** (quando você decidir): excluir os recursos `-staging` pelo Dashboard. É um ato manual seu; o banco gratuito expira sozinho em 30 dias.
- A produção, o DNS e a branch principal **não** são tocados em nenhum passo deste documento.
