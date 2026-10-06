// Confirmação antes de remover contato. O nome vem de data-nome (texto, nunca código).
document.addEventListener('click', e => {
  const btn = e.target.closest('.btn-delete');
  if (btn && !confirm('Remover ' + btn.dataset.nome + '?')) e.preventDefault();
});
