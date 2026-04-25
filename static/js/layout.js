(function () {
  function initUserMenu() {
    var menu = document.querySelector('.user-menu');
    var toggleButton = document.querySelector('[data-user-menu-toggle="true"]');
    var dropdown = document.getElementById('userMenuDropdown');

    if (!menu || !toggleButton || !dropdown) {
      return;
    }

    toggleButton.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      dropdown.classList.toggle('active');
    });

    document.addEventListener('click', function (event) {
      if (!menu.contains(event.target)) {
        dropdown.classList.remove('active');
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUserMenu);
  } else {
    initUserMenu();
  }
})();
