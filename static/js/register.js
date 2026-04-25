/**
 * register.js — Registration page interactions
 * Depends on: auth-shared.js
 */
document.addEventListener('DOMContentLoaded', function () {
  // Show/hide toggles for both password fields
  makePasswordToggle('togglePw',  'password',         'eyeIcon');
  makePasswordToggle('togglePw2', 'confirm_password', 'eyeIcon2');

  // Live password strength meter
  passwordStrengthMeter('password', 'pwBar', 'pwText');
});
