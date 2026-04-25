(function () {
  function initProfilePage() {
    var openButton = document.querySelector('.js-open-password-modal');
    var closeButtons = document.querySelectorAll('.js-close-password-modal');
    var modal = document.querySelector('.js-password-modal');
    var form = document.getElementById('changePasswordForm');
    var message = document.getElementById('passwordMessage');

    if (!openButton || !closeButtons.length || !modal || !form || !message) {
      return;
    }

    function openModal() {
      modal.style.display = 'block';
      form.reset();
      message.innerHTML = '';
    }

    function closeModal() {
      modal.style.display = 'none';
    }

    openButton.addEventListener('click', openModal);
    closeButtons.forEach(function (button) {
      button.addEventListener('click', closeModal);
    });

    document.addEventListener('click', function (event) {
      if (event.target === modal) {
        closeModal();
      }
    });

    form.addEventListener('submit', async function (event) {
      event.preventDefault();

      var currentPassword = document.getElementById('currentPassword').value;
      var newPassword = document.getElementById('newPassword').value;
      var confirmPassword = document.getElementById('confirmPassword').value;
      var endpoint = form.dataset.changePasswordUrl;

      if (newPassword !== confirmPassword) {
        message.innerHTML = '<div class="alert alert-error">Passwords do not match</div>';
        return;
      }

      try {
        var response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            confirm_password: confirmPassword
          })
        });

        var data = await response.json();

        if (response.ok) {
          message.innerHTML = '<div class="alert alert-success">' + data.message + '</div>';
          setTimeout(closeModal, 2000);
        } else {
          message.innerHTML = '<div class="alert alert-error">' + (data.error || 'Unable to update password') + '</div>';
        }
      } catch (error) {
        message.innerHTML = '<div class="alert alert-error">An error occurred</div>';
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initProfilePage);
  } else {
    initProfilePage();
  }
})();
