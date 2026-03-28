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
| `PAINEL_SENHA`     | Senha do painel de confiança | Sim         |
| `API_SECRET`       | Token interno da API         | Sim         |
| `ANTHROPIC_API_KEY`| Chave da API Anthropic       | Shield AI   |
| `SECRET_KEY`       | Chave secreta Flask          | Recomendado |

### Valores recomendados para produção:
```
PAINEL_SENHA=MinhasSenha@2026
API_SECRET=shield360-prod-CHAVE_ALEATORIA_AQUI
SECRET_KEY=flask-secret-OUTRA_CHAVE_ALEATORIA
```

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

O frontend busca o token em `/api/token` ao carregar.
Todas as requisições sensíveis enviam o header:
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
