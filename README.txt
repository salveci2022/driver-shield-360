# DRIVER-SHIELD-360 PREMIUM
Sistema de Segurança para Motoristas de Aplicativo

## NOVIDADES DA VERSÃO PREMIUM:
- ✅ Alertas automáticos via WhatsApp (Z-API)
- ✅ Login com senha no painel da pessoa de confiança
- ✅ Relatório em PDF para download
- ✅ Até 5 contatos de confiança (era 3)
- ✅ 8 tipos de ocorrência (incluindo acidente de trânsito)
- ✅ Visual futurista premium
- ✅ Vibração no celular ao enviar SOS
- ✅ Status do alerta (Ativo / Resolvido)

## ROTAS:
/                   → Home
/motorista          → Painel do Motorista (botão SOS)
/cadastro           → Cadastro de contatos (até 5)
/painel/login       → Login do painel de confiança
/painel             → Painel da pessoa de confiança (requer login)
/relatorio          → Relatório de ocorrências (requer login)
/relatorio.pdf      → Download do relatório em PDF

## CONFIGURAR NO RENDER.COM (Environment Variables):

| Variável          | Valor                          |
|-------------------|-------------------------------|
| SECRET_KEY        | (senha aleatória segura)       |
| PAINEL_SENHA      | (senha do painel)              |
| ZAPI_INSTANCE     | (ID da instância Z-API)        |
| ZAPI_TOKEN        | (Token da instância Z-API)     |
| ZAPI_CLIENT_TOKEN | (Client-Token da Z-API)        |

## COMO RODAR LOCAL:
1. pip install -r requirements.txt
2. python app.py
3. Acesse: http://127.0.0.1:5000

## OBSERVAÇÕES:
- A sirene toca no painel da pessoa de confiança
- Toque em "Sirene" 1x para liberar o áudio no celular
- O GPS requer permissão do navegador
- Para GPS funcionar, o site precisa estar em HTTPS (Render.com já faz isso)
