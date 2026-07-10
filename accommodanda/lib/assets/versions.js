/* Client layer for the version history: the "jämför med tidigare lydelse"
   <select> (in the details.lydelser panel on statute + lydelse pages) fetches
   the marked-up diff from /api/v1/document/diff and swaps it into #dokument --
   the old pipeline's ?diff=true&from=… view, now an API round-trip on the
   static page. The choice is mirrored into the ?diff= query param so a diff
   view is deep-linkable; clearing the select restores the original text
   without a reload. Plain DOM, no deps. */
(function () {
  var sel = document.querySelector('select[data-diff]');
  var doc = document.getElementById('dokument');
  if (!sel || !doc) return;
  var original = null;

  function show(version) {
    var url = new URL(location.href);
    if (!version) {
      if (original !== null) doc.innerHTML = original;
      url.searchParams.delete('diff');
      history.replaceState(null, '', url);
      return;
    }
    if (original === null) original = doc.innerHTML;
    doc.innerHTML = '<div class="diff-note">Hämtar skillnader …</div>';
    var q = '/api/v1/document/diff?uri=' + encodeURIComponent(sel.dataset.uri) +
            '&from=' + encodeURIComponent(version) +
            (sel.dataset.to ? '&to=' + encodeURIComponent(sel.dataset.to) : '');
    fetch(q).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      doc.innerHTML = html;          // explanatory note + marked-up text, API-composed
      url.searchParams.set('diff', version);
      history.replaceState(null, '', url);
    }).catch(function () {
      doc.innerHTML = '<div class="diff-note">Kunde inte hämta jämförelsen. ' +
                      'Prova att ladda om sidan.</div>';
    });
  }

  sel.addEventListener('change', function () { show(sel.value); });
  var wanted = new URL(location.href).searchParams.get('diff');
  if (wanted && Array.prototype.some.call(sel.options, function (o) {
        return o.value === wanted; })) {
    sel.value = wanted;
    sel.closest('details').open = true;
    show(wanted);
  }
})();
