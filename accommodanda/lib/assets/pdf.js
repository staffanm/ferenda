/* "Spara som PDF": the printer icon on the short-id row of the TOC rail,
   driving the print-typeset export of this very page. The dialog's options
   mirror what the export can do: one or two text columns, the page's TOC
   with printed page numbers, the SFS amendment register, and the context
   rail's sections printed under each provision, chosen by kind. The kind
   list is read out of the page's own #lagen-context island
   (the same panels scrollspy feeds the rail with), so the dialog never
   offers a kind this document does not have -- and it is read lazily on
   first open, since a large statute's island is megabytes. Injected by
   script rather than baked into the page HTML, so the whole feature ships
   as an assets-only refresh.

   The dialog itself does not render anything. It opens /api/v1/pdf/vanta,
   which starts the job, shows the render's progress and then becomes the
   PDF -- see api/pdfjob.py. Doing the work here, as one long fetch, meant
   holding a request open past nginx's 60-second timeout (a 504 for a render
   that had in fact succeeded) and parking the reader in a blank tab
   meanwhile. */
(function () {
  var tocCol = document.querySelector('aside.toc-col');
  if (!tocCol) return;               // solo pages are not documents

  // The printer icon rides the short-id row at the top of the TOC rail.
  // A document with no headings has an empty rail and so had no such row,
  // and no way to reach the export at all -- räntelagen among them. It gets
  // the row anyway, carrying the document's own short id from the
  // frontmatter, so the affordance is in the same place on every document.
  var row = tocCol.querySelector('.toc-list');
  if (!row) {
    row = document.createElement('div');
    row.className = 'toc-list';
    var eyebrow = document.querySelector('.frontmatter .eyebrow');
    var label = document.createElement('span');
    label.className = 'lvl1 toc-top';
    label.textContent = eyebrow ? eyebrow.textContent.trim() : 'Dokumentet';
    row.appendChild(label);
    tocCol.appendChild(row);
  }

  var open = document.createElement('button');
  open.type = 'button';
  open.className = 'pdf-open';
  open.setAttribute('aria-label', 'Spara som PDF');
  open.title = 'Spara som PDF';
  open.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9V3h12v6"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>';
  row.appendChild(open);

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
    var isSfs = !!document.querySelector('section.andringar .andring');
    dlg = document.createElement('dialog');
    dlg.className = 'pdf-dialog';
    dlg.innerHTML =
      '<form method="dialog"><h2>Spara som PDF</h2>' +
      '<fieldset class="pdf-layout"><legend>Sidlayout</legend>' +
      '<label><input type="radio" name="columns" value="1" checked> ' +
      'En kolumn</label>' +
      '<label><input type="radio" name="columns" value="2"> ' +
      'Två kolumner</label></fieldset>' +
      '<label class="pdf-row"><input type="checkbox" name="toc" checked> ' +
      'Innehållsförteckning med sidnummer</label>' +
      (isSfs
        ? '<label class="pdf-row"><input type="checkbox" name="andringar" ' +
          'checked> Ändringar och övergångsbestämmelser</label>'
        : '') +
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
    var toc = dlg.querySelector('input[name=toc]');
    var andringar = dlg.querySelector('input[name=andringar]');
    var columnChoices = dlg.querySelectorAll('input[name=columns]');
    Array.prototype.forEach.call(columnChoices, function (choice) {
      choice.addEventListener('change', function () {
        if (choice.value === '2' && choice.checked) {
          toc.checked = false;
          if (andringar) andringar.checked = false;
          if (kontext) {
            kontext.checked = false;
            kontext.disabled = true;
            fs.disabled = true;
          }
        } else if (choice.value === '1' && choice.checked && kontext) {
          kontext.disabled = false;
          fs.disabled = !kontext.checked;
        }
      });
    });
    // The dialog hands the export over to a page of its own
    // (/api/v1/pdf/vanta), which starts the render, shows how far it has
    // come and then becomes the PDF. The tab opens inside this click --
    // a popup blocker allows nothing opened a minute later -- and it opens
    // on a real address, where it used to open blank and be written into.
    var go = dlg.querySelector('.pdf-go');

    function fail(message) {
      var err = dlg.querySelector('.pdf-err');
      if (!err) {
        err = document.createElement('p');
        err.className = 'pdf-err';
        dlg.querySelector('.pdf-actions').before(err);
      }
      err.textContent = message;
    }

    function waitUrl() {
      var q = '/api/v1/pdf/vanta?path=' + encodeURIComponent(location.pathname);
      if (toc.checked) q += '&toc=1';
      if (andringar && !andringar.checked) q += '&andringar=0';
      if (dlg.querySelector('input[name=columns]:checked').value === '2') {
        q += '&kolumner=2';
      }
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
      if (dlg.querySelector('input[name=mode]:checked').value !== 'visa') {
        q += '&download=1';
      }
      return q;
    }

    go.addEventListener('click', function () {
      var old = dlg.querySelector('.pdf-err');
      if (old) old.remove();
      if (window.open(waitUrl(), '_blank')) {
        dlg.close();
      } else {
        fail('Webbl\u00e4saren blockerade den nya fliken. Till\u00e5t popup-f\u00f6nster ' +
             'f\u00f6r lagen.nu, eller skriv ut sidan med Ctrl+P.');
      }
    });

    document.body.appendChild(dlg);
  }

  open.addEventListener('click', function () {
    if (!dlg) build();
    dlg.showModal();
  });
})();
