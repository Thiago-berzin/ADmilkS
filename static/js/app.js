// ADmilkS — app.js (utilitários globais)
// A lógica por tela fica inline nos templates para clareza.

document.addEventListener('DOMContentLoaded', () => {
  // Anima cards ao carregar
  document.querySelectorAll('.beneficiario-card').forEach((el, i) => {
    el.style.animationDelay = `${i * 40}ms`;
  });
});
