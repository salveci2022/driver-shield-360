Driver Shield 360 • Deploy Render (produção)

✅ Links (Render)
- /                 (tela inicial)
- /motorista        (painel do motorista)
- /cadastro         (cadastro pessoa de confiança)
- /login            (login pessoa de confiança)
- /painel           (painel pessoa de confiança após login)
- /relatorio        (relatório de ocorrências)

Se /cadastro ou /login der 404/TemplateNotFound:
1) Confirme que a pasta /templates está no GitHub (commit + push).
2) No Render, Settings > Build & Deploy:
   - Root Directory: (vazio)  ✅ se app.py está na raiz do repo
   - Build:  pip install -r requirements.txt
   - Start:  gunicorn app:app
3) Em Deploys, clique 'Manual Deploy > Deploy latest commit'.

Dica de segurança (blindado):
- Debug desativado
- Páginas 404/500 amigáveis (sem stacktrace)
- SECRET_KEY via variável de ambiente (Render > Environment)

