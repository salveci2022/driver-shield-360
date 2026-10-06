# 🛡️ Driver Shield 360 — v3.0
**Sistema de Segurança Premium para Motoristas de Aplicativo**

---

## 📁 Estrutura de arquivos

```
driver-shield-360/
├── app.py                    ← Backend Flask principal
├── requirements.txt          ← Dependências Python
├── gerar_icones.py           ← Gera ícones PWA (rode 1x localmente)
├── static/
│   ├── icon-192.png          ← Ícone PWA (já gerado)
│   └── icon-512.png          ← Ícone PWA (já gerado)
├── templates/
│   ├── index.html            ← Landing page de vendas
│   ├── motorista.html        ← Tela principal (SOS + GPS + Timer)
│   ├── cadastro.html         ← Cadastro de contatos
│   ├── checklist.html        ← Checklist de segurança
│   ├── historico.html        ← Histórico de viagens
│   ├── painel.html           ← Painel da pessoa de confiança
│   ├── painel_login.html     ← Login do painel
│   ├── relatorio.html        ← Relatório de ocorrências
│   ├── ia_seguranca.html     ← Shield AI (chat com IA)
│   └── pessoa_sair.html      ← Logout do painel
└── data/                     ← Gerado automaticamente
    ├── contacts.json
    ├── alerts.json
    ├── viagem.json
    └── historico_viagens.json
```

---

## ⚙️ Variáveis de ambiente no Render

Configure em **Dashboard → Environment → Environment Variables**:

| Variável           | Valor                        | Obrigatório |
|--------------------|------------------------------|-------------|
| `ZAPI_INSTANCE`    | ID da instância Z-API        | Sim         |
| `ZAPI_TOKEN`       | Token da instância Z-API     | Sim         |
| `ZAPI_CLIENT_TOKEN`| Client Token Z-API           | Sim         |
| `SECRET_KEY`       | Chave secreta Flask (sessões)| **Sim — a aplicação não inicia sem ela** |
| `PAINEL_SENHA`     | Senha do painel de confiança | **Sim — a aplicação não inicia sem ela** |
| `MOTORISTA_SENHA`  | Código de acesso do motorista| **Sim — a aplicação não inicia sem ela** |
| `API_SECRET`       | Token de integrações servidor-a-servidor (nunca vai ao navegador) | **Sim — a aplicação não inicia sem ela** |
| `ANTHROPIC_API_KEY`| Chave da API Anthropic (fica só no servidor) | Opcional: sem ela o Shield AI responde "não configurado" |
| `IA_MODELO`        | Modelo do Shield AI          | Opcional (padrão `claude-opus-5-5`) |
| `DS360_PROXY_HOPS` | Proxies confiáveis na frente do app | Opcional (padrão `1`, que é o caso do Render; use `0` sem proxy) |
| `STORAGE_BACKEND`  | `json` (padrão) ou `postgres` | Opcional: ver `ARQUITETURA.md` |
| `DATABASE_URL`     | URL do PostgreSQL            | Obrigatória com `STORAGE_BACKEND=postgres` |
| `AGENDADOR_EMBUTIDO` | `1` (padrão) ou `0` com o worker `python agendador.py` | Opcional |

> **FASE 3:** no modo `postgres`, `MOTORISTA_SENHA` e `PAINEL_SENHA` deixam de ser obrigatórias (cada usuário tem a sua senha). O passo a passo completo de banco, migração, worker e rollback está em **`ARQUITETURA.md`**.

### Gerando valores seguros
Gere cada valor separadamente (nunca reutilize o mesmo valor em duas variáveis):
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Os antigos valores padrão (`shield360`, `ds360-api-2026`, `driver-shield-360-premium-2026`)
e os exemplos que estavam neste README são **recusados** na inicialização.

Para desenvolvimento local apenas: `DS360_DEV=1` gera valores aleatórios para os secrets
ausentes e desativa o cookie `Secure` (HTTP local). **Nunca use `DS360_DEV` no Render.**

---

## 🚀 Deploy no Render

1. **Suba os arquivos** para o GitHub (repositório privado)
2. **Crie um Web Service** no Render apontando para o repositório
3. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Python Version:** 3.11+
4. **Adicione as variáveis** de ambiente listadas acima
5. **Deploy** — aguarde 2–3 minutos

---

## 📲 Links do sistema

Após o deploy, os links são:

```
🔑  Login motorista:     https://driver-shield-360.onrender.com/motorista/login
🛡️  Motorista (SOS):     https://driver-shield-360.onrender.com/motorista
📋  Cadastro contatos:   https://driver-shield-360.onrender.com/cadastro
✅  Checklist:           https://driver-shield-360.onrender.com/checklist
📊  Histórico viagens:   https://driver-shield-360.onrender.com/historico
🔐  Login painel:        https://driver-shield-360.onrender.com/painel/login
📈  Painel confiança:    https://driver-shield-360.onrender.com/painel
📄  Relatório:           https://driver-shield-360.onrender.com/relatorio
📥  Download PDF:        https://driver-shield-360.onrender.com/relatorio.pdf
🤖  Shield AI:           https://driver-shield-360.onrender.com/ia
🏠  Landing page:        https://driver-shield-360.onrender.com/
```

---

## 🔑 Como o sistema de segurança da API funciona

- **Motorista** entra em `/motorista/login` com `MOTORISTA_SENHA`. A sessão dura 30 dias
  e é renovada a cada uso, então quem usa o app não perde o acesso ao SOS. Para sair: botão "Sair desta conta" (o logout revoga a sessão no servidor).
- **Painel** entra em `/painel/login` com `PAINEL_SENHA` (sessão de 12h).
- Depois de 5 senhas erradas em 15 minutos, o IP fica bloqueado por 15 minutos.
- Toda alteração (POST) exige o token CSRF da sessão.
- `/api/token` foi removido: nenhum secret é enviado ao navegador.
- Integrações servidor-a-servidor ainda podem chamar `/api/panic`, `/api/viagem/*` e
  `/api/passageiro_suspeito` com o header:
```
X-Shield-Token: SEU_API_SECRET
```

Isso impede que terceiros disparem alertas falsos pela API.

---

## ⏰ Dead Man's Switch — como funciona

1. Motorista ativa Modo Viagem e define o tempo
2. `expira_em` (timestamp Unix) é **salvo em disco** (`data/viagem.json`)
3. Um `threading.Timer` é iniciado em memória
4. **Se o Render hibernar:** ao acordar, `verificar_dms_ao_iniciar()` lê o arquivo e:
   - Se já expirou → dispara alerta imediatamente
   - Se ainda não → reinicia o timer com o tempo restante
5. Motorista confirma chegada → timer cancelado, histórico atualizado

---

## 📱 Instalação como PWA (app no celular)

1. Motorista acessa `https://driver-shield-360.onrender.com/motorista` pelo Chrome
2. Banner de instalação aparece automaticamente
3. Toca em "Instalar" → app fica na tela inicial como nativo
4. Funciona offline para as telas principais

---

## 🆘 SOS pelo botão de volume

O sistema detecta 3 pressões consecutivas nos botões de volume em até 2 segundos e dispara o SOS automaticamente — sem precisar desbloquear a tela.

> **Compatibilidade:** Android Chrome. iOS tem restrições de hardware que impedem esse recurso.

---

## 📞 Suporte

Desenvolvido por **SPYNET Security** — Brasília-DF  
WhatsApp: https://wa.me/5561999999999

---

*Driver Shield 360 v3.0 — © 2026 SPYNET Security*

---

## 🔒 Segurança — FASE 2

- **CSP estrita:** nenhum script inline (todo o JS fica em `static/js/`). Violações são registradas no log como `[CSP]`.
- **Sessões revogáveis:** ficam guardadas em `data/seguranca.db`. Trocar `PAINEL_SENHA` ou `MOTORISTA_SENHA` derruba as sessões abertas daquele papel.
- **Limite de tentativas compartilhado** entre os workers do gunicorn (SQLite).
- **Gravação dos JSONs com trava entre processos**, sem perder atualizações. Um JSON corrompido é preservado em `*.corrompido-<data>`, nunca sobrescrito.
- **Shield AI via backend** (`/api/ia`): exige login do motorista e tem limite de 20 perguntas a cada 10 minutos e 300 por dia no total.
- **LGPD:** ver `LGPD.md` e `python lgpd.py --help`.
- ⚠️ **Disco no Render:** sem um *Persistent Disk*, `data/` (JSONs, sessões, limite de tentativas) é apagado a cada deploy ou reinício.
