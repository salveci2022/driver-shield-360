# Driver Shield 360 — Versão Corrigida (Templates + Backend)

## Como rodar no Windows (local)
1) Instale Python 3.10+
2) No terminal, dentro desta pasta:
   pip install flask
3) Rode:
   python app.py
4) Abra no navegador:
   http://127.0.0.1:5000

## Rotas
/               (home)
/motorista       (motorista)
/cadastro        (cadastro até 3)
/painel          (pessoa de confiança, aberto)
/relatorio       (relatório)

## Observações importantes
- A SIRENE toca somente no painel da Pessoa de Confiança.
- No celular, entre no /painel e toque em "Sirene" 1 vez para liberar o áudio.
- O GPS depende de permissão do navegador e das configurações do celular.
