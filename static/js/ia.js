// O prompt de sistema e a chave da IA ficam SOMENTE no servidor (/api/ia).
const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const MAX_HISTORICO = 19;   // servidor aceita até 20 mensagens, começando e terminando com "user"

let historico = [];
let aguardando = false;

async function enviar() {
  const input = document.getElementById('msg-input');
  const texto = input.value.trim();
  if (!texto || aguardando) return;

  const welcome = document.getElementById('welcome-screen');
  if (welcome) welcome.remove();

  adicionarMsg(texto, 'user');
  input.value = '';
  autoResize(input);
  historico.push({ role: 'user', content: texto });
  if (historico.length > MAX_HISTORICO) historico = historico.slice(-MAX_HISTORICO);

  aguardando = true;
  document.getElementById('btn-send').disabled = true;

  const typing = adicionarTyping();

  try {
    const res = await fetch('/api/ia', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      body: JSON.stringify({ mensagens: historico })
    });
    if (res.status === 401) { location.href = '/motorista/login?next=/ia'; return; }
    const data = await res.json();
    typing.remove();
    if (data.ok) {
      adicionarMsg(data.resposta, 'ai');
      historico.push({ role: 'assistant', content: data.resposta });
    } else {
      historico.pop();   // mantém a alternância user/assistant exigida pelo servidor
      adicionarMsg('⚠️ ' + (data.erro || 'Não consegui responder agora.'), 'ai');
    }
  } catch(e) {
    typing.remove();
    historico.pop();
    adicionarMsg('❌ Sem conexão com o assistente. Verifique sua internet.', 'ai');
  } finally {
    aguardando = false;
    document.getElementById('btn-send').disabled = false;
    input.focus();
  }
}

function enviarSugestao(btn) {
  document.getElementById('msg-input').value = btn.textContent;
  enviar();
}

function adicionarMsg(texto, tipo) {
  const area = document.getElementById('chat-area');
  const div = document.createElement('div');
  div.className = `msg ${tipo}`;
  div.innerHTML = `
    <div class="msg-avatar">${tipo === 'ai' ? '🤖' : '👤'}</div>
    <div class="msg-bubble">${formatarTexto(texto)}</div>
  `;
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
  return div;
}

function adicionarTyping() {
  const area = document.getElementById('chat-area');
  const div = document.createElement('div');
  div.className = 'msg ai typing';
  div.innerHTML = `
    <div class="msg-avatar">🤖</div>
    <div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>
  `;
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
  return div;
}

function formatarTexto(texto) {
  return texto
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/\n/g,'<br>');
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    enviar();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Eventos (sem handlers inline — compatível com CSP) ──
(function ligarEventos() {
  const input = document.getElementById('msg-input');
  input.addEventListener('keydown', handleKey);
  input.addEventListener('input', () => autoResize(input));
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-acao]');
    if (!el) return;
    if (el.dataset.acao === 'enviar') enviar();
    if (el.dataset.acao === 'enviarSugestao') enviarSugestao(el);
  });
})();
