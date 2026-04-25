/**
 * home.js — Dashboard home page scripts
 * Count-up animation for stat cards
 */
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-count]').forEach(function (el) {
    const target = parseInt(el.dataset.count, 10);
    if (!target) return;

    let current = 0;
    const duration = 900; // ms
    const step = target / (duration / 16);

    const timer = setInterval(function () {
      current = Math.min(current + step, target);
      el.textContent = Math.floor(current);
      if (current >= target) {
        el.textContent = target;
        clearInterval(timer);
      }
    }, 16);
  });
});
