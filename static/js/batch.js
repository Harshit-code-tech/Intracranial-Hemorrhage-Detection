(function () {
  function initBatchProgress() {
    var page = document.querySelector('.batch-page');
    if (!page) {
      return;
    }

    var statusUrl = page.dataset.statusUrl;
    var pollMs = 1000;
    var expectedTotal = parseInt(page.dataset.expectedTotal || '0', 10) || 0;
    var cancelUrl = page.dataset.cancelUrl;

    var title = document.getElementById('batchTitle');
    var subtitle = document.getElementById('batchSubtitle');
    var queueStatus = document.getElementById('queueStatus');
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
    var cancelBtn = document.getElementById('cancelBatch');
    var seenIds = new Set();
    var canceled = false;

    if (!statusUrl || !title || !subtitle || !fill || !pctLabel || !currentFile || !statTotal || !statProc || !statOK || !statFail || !feedPanel || !feedList || !donePanel || !doneSummary || !failPanel || !failList) {
      return;
    }

    if (cancelBtn && cancelUrl) {
      cancelBtn.addEventListener('click', function () {
        if (!confirm('Cancel this batch? Any in-progress file may not complete.')) {
          return;
        }
        cancelBtn.disabled = true;
        fetch(cancelUrl, { method: 'POST' })
          .then(function (response) { return response.json(); })
          .then(function () {
            canceled = true;
            title.textContent = 'Batch Canceled';
            subtitle.textContent = 'The batch was canceled. You can start a new upload anytime.';
            currentFile.textContent = '';
            if (queueStatus) {
              queueStatus.textContent = '';
            }
          })
          .catch(function () {
            cancelBtn.disabled = false;
          });
      });
    }

    function poll() {
      fetch(statusUrl)
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          if (canceled) {
            return;
          }

          if (data.total > 0 && expectedTotal === 0) {
            expectedTotal = data.total;
          }
          var total = data.total > 0 ? data.total : expectedTotal;
          var pct = total > 0 ? Math.round(data.processed / total * 100) : 0;

          statTotal.textContent = total;
          statProc.textContent = data.processed;
          statOK.textContent = data.succeeded;
          statFail.textContent = data.failed_ids ? data.failed_ids.length : 0;

          if (queueStatus) {
            if (typeof data.queue_size === 'number') {
              queueStatus.textContent = 'Queue size: ' + data.queue_size;
            } else if (data.status === 'pending') {
              queueStatus.textContent = 'Queued for processing...';
            } else {
              queueStatus.textContent = '';
            }
          }

          if (data.status === 'pending' && expectedTotal > 0) {
            subtitle.textContent = 'Queued - waiting for a worker. Total files: ' + expectedTotal + '.';
          }

          fill.style.width = pct + '%';
          pctLabel.textContent = pct + '%';
          currentFile.textContent = data.current_file ? 'Processing: ' + data.current_file : '';

          if (data.image_ids && data.image_ids.length) {
            feedPanel.style.display = 'block';
            data.image_ids.forEach(function (imageId) {
              if (!seenIds.has(imageId)) {
                seenIds.add(imageId);
                var li = document.createElement('li');
                var link = document.createElement('a');
                link.href = '/case/' + imageId;
                link.textContent = imageId;
                li.appendChild(link);
                feedList.insertBefore(li, feedList.firstChild);
                while (feedList.children.length > 20) {
                  var last = feedList.lastChild;
                  if (!last) {
                    break;
                  }
                  var lastLink = last.querySelector('a');
                  if (lastLink && lastLink.textContent) {
                    seenIds.delete(lastLink.textContent);
                  }
                  feedList.removeChild(last);
                }
              }
            });
          }

          if (data.status === 'canceled') {
            title.textContent = 'Batch Canceled';
            subtitle.textContent = 'The batch was canceled.';
            if (cancelBtn) {
              cancelBtn.disabled = true;
            }
            return;
          }

          if (data.status === 'completed' || data.status === 'failed') {
            title.textContent = 'Batch Complete';
            subtitle.textContent = '';
            if (cancelBtn) {
              cancelBtn.disabled = true;
            }
            donePanel.style.display = 'block';
            var failCount = data.failed_ids ? data.failed_ids.length : 0;
            doneSummary.textContent = data.succeeded + ' of ' + total + ' files processed successfully' + (failCount > 0 ? ', ' + failCount + ' failed' : '') + '.';

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
