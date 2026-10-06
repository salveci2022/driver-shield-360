const TOTAL = 12;
let marcados = new Set();

function toggle(el) {
  const id = el.dataset.id;
  if (marcados.has(id)) {
    marcados.delete(id);
    el.classList.remove('marcado');
    el.querySelector('.checkbox').textContent = '';
  } else {
    marcados.add(id);
    el.classList.add('marcado');
    el.querySelector('.checkbox').textContent = '✓';
  }
  salvar();
  atualizar();
}

function atualizar() {
  const n = marcados.size;
  const pct = Math.round((n / TOTAL) * 100);

  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-label').textContent = `${n} de ${TOTAL} itens`;
  document.getElementById('progress-pct').textContent = pct + '%';

  const status = document.getElementById('status-card');
  const goBtn = document.getElementById('go-btn');

  if (n === TOTAL) {
    status.className = 'status-card ok';
    status.textContent = '🟢 Tudo certo! Você está pronta para partir com segurança.';
    goBtn.className = 'go-btn pronto';
    goBtn.textContent = '🚗 Partir com segurança!';
  } else if (n >= 8) {
    status.className = 'status-card parcial';
    status.textContent = `⚠️ ${TOTAL - n} itens pendentes — complete para máxima segurança.`;
    goBtn.className = 'go-btn pendente';
    goBtn.textContent = `Completar checklist (${n}/${TOTAL})`;
  } else {
    status.className = 'status-card';
    goBtn.className = 'go-btn pendente';
    goBtn.textContent = `Completar checklist (${n}/${TOTAL})`;
  }
}

function partirComSeguranca() {
  if (marcados.size === TOTAL) {
    window.location.href = '/motorista';
  }
}

function resetar() {
  if (!confirm('Resetar todos os itens?')) return;
  marcados.clear();
  document.querySelectorAll('.item-check').forEach(el => {
    el.classList.remove('marcado');
    el.querySelector('.checkbox').textContent = '';
  });
  salvar();
  atualizar();
}

function salvar() {
  localStorage.setItem('checklist', JSON.stringify([...marcados]));
}

function carregar() {
  try {
    const saved = JSON.parse(localStorage.getItem('checklist') || '[]');
    saved.forEach(id => {
      const el = document.querySelector(`[data-id="${id}"]`);
      if (el) { marcados.add(id); el.classList.add('marcado'); el.querySelector('.checkbox').textContent = '✓'; }
    });
  } catch(e) {}
  atualizar();
}

carregar();

// ── Eventos (sem handlers inline — compatível com CSP) ──
document.addEventListener('click', e => {
  const el = e.target.closest('[data-acao]');
  if (!el) return;
  const ACOES = { toggle: el => toggle(el), resetar: () => resetar(), partirComSeguranca: () => partirComSeguranca() };
  if (ACOES[el.dataset.acao]) ACOES[el.dataset.acao](el);
});
