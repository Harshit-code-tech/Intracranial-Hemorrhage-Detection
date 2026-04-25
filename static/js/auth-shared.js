/**
 * auth-shared.js — Shared utility for all auth pages
 * Provides: makePasswordToggle(), passwordStrengthMeter()
 */

/**
 * Wire up a show/hide password toggle button.
 * @param {string} btnId     - ID of the toggle button
 * @param {string} inputId   - ID of the password input
 * @param {string} iconId    - ID of the SVG element inside the button
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
      /* eye-off */
      ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>' +
        '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>' +
        '<line x1="1" y1="1" x2="23" y2="23"/>'
      /* eye */
      : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
        '<circle cx="12" cy="12" r="3"/>';
  });
}

/**
 * Wire up a live password-strength indicator.
 * @param {string} inputId   - ID of the password input
 * @param {string} barId     - ID of the strength fill div
 * @param {string} textId    - ID of the strength label span
 */
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
