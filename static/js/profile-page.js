/**
 * profile-page.js — Profile page interactions
 * Handles: pw-fields toggle, eye toggles, strength meter
 * NOTE: AJAX password-change is still handled by profile.js (legacy)
 * Depends on: auth-shared.js
 */
document.addEventListener('DOMContentLoaded', function () {

  /* ── Change-password field toggle ── */
  const pwFields    = document.getElementById('pwFields');
  const pwToggleBtn = document.getElementById('pwToggleBtn');
  const pwCancelBtn = document.getElementById('pwCancelBtn');

  if (pwToggleBtn && pwFields) {
    pwToggleBtn.addEventListener('click', function () {
      pwFields.classList.add('active');
      pwToggleBtn.style.display = 'none';
    });
  }

  if (pwCancelBtn && pwFields) {
    pwCancelBtn.addEventListener('click', function () {
      pwFields.classList.remove('active');
      if (pwToggleBtn) pwToggleBtn.style.display = '';
      const form = document.getElementById('changePasswordForm');
      if (form) form.reset();
      const msg = document.getElementById('pwMessage');
      if (msg) { msg.className = ''; msg.textContent = ''; }
      // Reset strength bar
      const bar  = document.getElementById('profilePwBar');
      const text = document.getElementById('profilePwText');
      if (bar)  { bar.className  = 'pw-strength-fill'; }
      if (text) { text.className = 'pw-strength-text'; text.textContent = ''; }
    });
  }

  /* ── Eye toggles ── */
  makePasswordToggle('toggleCur', 'currentPassword', 'eyeCur');
  makePasswordToggle('toggleNew', 'newPassword',     'eyeNew');

  /* ── Strength meter on new password ── */
  passwordStrengthMeter('newPassword', 'profilePwBar', 'profilePwText');
});
