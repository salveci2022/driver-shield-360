// ── Sessão + CSRF ────────────────────────
// A autenticação é a sessão do motorista (cookie HttpOnly). Nenhum secret fica no navegador.
let CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').content;

async function renovarCsrf() {
  try {
    const r = await fetch('/api/csrf', { cache: 'no-store' });
    if (r.status === 401) { irParaLogin(); return false; }
    const d = await r.json();
    if (d.csrf) { CSRF_TOKEN = d.csrf; return true; }
  } catch(e) {}
  return false;
}

function irParaLogin() {
  showToast('Sessão expirada — entre novamente', 'erro');
  setTimeout(() => location.href = '/motorista/login?next=/motorista', 1200);
}

function apiHeaders() {
  return { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN };
}

// POST autenticado; se o token CSRF estiver desatualizado (ex.: página do cache), renova e tenta de novo
async function apiPost(url, body) {
  const opts = () => ({ method: 'POST', headers: apiHeaders(), body: body ? JSON.stringify(body) : undefined });
  let res = await fetch(url, opts());
  if (res.status === 403 && await renovarCsrf()) res = await fetch(url, opts());
  if (res.status === 401) { irParaLogin(); throw new Error('sessao'); }
  return res;
}

// ── GPS ──────────────────────────────────
let gpsLat = null, gpsLng = null;
const gpsBanner = document.getElementById('gps-banner');

function iniciarGPS() {
  if (!navigator.geolocation) {
    gpsBanner.className = 'gps-banner erro';
    gpsBanner.textContent = '❌ GPS não disponível';
    return;
  }
  navigator.geolocation.watchPosition(
    pos => {
      gpsLat = pos.coords.latitude.toFixed(6);
      gpsLng = pos.coords.longitude.toFixed(6);
      gpsBanner.className = 'gps-banner ok';
      gpsBanner.textContent = `✅ GPS ativo — ${gpsLat}, ${gpsLng}`;
    },
    () => {
      gpsBanner.className = 'gps-banner erro';
      gpsBanner.textContent = '⚠️ GPS sem permissão — alerta sem localização';
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 }
  );
}

// ── Tipo de ocorrência ───────────────────
let tipoSelecionado = 'Emergência Geral';
document.querySelectorAll('.tipo-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tipo-btn').forEach(b => b.classList.remove('ativo'));
    btn.classList.add('ativo');
    tipoSelecionado = btn.dataset.tipo;
  });
});

// ── SOS 2026 — segurar 1.5s para disparar ────
let pressAnim = null;
let holding = false;
let fired = false;

function startPress(e) {
  if (e) e.preventDefault();
  if (fired) return;
  holding = true;
  const svg = document.getElementById('prog-svg');
  const fill = document.getElementById('prog-fill');
  svg.style.opacity = '1';
  document.getElementById('sos-sub').textContent = 'segurando...';

  const total = 565;
  const duration = 1500;
  const start = performance.now();

  function animate(now) {
    if (!holding) return;
    const pct = Math.min((now - start) / duration, 1);
    fill.style.strokeDashoffset = total * (1 - pct);
    if (pct < 1) {
      pressAnim = requestAnimationFrame(animate);
    } else {
      disparar();
    }
  }
  pressAnim = requestAnimationFrame(animate);
}

function endPress() {
  if (!holding || fired) return;
  holding = false;
  if (pressAnim) cancelAnimationFrame(pressAnim);
  document.getElementById('prog-svg').style.opacity = '0';
  document.getElementById('prog-fill').style.strokeDashoffset = '565';
  document.getElementById('sos-sub').textContent = 'segure para disparar';
}

async function disparar() {
  if (fired) return;
  holding = false;
  fired = true;
  if (pressAnim) cancelAnimationFrame(pressAnim);

  const nome = document.getElementById('nome-input').value.trim() || 'Motorista';
  const outer = document.getElementById('sos-outer');
  const btn   = document.getElementById('sos-btn');

  // Ativa rotação + cor laranja de alerta
  outer.classList.add('girando');
  btn.classList.add('disparado');
  document.getElementById('sos-sub').textContent = 'Enviando...';
  document.getElementById('status-txt').textContent = 'ALERTA ATIVO';
  document.getElementById('prog-svg').style.opacity = '0';
  vibrar([200,100,200,100,400]);

  try {
    const res = await apiPost('/api/panic',
      { motorista:nome, ocorrencia:tipoSelecionado, lat:gpsLat, lng:gpsLng });
    const data = await res.json();
    showToast(data.ok ? '🚨 Alerta enviado com GPS!' : 'Erro: ' + (data.erro||'tente novamente'),
              data.ok ? 'ok' : 'erro');
    document.getElementById('sos-sub').textContent = data.ok ? 'Alerta disparado!' : 'Erro — tente de novo';
  } catch(e) {
    if (e.message !== 'sessao') showToast('Sem conexão', 'erro');
    document.getElementById('sos-sub').textContent = e.message === 'sessao' ? 'Entre novamente' : 'Sem conexão';
  }

  setTimeout(() => {
    fired = false;
    outer.classList.remove('girando');
    btn.classList.remove('disparado');
    document.getElementById('sos-sub').textContent = 'segure para disparar';
    document.getElementById('status-txt').textContent = 'Protegido';
    document.getElementById('prog-fill').style.strokeDashoffset = '565';
  }, 5000);
}

// ── Botão Volume — SOS Disfarçado ────────
let volPresses = 0, volTimer = null;
document.addEventListener('keydown', e => {
  if (e.key === 'AudioVolumeUp' || e.key === 'VolumeUp' ||
      e.key === 'AudioVolumeDown' || e.key === 'VolumeDown') {
    volPresses++;
    clearTimeout(volTimer);
    if (volPresses >= 3) {
      volPresses = 0;
      disparar();
    } else {
      volTimer = setTimeout(() => { volPresses = 0; }, 2000);
    }
  }
});

// ── Dead Man's Switch ─────────────────────
let timerInterval = null, tempoRestante = 0;

async function iniciarViagem() {
  const nome = document.getElementById('nome-input').value.trim() || 'Motorista';
  const minutos = parseInt(document.getElementById('min-select').value);
  const res = await apiPost('/api/viagem/iniciar',
    { motorista: nome, minutos, lat: gpsLat, lng: gpsLng });
  const data = await res.json();
  if (data.ok) {
    tempoRestante = data.segundos;
    mostrarPainelAtivo();
    iniciarTimerLocal();
    showToast(`⏰ Modo viagem: ${minutos} min`, 'ok');
    vibrar([200]);
  } else {
    showToast(data.erro || 'Erro ao iniciar', 'erro');
  }
}

async function confirmarChegada() {
  const res = await apiPost('/api/viagem/confirmar');
  if (!res.ok) { showToast('Erro ao confirmar — tente de novo', 'erro'); return; }
  pararTimer();
  mostrarPainelConfig();
  showToast('✅ Chegada confirmada!', 'ok');
  vibrar([200,100,200]);
}

async function cancelarViagem() {
  const res = await apiPost('/api/viagem/cancelar');
  if (!res.ok) { showToast('Erro ao cancelar — tente de novo', 'erro'); return; }
  pararTimer();
  mostrarPainelConfig();
  showToast('Viagem cancelada.', 'ok');
}

function mostrarPainelAtivo() {
  document.getElementById('viagem-config-panel').style.display = 'none';
  document.getElementById('viagem-ativa-panel').style.display  = 'block';
}
function mostrarPainelConfig() {
  document.getElementById('viagem-config-panel').style.display = 'block';
  document.getElementById('viagem-ativa-panel').style.display  = 'none';
}
function iniciarTimerLocal() {
  pararTimer();
  atualizarTimer();
  timerInterval = setInterval(() => {
    tempoRestante--;
    atualizarTimer();
    if (tempoRestante <= 0) { pararTimer(); mostrarPainelConfig(); }
  }, 1000);
}
function pararTimer() { if (timerInterval) { clearInterval(timerInterval); timerInterval = null; } }
function atualizarTimer() {
  const m = Math.floor(tempoRestante / 60), s = tempoRestante % 60;
  const el = document.getElementById('timer-num');
  el.textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  el.className = 'timer-num' + (tempoRestante <= 120 ? ' urgente' : '');
}

async function verificarViagemAtiva() {
  try {
    const res = await fetch('/api/viagem/status');
    if (res.status === 401) { irParaLogin(); return; }
    const data = await res.json();
    if (data.ativa) {
      tempoRestante = data.restante;
      mostrarPainelAtivo();
      iniciarTimerLocal();
    }
  } catch(e) {}
}

// ── Utils ─────────────────────────────────
function vibrar(pattern) { if (navigator.vibrate) navigator.vibrate(pattern); }

function showToast(msg, tipo) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${tipo} show`;
  setTimeout(() => t.className = 'toast', 3000);
}

// ── PWA Install ──────────────────────────
let deferredPrompt;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault(); deferredPrompt = e;
  document.getElementById('install-banner').style.display = 'flex';
});
document.getElementById('install-btn').addEventListener('click', async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  document.getElementById('install-banner').style.display = 'none';
});

// ── SW ────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

// ── Nome persistido ───────────────────────
const nomeInput = document.getElementById('nome-input');
const nomeGuardado = localStorage.getItem('motorista_nome');
if (nomeGuardado) nomeInput.value = nomeGuardado;
nomeInput.addEventListener('change', () => localStorage.setItem('motorista_nome', nomeInput.value));

// ── Init ──────────────────────────────────
iniciarGPS();
verificarViagemAtiva();
renovarCsrf();

// ── Eventos (sem handlers inline — compatível com CSP) ──
(function ligarEventos() {
  const sos = document.getElementById('sos-btn');
  sos.addEventListener('mousedown', () => startPress());
  sos.addEventListener('mouseup', endPress);
  sos.addEventListener('mouseleave', endPress);
  sos.addEventListener('touchstart', e => startPress(e), { passive: false });
  sos.addEventListener('touchend', endPress);
  const ACOES = { iniciarViagem, confirmarChegada, cancelarViagem };
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-acao]');
    if (el && ACOES[el.dataset.acao]) ACOES[el.dataset.acao](el, e);
  });
})();
