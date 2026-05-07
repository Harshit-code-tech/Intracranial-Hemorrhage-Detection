(function () {
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

  // ── File size guard ──────────────────────────────────────────────────────
  var MAX_MB = 50;

  function removeSizeWarning() {
    var w = document.getElementById('uploadSizeWarning');
    if (w) w.remove();
  }

  function showSizeWarning(sizeMB) {
    removeSizeWarning();
    var el = document.createElement('div');
    el.id = 'uploadSizeWarning';
    el.style.cssText = 'margin-top:14px;padding:14px 18px;border-radius:12px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.35);color:#fde68a;font-size:0.88rem;line-height:1.6;display:flex;align-items:flex-start;gap:12px;';
    el.innerHTML =
      '<span style="font-size:1.5rem;flex-shrink:0;line-height:1.2">😅</span>' +
      '<div><strong>File too large (' + sizeMB.toFixed(1) + ' MB)</strong><br>' +
      'Because of free-tier limitations, we can\'t go further than <strong>' + MAX_MB + ' MB</strong> per upload. ' +
      'Please split your scan into smaller batches and try again.</div>';
    var active = document.querySelector('.tab-panel.active');
    if (active && active.parentNode) {
      active.parentNode.insertBefore(el, active.nextSibling);
    }
  }

  function attachSizeCheck(inputId, submitId, formId, overlayId) {
    var inp = document.getElementById(inputId);
    var sub = document.getElementById(submitId);
    var frm = document.getElementById(formId);
    var ov = document.getElementById(overlayId);
    if (!inp || !frm) return;
    frm.addEventListener('submit', function (e) {
      var totalMB = 0;
      for (var i = 0; i < inp.files.length; i++) { totalMB += inp.files[i].size / (1024 * 1024); }
      if (totalMB > MAX_MB) {
        e.preventDefault();
        if (sub) sub.disabled = false;
        if (ov) ov.style.display = 'none';
        showSizeWarning(totalMB);
      } else {
        removeSizeWarning();
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
        tabs.forEach(function (item) { item.classList.remove('active'); });
        panels.forEach(function (panel) { panel.classList.remove('active'); });
        tab.classList.add('active');
        var target = document.getElementById('tab-' + tab.dataset.tab);
        if (target) target.classList.add('active');
        removeSizeWarning();
      });
    });

    wireDropzone({
      zoneId: 'dropzoneSingle', inputId: 'singleInput', infoId: 'singleInfo',
      labelId: 'singleFileName', clearSel: '.js-clear-single',
      submitId: 'singleSubmit', formId: 'singleForm', overlayId: 'singleOverlay',
      multi: false
    });

    wireDropzone({
      zoneId: 'dropzoneMulti', inputId: 'multiInput', infoId: 'multiInfo',
      labelId: 'multiFileName', clearSel: '.js-clear-multi',
      submitId: 'multiSubmit', formId: 'multiForm', overlayId: 'multiOverlay',
      multi: true
    });

    attachSizeCheck('singleInput', 'singleSubmit', 'singleForm', 'singleOverlay');
    attachSizeCheck('multiInput', 'multiSubmit', 'multiForm', 'multiOverlay');

    var dirInput = document.getElementById('dirPath');
    var dirSubmit = document.getElementById('dirSubmit');
    if (dirInput && dirSubmit) {
      function checkDir() { dirSubmit.disabled = !dirInput.value.trim(); }
      dirInput.addEventListener('input', checkDir);
      checkDir();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUploadPage);
  } else {
    initUploadPage();
  }
})();
