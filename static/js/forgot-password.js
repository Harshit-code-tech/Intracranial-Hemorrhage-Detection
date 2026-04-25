/**
 * forgot-password.js — Forgot password page interactions
 */
document.addEventListener('DOMContentLoaded', function () {
  // If redirected back with ?sent=1, show the success state
  const params = new URLSearchParams(window.location.search);
  if (params.get('sent') === '1') {
    const form   = document.getElementById('fpForm');
    const footer = document.querySelector('.auth-footer');
    const state  = document.getElementById('successState');
    if (form)   form.style.display   = 'none';
    if (footer) footer.style.display = 'none';
    if (state)  state.style.display  = 'block';
  }
});
