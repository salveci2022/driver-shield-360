# LGPD — Inventário de dados pessoais (Driver Shield 360)

> Documento técnico de apoio. **As bases legais são sugestões e precisam ser validadas pelo jurídico/DPO.**
> A fonte de verdade em código é `lgpd.py` (`INVENTARIO` e `OUTROS_DADOS`).

## Dados armazenados

| Categoria | Onde | Campos pessoais | Titular | Finalidade | Compartilhado com | Retenção proposta |
|---|---|---|---|---|---|---|
| Contatos de confiança | `data/contacts.json` | nome, telefone, relação | Terceiros indicados pelo motorista | Enviar alertas de emergência por WhatsApp | Z-API (telefone + texto do alerta) | Enquanto o contato estiver cadastrado |
| Alertas / ocorrências | `data/alerts.json` | nome do motorista, **localização GPS**, descrição da ocorrência (pode descrever o passageiro) | Motorista; passageiro descrito | Acionar e acompanhar emergências; relatório PDF | Z-API (nome, ocorrência, link do mapa) | 365 dias (`RETENCAO_ALERTAS_DIAS`) |
| Histórico de viagens | `data/historico_viagens.json` | nome, **GPS de início**, horários | Motorista | Histórico do Modo Viagem | — | 180 dias (`RETENCAO_HISTORICO_DIAS`) |
| Viagem ativa | `data/viagem.json` | nome, GPS de início, expiração | Motorista | Disparo automático (Dead Man's Switch) | — | Sobrescrito ao fim de cada viagem |
| Segurança | `data/seguranca.db` | IP **somente como HMAC-SHA256**, hash da sessão | Usuários | Bloqueio de força bruta, sessões e logout | — | Automática: 15 min a 24 h (tentativas), até expirar (sessões) |
| Navegador | `localStorage` | nome do motorista, checklist | Motorista | Conveniência | — | Até o usuário limpar os dados |
| Shield AI | não armazenado | texto digitado | Motorista | Responder perguntas de segurança | **Anthropic (transferência internacional)** | Não gravado pelo sistema |
| Logs da hospedagem | Render/gunicorn | IP e caminho das requisições | Usuários | Operação e segurança | Provedor de hospedagem | Conforme o provedor |

O sistema não grava em log senhas, tokens, telefones completos nem o conteúdo das conversas com a IA.

## Direitos do titular — ferramentas prontas (nada é automático)

| Direito | Como atender |
|---|---|
| Acesso e portabilidade | `GET /api/lgpd/exportar` (motorista logado), ou `python lgpd.py exportar --saida arquivo.json` |
| Relatório de retenção | `GET /api/lgpd/retencao`, ou `python lgpd.py retencao` (somente leitura) |
| Eliminação | `python lgpd.py excluir --categoria <alertas\|historico\|contatos> [--antes-de-dias N]`: **simulação por padrão**; `--executar` exclui e salva um backup completo antes, em `data/lgpd_backup_<data>/` |

A exclusão **não** tem rota web, de propósito. Uma exclusão automática por prazo de retenção só deve ser ligada depois que os prazos forem aprovados pelo jurídico.

## Pendências (fora do código)

- Política de privacidade e termo de ciência para o motorista. Incluir os contatos de confiança, que são terceiros, e a transferência internacional para a Anthropic.
- Confirmar as bases legais e os prazos de retenção com o jurídico/DPO.
- Contrato ou termos de tratamento com a Z-API e com a hospedagem.
- Se `data/` já esteve em algum repositório Git público ou compartilhado, tratar como possível incidente: avaliar a remoção do histórico e o comunicado à ANPD.
