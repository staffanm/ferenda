/* The ⌘K command palette -- live full-text search against the REST API. Its
   own script: the search UI is unrelated to the TOC scrollspy and is global
   to every page, so it does not ride along in scrollspy.js. The API is always
   same-origin (the site and the API are served by one process, lagen serve),
   so requests are relative ('/api/v1/...') -- never a baked absolute base,
   which can only go stale and point a cached page at the wrong/dead port.
   Debounced; renders the top hits as links to each document's matching
   paragraph. */
(function () {
  var overlay = null, results = null, refine = null, timer = null, seq = 0, sel = 0;

  // the API returns raw field values (correct for an API); the indexed text is
  // parsed remote content, so everything interpolated into innerHTML is escaped
  // here. The highlight fragment is the one exception with markup: OpenSearch
  // html-encodes the body (search.py HIGHLIGHT encoder) and only injects <em>.
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function choices() {
    if (!overlay) return [];
    var out = Array.prototype.slice.call(results.querySelectorAll('.search-hit'));
    if (refine && !refine.hidden) out.push(refine);
    return out;
  }
  function select(i) {
    var hs = choices();
    if (!hs.length) return;
    sel = (i + hs.length) % hs.length;
    hs.forEach(function (h, n) { h.classList.toggle('sel', n === sel); });
    hs[sel].scrollIntoView({ block: 'nearest' });
  }
  function render(items, total, q) {
    if (!results) return;
    if (!items.length) {
      refine.hidden = true;
      results.innerHTML = '<div class="search-note">Inga träffar för ' +
        '\u201d' + esc(q) + '\u201d.</div>';
      return;
    }
    var hitHtml = items.map(function (r) {
      // r.url is the hosted page path (server-computed via layout.page_relpath);
      // a fragment hit deep-links to its paragraph anchor (the node id == pinpoint)
      var frag = r.fragments && r.fragments[0];
      var hl = (frag && frag.highlight[0]) || (r.highlight && r.highlight[0]) || '';
      var target = (r.url || '#') + (frag && frag.pinpoint ? '#' + frag.pinpoint : '');
      // lead with the page title (display: short name + acronym where the act
      // has them, else the full title -- the same heading the document page
      // shows), and carry the citation id (CELEX / "SFS 2018:218") as the sub,
      // shown only when it differs from the title (DV's label == its title)
      var primary = r.display || r.title || r.identifier || r.uri;
      return '<a class="search-hit" href="' + esc(target) + '">' +
        '<span class="hit-title">' + esc(primary) + '</span>' +
        (r.identifier && r.identifier !== primary ?
          '<span class="hit-sub">' + esc(r.identifier) + '</span>' : '') +
        (hl ? '<span class="hit-snip">' + hl + '</span>' : '') + '</a>';
    }).join('');
    var searchUrl = '/sok/?q=' + encodeURIComponent(q);
    var count = new Intl.NumberFormat('sv-SE').format(total);
    refine.href = searchUrl;
    refine.innerHTML = 'Avgränsa ' + esc(count) + ' träffar';
    refine.hidden = false;
    results.innerHTML = hitHtml;
    // the first hit is the resolved target for a citation-shaped query
    // ("avtalslagen 36" -> §36); selecting it means Enter goes straight there
    select(0);
  }
  function go() {
    // navigate to the selected hit (the first by default == the resolved target)
    var hs = choices();
    if (!hs.length) return false;
    window.location.href = hs[sel].getAttribute('href');
    return true;
  }
  function run(q, andGo) {
    var mine = ++seq;
    if (!q.trim()) {
      if (results) results.innerHTML = '';
      if (refine) refine.hidden = true;
      return;
    }
    fetch('/api/v1/search?limit=8&q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) { if (mine === seq) { render(d.results || [], d.total || 0, q); if (andGo) go(); } })
      .catch(function () {
        if (mine === seq && results) {
          refine.hidden = true;
          results.innerHTML = '<div class="search-note">Sökningen kunde inte ' +
            'nås.</div>';
        }
      });
  }
  function open() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'search-overlay';
    overlay.innerHTML = '<div class="search-box"><div class="search-input-row">' +
      '<a class="search-refine" href="/sok/" hidden></a><input autofocus ' +
      'placeholder="Sök lag, paragraf, rättsfall…"></div>' +
      '<div class="search-results"></div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    var input = overlay.querySelector('input');
    refine = overlay.querySelector('.search-refine');
    results = overlay.querySelector('.search-results');
    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value;
      timer = setTimeout(function () { run(q); }, 180);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); select(sel + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); select(sel - 1); }
      else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !refine.hidden) {
        // The explicit bridge from quick navigation to the complete, faceted
        // result list: either horizontal arrow selects it, then Enter follows it.
        e.preventDefault();
        select(choices().indexOf(refine));
      }
      else if (e.key === 'Enter') {
        // Enter goes to the selected hit -- the first by default, which for a
        // citation-shaped query is the resolved §/article. If the debounced
        // results aren't in yet, fetch now and jump to the first hit.
        e.preventDefault();
        clearTimeout(timer);
        if (!go()) run(input.value, true);
      }
    });
    input.focus();
  }
  function close() {
    if (overlay) { overlay.remove(); overlay = null; results = null; refine = null; }
  }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); }
    if (e.key === 'Escape') close();
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-search]')) { e.preventDefault(); open(); }
  });
})();
