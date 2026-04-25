document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('otpForm');
  var combined = document.getElementById('otpCombined');
  var inputs = Array.prototype.slice.call(document.querySelectorAll('.otp-digit'));

  if (!form || !combined || !inputs.length) {
    return;
  }

  function normalize(v) {
    return (v || '').replace(/\D/g, '').slice(0, 1);
  }

  function updateCombined() {
    combined.value = inputs.map(function (i) { return i.value; }).join('');
  }

  inputs.forEach(function (input, idx) {
    input.addEventListener('input', function () {
      input.value = normalize(input.value);
      updateCombined();
      if (input.value && idx < inputs.length - 1) {
        inputs[idx + 1].focus();
      }
    });

    input.addEventListener('keydown', function (event) {
      if (event.key === 'Backspace' && !input.value && idx > 0) {
        inputs[idx - 1].focus();
      }
    });

    input.addEventListener('paste', function (event) {
      var text = (event.clipboardData || window.clipboardData).getData('text');
      var digits = (text || '').replace(/\D/g, '').slice(0, 6).split('');
      if (!digits.length) {
        return;
      }
      event.preventDefault();
      inputs.forEach(function (box, i) {
        box.value = digits[i] || '';
      });
      updateCombined();
      var last = Math.min(digits.length - 1, inputs.length - 1);
      inputs[last].focus();
    });
  });

  form.addEventListener('submit', function () {
    updateCombined();
  });

  inputs[0].focus();
});
