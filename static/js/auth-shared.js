/**
 * auth-shared.js — Shared utility for all auth pages
 * Provides: makePasswordToggle(), passwordStrengthMeter()
 */

function makePasswordToggle(btnId, inputId, iconId) {
  const btn   = document.getElementById(btnId);
  const input = document.getElementById(inputId);
  const icon  = document.getElementById(iconId);
  if (!btn || !input || !icon) return;

  btn.addEventListener('click', function () {
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    icon.innerHTML = isHidden
      ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>' +
        '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>' +
        '<line x1="1" y1="1" x2="23" y2="23"/>'
      : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
        '<circle cx="12" cy="12" r="3"/>';
  });
}

function passwordStrengthMeter(inputId, barId, textId) {
  const input = document.getElementById(inputId);
  const bar   = document.getElementById(barId);
  const text  = document.getElementById(textId);
  if (!input || !bar || !text) return;

  input.addEventListener('input', function () {
    const v = this.value;
    let score = 0;
    if (v.length >= 8)            score++;
    if (/[A-Z]/.test(v))          score++;
    if (/[a-z]/.test(v))          score++;
    if (/[0-9]/.test(v))          score++;
    if (/[^A-Za-z0-9]/.test(v))   score++;

    const classes = ['', 'weak', 'fair', 'good', 'good', 'strong'];
    const labels  = ['', 'Weak', 'Fair', 'Good', 'Good', 'Strong'];
    const cls = classes[score] || '';

    bar.className  = 'pw-strength-fill ' + cls;
    text.className = 'pw-strength-text ' + cls;
    text.textContent = v.length ? labels[score] : '';
  });
}

// Check for cross-origin iframe context (Hugging Face Spaces)
document.addEventListener('DOMContentLoaded', function() {
  let isFramed = false;
  try {
    isFramed = (window.self !== window.top);
  } catch (e) {
    isFramed = true;
  }
  
  if (isFramed) {
    const banner = document.createElement('div');
    banner.style.cssText = `
      position: fixed; top: 0; left: 0; right: 0;
      background: #ef4444; color: white;
      text-align: center; padding: 14px;
      font-weight: 600; font-size: 15px;
      z-index: 9999; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    `;
    banner.innerHTML = `
      <div style="max-width: 800px; margin: 0 auto; display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 10px;">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink: 0;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>Browsers block login cookies inside iframes.</span>
        <a href="${window.location.href}" target="_blank" style="background: white; color: #ef4444; padding: 4px 12px; border-radius: 4px; text-decoration: none; font-weight: bold; font-size: 13px; margin-left: 8px; white-space: nowrap;">
          Open App in New Tab
        </a>
      </div>
    `;
    document.body.prepend(banner);
    
    // Adjust layout to prevent overlap
    const authPage = document.querySelector('.auth-page');
    if (authPage) authPage.style.marginTop = '60px';
    const mainHeader = document.querySelector('header');
    if (mainHeader) mainHeader.style.marginTop = '50px';
  }
});
