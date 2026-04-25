(function () {
  function initBatchProgress() {
    var page = document.querySelector('.batch-page');
    if (!page) {
      return;
    }

    var statusUrl = page.dataset.statusUrl;
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
          currentFile.textContent = data.current_file ? 'Processing: ' + data.current_file : '';

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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBatchProgress);
  } else {
    initBatchProgress();
  }
})();
