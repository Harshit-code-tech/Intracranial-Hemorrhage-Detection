/* ═══════════════════════════════════════════════════════════════
   ICH Pipeline — Landing Page JS
   ═══════════════════════════════════════════════════════════════ */

(function () {
  /* ── Scroll-triggered navbar shadow ── */
  var topbar = document.querySelector('.topbar');
  if (topbar) {
    window.addEventListener('scroll', function () {
      if (window.scrollY > 20) {
        topbar.style.boxShadow = '0 4px 32px rgba(0,0,0,0.4)';
      } else {
        topbar.style.boxShadow = 'none';
      }
    }, { passive: true });
  }

  /* ── Intersection Observer: fade-in feature cards on scroll ── */
  if ('IntersectionObserver' in window) {
    var cards = document.querySelectorAll('.feat-card, .step, .stat-item');
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.style.opacity = '1';
          entry.target.style.transform = 'translateY(0)';
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });

    cards.forEach(function (card) {
      card.style.opacity = '0';
      card.style.transform = 'translateY(24px)';
      card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
      observer.observe(card);
    });
  }
})();
