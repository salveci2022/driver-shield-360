// ══ SEGURANÇA ══
const CSRF = document.querySelector('meta[name="csrf-token"]').content;
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function coord(v) { const n = parseFloat(v); return Number.isFinite(n) ? n : null; }
async function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({'X-CSRF-Token': CSRF}, opts.headers || {});
  const res = await fetch(url, opts);
  if (res.status === 401) { location.href = '/painel/login?next=/painel'; throw new Error('sessao'); }
  return res;
}

let todosAlertas = [];
let filtroAtual = 'todos';
let viagemInterval = null;
let ultimoTotal = -1;
let sireneAtiva = false;
let sireneLoop = null;
let painelAtivado = false;

// ══ ATIVAÇÃO ══
function ativarPainel() {
  painelAtivado = true;
  // Pré-carrega e desbloqueia MP3 com gesto do usuário
  criarAudio();
  // Toca mudo por 0.1s para desbloquear autoplay
  sireneAudio.volume = 0;
  sireneAudio.play().then(()=>{
    setTimeout(()=>{ sireneAudio.pause(); sireneAudio.currentTime=0; sireneAudio.volume=0.9; },100);
  }).catch(()=>{});
  document.getElementById('ativacao').classList.add('oculto');
  carregarAlertas();
  verificarViagem();
  setInterval(carregarAlertas, 6000);
  setInterval(verificarViagem, 15000);
}

// ══ SIRENE MP3 REAL ══
let sireneAudio = null;

function criarAudio() {
  if (sireneAudio) return;
  sireneAudio = new Audio('/static/sirene.mp3');
  sireneAudio.loop = true;
  sireneAudio.volume = 0.9;
}

function iniciarSirene() {
  if (sireneAtiva) return;
  sireneAtiva = true;
  criarAudio();
  sireneAudio.currentTime = 0;
  sireneAudio.play().catch(()=>{});
  const btn = document.getElementById('btn-sirene');
  btn.textContent = '🔇 Parar';
  btn.className = 'hbtn sirene-on';
}

function pararSirene() {
  sireneAtiva = false;
  if (sireneAudio) { sireneAudio.pause(); sireneAudio.currentTime = 0; }
  const btn = document.getElementById('btn-sirene');
  btn.textContent = '🔊 Sirene';
  btn.className = 'hbtn verde';
}

function toggleSirene() {
  if (sireneAtiva) pararSirene();
  else iniciarSirene();
}

// ══ ALERTAS ══
async function carregarAlertas() {
  try {
    const res = await apiFetch('/api/alerts');
    const data = await res.json();
    todosAlertas = data.reverse();

    const ativos = todosAlertas.filter(a => a.status === 'Ativo').length;

    // Novo alerta chegou → toca sirene
    if (ultimoTotal >= 0 && todosAlertas.length > ultimoTotal && ativos > 0) {
      iniciarSirene();
      showToast('🚨 Novo alerta recebido!', 'erro');
      if (navigator.vibrate) navigator.vibrate([300,100,300,100,600]);
    }
    ultimoTotal = todosAlertas.length;

    // UI muda conforme estado
    const header = document.getElementById('main-header');
    const banner = document.getElementById('alerta-banner');
    const badge  = document.getElementById('live-badge');
    const dot    = document.getElementById('ldot');
    const liveTxt = document.getElementById('live-txt');

    if (ativos > 0) {
      header.classList.add('alerta-ativo');
      banner.classList.add('visivel');
      badge.classList.add('alerta');
      dot.classList.add('alerta');
      liveTxt.textContent = `${ativos} ALERTA${ativos>1?'S':''} ATIVO${ativos>1?'S':''}`;
      document.getElementById('banner-txt').textContent =
        `${ativos} alerta${ativos>1?'s':''} ativo${ativos>1?'s':''} — motorista precisa de ajuda!`;
      document.title = `🚨 ${ativos} ALERTA — Driver Shield 360`;
    } else {
      header.classList.remove('alerta-ativo');
      banner.classList.remove('visivel');
      badge.classList.remove('alerta');
      dot.classList.remove('alerta');
      liveTxt.textContent = 'Ao vivo';
      document.title = 'Painel — Driver Shield 360';
      if (sireneAtiva) pararSirene();
    }

    atualizarStats();
    renderAlertas();
  } catch(e) {}
}

function atualizarStats() {
  const ativos = todosAlertas.filter(a => a.status==='Ativo').length;
  const resolvidos = todosAlertas.filter(a => a.status==='Resolvido').length;
  document.getElementById('cnt-ativos').textContent = ativos;
  document.getElementById('cnt-total').textContent = todosAlertas.length;
  document.getElementById('cnt-resolvidos').textContent = resolvidos;
}

function renderAlertas() {
  const list = document.getElementById('alertas-list');
  let f = todosAlertas;
  if (filtroAtual==='ativo')     f = todosAlertas.filter(a=>a.status==='Ativo');
  if (filtroAtual==='resolvido') f = todosAlertas.filter(a=>a.status==='Resolvido');
  if (filtroAtual==='dms')       f = todosAlertas.filter(a=>a.tipo==='dead_mans_switch');

  if (!f.length) {
    list.innerHTML = `<div class="empty"><div class="empty-icon">✅</div><p>Nenhuma ocorrência</p></div>`;
    return;
  }
  list.innerHTML = f.map(a => {
    const st  = a.status === 'Resolvido' ? 'Resolvido' : 'Ativo';
    const cl  = a.tipo==='dead_mans_switch' ? 'dms' : st.toLowerCase();
    const lat = coord(a.lat), lng = coord(a.lng);
    const temGps = a.lat && a.lng && lat !== null && lng !== null;
    const map = temGps ? `https://www.google.com/maps?q=${lat},${lng}&z=16` : null;
    const loc = temGps ? `${lat.toFixed(4)}, ${lng.toFixed(4)}` : 'Sem GPS';
    return `
    <div class="alerta-card ${cl}">
      <div class="alerta-top">
        <div class="alerta-tipo">${esc(a.ocorrencia)}</div>
        <span class="badge ${st.toLowerCase()}">${st}</span>
      </div>
      <div class="alerta-meta">
        <div class="alerta-field">👤 <strong>${esc(a.motorista)}</strong></div>
        <div class="alerta-field">⏰ ${esc(a.ts)}</div>
        <div class="alerta-field">📍 ${esc(loc)}</div>
      </div>
      <div class="alerta-actions">
        ${st==='Ativo'?`<button class="btn-resolver" data-acao="resolver" data-id="${Number(a.id)}">✅ Resolver</button>`:''}
        ${map?`<a class="btn-mapa" href="${map}" target="_blank" rel="noopener noreferrer">🗺️ Ver mapa</a>`:''}
      </div>
    </div>`;
  }).join('');
}

function filtrar(f, btn) {
  filtroAtual = f;
  document.querySelectorAll('.filtro-btn').forEach(b=>b.classList.remove('ativo'));
  btn.classList.add('ativo');
  renderAlertas();
}

async function resolver(id) {
  await apiFetch('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id})});  // id do alerta (o servidor confere a permissão)
  await carregarAlertas();
  showToast('✅ Ocorrência resolvida','ok');
}

async function limparTodos() {
  if (!confirm('Apagar todo o histórico de alertas?')) return;
  await apiFetch('/api/clear_alerts',{method:'POST'});
  ultimoTotal = 0;
  await carregarAlertas();
  showToast('🗑️ Histórico limpo','ok');
}

function reiniciar() {
  pararSirene();
  showToast('🔄 Recarregando...','ok');
  setTimeout(()=>location.reload(),800);
}

// ══ VIAGEM ══
async function verificarViagem() {
  try {
    const res = await apiFetch('/api/viagem/status');
    const d = await res.json();
    const banner = document.getElementById('viagem-banner');
    if (d.ativa) {
      banner.classList.remove('oculto');
      document.getElementById('viagem-motorista').textContent = d.motorista||'—';
      iniciarCountdown(d.restante);
    } else {
      banner.classList.add('oculto');
      if (viagemInterval) { clearInterval(viagemInterval); viagemInterval=null; }
    }
  } catch(e) {}
}

function iniciarCountdown(s) {
  if (viagemInterval) { clearInterval(viagemInterval); viagemInterval=null; }
  atualizarTimer(s);
  viagemInterval = setInterval(()=>{s--;atualizarTimer(s);if(s<=0){clearInterval(viagemInterval);verificarViagem();}},1000);
}
function atualizarTimer(s) {
  const el = document.getElementById('viagem-timer');
  el.textContent = `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  el.className = 'viagem-timer'+(s<=120?' urgente':'');
}

// ══ TOAST ══
function showToast(msg, tipo) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = `toast ${tipo} show`;
  setTimeout(()=>t.className='toast',3000);
}

// ── Eventos (sem handlers inline — compatível com CSP) ──
document.addEventListener('click', e => {
  const el = e.target.closest('[data-acao]');
  if (!el) return;
  const ACOES = {
    ativarPainel: () => ativarPainel(),
    toggleSirene: () => toggleSirene(),
    reiniciar:    () => reiniciar(),
    limparTodos:  () => limparTodos(),
    filtrar:      el => filtrar(el.dataset.filtro, el),
    resolver:     el => resolver(Number(el.dataset.id)),
  };
  if (ACOES[el.dataset.acao]) ACOES[el.dataset.acao](el);
});
