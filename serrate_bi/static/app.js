// Interacciones mínimas: pestañas, trabajos en segundo plano con seguimiento, copiar al portapapeles.
(function () {
  function activateTab(id) {
    var tabs = document.querySelectorAll('.tabs a');
    if (!tabs.length) return;
    var found = false;
    tabs.forEach(function (a) {
      var on = a.getAttribute('href') === '#' + id;
      a.classList.toggle('active', on);
      if (on) found = true;
    });
    if (!found) { id = tabs[0].getAttribute('href').slice(1); tabs[0].classList.add('active'); }
    document.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t.id === 'tab-' + id); });
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('.tabs a');
    if (a) { e.preventDefault(); var id = a.getAttribute('href').slice(1); history.replaceState(null, '', '#' + id); activateTab(id); }
    var c = e.target.closest('[data-copy]');
    if (c) {
      var el = document.getElementById(c.getAttribute('data-copy'));
      if (el && navigator.clipboard) navigator.clipboard.writeText(el.innerText).then(function () {
        var t = c.textContent; c.textContent = 'Copiado ✓'; setTimeout(function () { c.textContent = t; }, 1500);
      });
    }
  });
  if (document.querySelector('.tabs')) activateTab((location.hash || '#').slice(1));

  function watchRun(runId, box) {
    var bar = document.getElementById('progress');
    var logEl = document.getElementById('progress-log');
    if (bar) { bar.classList.add('on'); }
    function tick() {
      fetch('/trabajos/' + runId).then(function (r) { return r.json(); }).then(function (run) {
        var msg = document.getElementById('progress-msg');
        if (msg) msg.textContent = 'Trabajo #' + run.id + ' (' + run.kind + '): ' + run.status;
        if (logEl) { logEl.textContent = run.log || ''; logEl.scrollTop = logEl.scrollHeight; logEl.style.display = 'block'; }
        if (run.status === 'en cola' || run.status === 'en curso') { setTimeout(tick, 2000); }
        else {
          if (msg) msg.textContent = 'Trabajo #' + run.id + ' ' + run.status + ': ' + (run.summary || '');
          var sp = document.querySelector('#progress .spinner'); if (sp) sp.style.display = 'none';
          var rl = document.getElementById('progress-reload'); if (rl) rl.style.display = 'inline-flex';
        }
      }).catch(function () { setTimeout(tick, 4000); });
    }
    tick();
  }
  window.serrateWatchRun = watchRun;

  document.querySelectorAll('form[data-job]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = f.querySelector('button[type=submit]');
      if (btn) btn.disabled = true;
      fetch(f.action, { method: 'POST', body: new FormData(f) }).then(function (r) { return r.json(); })
        .then(function (d) { watchRun(d.run_id); })
        .catch(function () { alert('No se pudo iniciar el trabajo.'); if (btn) btn.disabled = false; });
    });
  });
  var auto = document.querySelector('[data-watch-run]');
  if (auto) watchRun(auto.getAttribute('data-watch-run'));
})();
