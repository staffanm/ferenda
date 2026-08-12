/* "Spara som PDF": the printer icon on the short-id row of the TOC rail,
   driving
   /api/v1/pdf -- the print-typeset export of this very page. The dialog's
   options mirror what the export can do: the page's TOC with printed page
   numbers, and the context rail's sections printed under each provision,
   chosen by kind. The kind list is read out of the page's own
   #lagen-context island (the same panels scrollspy feeds the rail with),
   so the dialog never offers a kind this document does not have -- and it
   is read lazily on first open, since a large statute's island is
   megabytes. Injected by script rather than baked into the page HTML, so
   the whole feature ships as an assets-only refresh. */
(function () {
  var tocCol = document.querySelector('aside.toc-col');
  if (!tocCol) return;               // solo pages are not documents

  // the printer icon rides the short-id row at the top of the TOC rail
  // (absolute against .toc-list; a page with no TOC anchors it to the
  // column itself, which is also positioned)
  var open = document.createElement('button');
  open.type = 'button';
  open.className = 'pdf-open';
  open.setAttribute('aria-label', 'Spara som PDF');
  open.title = 'Spara som PDF';
  open.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9V3h12v6"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>';
  (tocCol.querySelector('.toc-list') || tocCol).appendChild(open);

  // [{key, label}] in first-seen order across the panels, deduplicated --
  // the order the rail itself ranks sections in
  function collectKinds() {
    var el = document.getElementById('lagen-context');
    if (!el) return [];
    var island = JSON.parse(el.textContent);
    var kinds = [], seen = {};
    var probe = document.createElement('div');
    Object.keys(island).forEach(function (id) {
      probe.innerHTML = island[id];
      Array.prototype.forEach.call(probe.querySelectorAll('.rail-sec'),
        function (sec) {
          var key = sec.getAttribute('data-sec');
          if (!key || seen[key]) return;
          seen[key] = true;
          kinds.push({ key: key, label: sec.getAttribute('data-label') || key });
        });
    });
    return kinds;
  }

  var dlg = null;
  function build() {
    var kinds = collectKinds();
    dlg = document.createElement('dialog');
    dlg.className = 'pdf-dialog';
    dlg.innerHTML =
      '<form method="dialog"><h2>Spara som PDF</h2>' +
      '<label class="pdf-row"><input type="checkbox" name="toc" checked> ' +
      'Innehållsförteckning med sidnummer</label>' +
      (kinds.length
        ? '<label class="pdf-row"><input type="checkbox" name="kontext"> ' +
          'Kontext under varje avsnitt</label>' +
          '<fieldset class="pdf-kinds" disabled></fieldset>'
        : '') +
      '<div class="pdf-row pdf-mode">' +
      '<label><input type="radio" name="mode" value="visa" checked> Visa</label>' +
      '<label><input type="radio" name="mode" value="ladda-ned"> Ladda ned</label>' +
      '</div>' +
      '<p class="pdf-hint">För papper direkt: Ctrl+P skriver ut sidan i samma ' +
      'pappersform, utan innehållsförteckning och kontext.</p>' +
      '<div class="pdf-actions"><button value="cancel">Avbryt</button>' +
      '<button type="button" class="pdf-go">Skapa PDF</button></div></form>';
    var fs = dlg.querySelector('.pdf-kinds');
    kinds.forEach(function (k) {        // DOM, not innerHTML: labels are data
      var label = document.createElement('label');
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.name = 'kind';
      box.value = k.key;
      box.checked = true;
      label.appendChild(box);
      label.appendChild(document.createTextNode(' ' + k.label));
      fs.appendChild(label);
    });
    var kontext = dlg.querySelector('input[name=kontext]');
    if (kontext) kontext.addEventListener('change', function () {
      fs.disabled = !kontext.checked;
    });
    // the export runs as a fetch so the dialog can show real progress; a
    // large statute lays out for a minute or more on first request (the
    // server caches the result, so the next one is instant)
    var go = dlg.querySelector('.pdf-go');
    var pending = null;      // {ctrl, win} while a fetch is in flight

    function busy(on) {
      go.disabled = on;
      go.classList.toggle('busy', on);
      go.textContent = on ? 'Skapar PDF \u2026' : 'Skapa PDF';
    }

    function fail(message) {
      var err = dlg.querySelector('.pdf-err');
      if (!err) {
        err = document.createElement('p');
        err.className = 'pdf-err';
        dlg.querySelector('.pdf-actions').before(err);
      }
      err.textContent = message;
    }

    function exportUrl() {
      var q = '/api/v1/pdf?path=' + encodeURIComponent(location.pathname);
      if (dlg.querySelector('input[name=toc]').checked) q += '&toc=1';
      if (kontext && kontext.checked) {
        var boxes = dlg.querySelectorAll('input[name=kind]');
        var picked = Array.prototype.filter.call(boxes,
          function (b) { return b.checked; })
          .map(function (b) { return b.value; });
        if (picked.length) {
          q += '&kontext=' + encodeURIComponent(
            picked.length === boxes.length ? 'alla' : picked.join(','));
        }
      }
      return q;
    }

    go.addEventListener('click', function () {
      var visa = dlg.querySelector('input[name=mode]:checked').value === 'visa';
      // the viewing tab must open inside the click gesture -- opened after
      // a minute-long fetch it would be popup-blocked
      var win = visa ? window.open('', '_blank') : null;
      if (win) {
        win.document.title = 'Skapar PDF \u2026';
        win.document.body.textContent = 'Skapar PDF \u2026';
      }
      var ctrl = new AbortController();
      pending = { ctrl: ctrl, win: win };
      busy(true);
      var old = dlg.querySelector('.pdf-err');
      if (old) old.remove();
      fetch(exportUrl(), { signal: ctrl.signal }).then(function (r) {
        if (!r.ok) throw new Error('servern svarade ' + r.status);
        // the filename rule lives server-side only (api/pdf.filename_for);
        // a blob URL carries none, so lift it off the response header
        var disp = /filename="([^"]+)"/.exec(
          r.headers.get('content-disposition') || '');
        return r.blob().then(function (blob) {
          return { blob: blob, name: disp ? disp[1] : 'dokument.pdf' };
        });
      }).then(function (res) {
        var url = URL.createObjectURL(res.blob);
        if (win) {
          win.location = url;
        } else {
          var a = document.createElement('a');
          a.href = url;
          a.download = res.name;
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
        pending = null;
        busy(false);
        dlg.close();
      }).catch(function (err) {
        pending = null;
        busy(false);
        if (err.name === 'AbortError') return;   // Avbryt closed the dialog
        if (win) win.close();
        fail('Det gick inte att skapa PDF:en (' + err.message + '). ' +
             'Prova igen, eller skriv ut sidan med Ctrl+P.');
      });
    });

    // Avbryt (or Esc) while a render runs: stop the fetch, drop the tab
    dlg.addEventListener('close', function () {
      if (!pending) return;
      pending.ctrl.abort();
      if (pending.win) pending.win.close();
      pending = null;
      busy(false);
    });
    document.body.appendChild(dlg);
  }

  open.addEventListener('click', function () {
    if (!dlg) build();
    dlg.showModal();
  });
})();
