(function () {
  function initUserMenu() {
    var menu = document.querySelector('.user-menu');
    var toggleButton = document.querySelector('[data-user-menu-toggle="true"]');
    var dropdown = document.getElementById('userMenuDropdown');

    if (!menu || !toggleButton || !dropdown) {
      return;
    }

    function closeMenu() {
      dropdown.classList.remove('active');
    }

    function toggleMenu(event) {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      dropdown.classList.toggle('active');
    }

    toggleButton.addEventListener('click', toggleMenu);
    document.addEventListener('click', function (event) {
      if (!menu.contains(event.target)) {
        closeMenu();
      }
    });
  }

  function initPasswordModal() {
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

  function initUploadPage() {
    var tabs = document.querySelectorAll('.upload-tab');
    var panels = document.querySelectorAll('.tab-panel');

    if (!tabs.length || !panels.length) {
      return;
    }

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (item) {
          item.classList.remove('active');
        });
        panels.forEach(function (panel) {
          panel.classList.remove('active');
        });

        tab.classList.add('active');
        var target = document.getElementById('tab-' + tab.dataset.tab);
        if (target) {
          target.classList.add('active');
        }
      });
    });

    function wireDropzone(options) {
      var zone = document.getElementById(options.zoneId);
      var input = document.getElementById(options.inputId);
      var info = document.getElementById(options.infoId);
      var label = document.getElementById(options.labelId);
      var clearButton = document.querySelector(options.clearSel);
      var submit = document.getElementById(options.submitId);
      var form = document.getElementById(options.formId);
      var overlay = document.getElementById(options.overlayId);

      if (!zone || !input || !info || !label || !submit) {
        return;
      }

      function showFiles(files) {
        var validFiles = [];
        for (var i = 0; i < files.length; i++) {
          var name = files[i].name.toLowerCase();
          if (name.endsWith('.dcm') || name.endsWith('.zip')) {
            validFiles.push(files[i]);
          }
        }

        if (!validFiles.length) {
          return;
        }

        if (options.multi) {
          var totalSizeMB = 0;
          for (var j = 0; j < validFiles.length; j++) {
            totalSizeMB += validFiles[j].size / (1024 * 1024);
          }
          label.textContent = validFiles.length + ' file' + (validFiles.length > 1 ? 's' : '') + ' (' + totalSizeMB.toFixed(1) + ' MB)';
        } else {
          label.textContent = validFiles[0].name;
        }

        info.style.display = 'flex';
        zone.style.display = 'none';
        submit.disabled = false;
      }

      function reset() {
        input.value = '';
        info.style.display = 'none';
        zone.style.display = 'flex';
        submit.disabled = true;
      }

      zone.addEventListener('click', function () {
        input.click();
      });

      zone.addEventListener('dragover', function (event) {
        event.preventDefault();
        zone.classList.add('dragover');
      });

      zone.addEventListener('dragleave', function () {
        zone.classList.remove('dragover');
      });

      zone.addEventListener('drop', function (event) {
        event.preventDefault();
        zone.classList.remove('dragover');
        if (event.dataTransfer.files.length) {
          input.files = event.dataTransfer.files;
          showFiles(event.dataTransfer.files);
        }
      });

      input.addEventListener('change', function () {
        if (input.files.length) {
          showFiles(input.files);
        }
      });

      if (clearButton) {
        clearButton.addEventListener('click', reset);
      }

      if (form && overlay) {
        form.addEventListener('submit', function () {
          overlay.style.display = 'flex';
          submit.disabled = true;
        });
      }
    }

    wireDropzone({
      zoneId: 'dropzoneSingle',
      inputId: 'singleInput',
      infoId: 'singleInfo',
      labelId: 'singleFileName',
      clearSel: '.js-clear-single',
      submitId: 'singleSubmit',
      formId: 'singleForm',
      overlayId: 'singleOverlay',
      multi: false
    });

    wireDropzone({
      zoneId: 'dropzoneMulti',
      inputId: 'multiInput',
      infoId: 'multiInfo',
      labelId: 'multiFileName',
      clearSel: '.js-clear-multi',
      submitId: 'multiSubmit',
      formId: 'multiForm',
      overlayId: 'multiOverlay',
      multi: true
    });

    var dirInput = document.getElementById('dirPath');
    var dirSubmit = document.getElementById('dirSubmit');

    if (dirInput && dirSubmit) {
      function checkDir() {
        dirSubmit.disabled = !dirInput.value.trim();
      }

      dirInput.addEventListener('input', checkDir);
      checkDir();
    }
  }

  function initBatchProgress() {
    var page = document.querySelector('.batch-page');

    if (!page) {
      return;
    }

    var statusUrl = page.dataset.statusUrl;
    var reportsUrl = page.dataset.reportsUrl;
    var pollMs = 1000;

    var title = document.getElementById('batchTitle');
    var subtitle = document.getElementById('batchSubtitle');
    var fill = document.getElementById('progressFill');
    var pctLabel = document.getElementById('progressPct');
    var currentFile = document.getElementById('currentFile');
    var statTotal = document.getElementById('statTotal');
    var statProc = document.getElementById('statProcessed');
    var statOK = document.getElementById('statSucceeded');
    var statFail = document.getElementById('statFailed');
    var feedPanel = document.getElementById('feedPanel');
    var feedList = document.getElementById('batchFeed');
    var donePanel = document.getElementById('donePanel');
    var doneSummary = document.getElementById('doneSummary');
    var failPanel = document.getElementById('failPanel');
    var failList = document.getElementById('failList');
    var prevIds = [];

    if (!statusUrl || !title || !subtitle || !fill || !pctLabel || !currentFile || !statTotal || !statProc || !statOK || !statFail || !feedPanel || !feedList || !donePanel || !doneSummary || !failPanel || !failList) {
      return;
    }

    function poll() {
      fetch(statusUrl)
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          var pct = data.total > 0 ? Math.round(data.processed / data.total * 100) : 0;

          statTotal.textContent = data.total;
          statProc.textContent = data.processed;
          statOK.textContent = data.succeeded;
          statFail.textContent = data.failed_count;

          fill.style.width = pct + '%';
          pctLabel.textContent = pct + '%';

          if (data.current_file) {
            currentFile.textContent = 'Processing: ' + data.current_file;
          } else {
            currentFile.textContent = '';
          }

          if (data.image_ids && data.image_ids.length) {
            feedPanel.style.display = 'block';
            data.image_ids.forEach(function (imageId) {
              if (prevIds.indexOf(imageId) === -1) {
                prevIds.push(imageId);
                var li = document.createElement('li');
                var link = document.createElement('a');
                link.href = '/case/' + imageId;
                link.textContent = imageId;
                li.appendChild(link);
                feedList.insertBefore(li, feedList.firstChild);
                while (feedList.children.length > 20) {
                  feedList.removeChild(feedList.lastChild);
                }
              }
            });
          }

          if (data.status === 'completed' || data.status === 'failed') {
            title.textContent = 'Batch Complete';
            subtitle.textContent = '';
            donePanel.style.display = 'block';
            doneSummary.textContent = data.succeeded + ' of ' + data.total + ' files processed successfully' + (data.failed_count > 0 ? ', ' + data.failed_count + ' failed' : '') + '.';

            if (data.failed_ids && data.failed_ids.length) {
              failPanel.style.display = 'block';
              data.failed_ids.forEach(function (failedId) {
                var li = document.createElement('li');
                li.textContent = failedId;
                failList.appendChild(li);
              });
            }

            if (reportsUrl) {
              return;
            }
            return;
          }

          setTimeout(poll, pollMs);
        })
        .catch(function () {
          setTimeout(poll, pollMs * 3);
        });
    }

    poll();
  }

  function initPages() {
    initUserMenu();
    initPasswordModal();
    initUploadPage();
    initBatchProgress();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPages);
  } else {
    initPages();
  }
})();
