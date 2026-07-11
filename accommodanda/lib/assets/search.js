/* The ⌘K command palette -- live full-text search against the REST API, led
   by instant *local* hits: a terse pinpoint typed on a document page ("4",
   "4 §", "11:2", "4:", "art 5", "(42", "skäl 42", "bilaga III") resolves
   against the anchors of the page itself, no network, and shows the target's
   own text so the reader knows where Enter will land. Its own script: the
   search UI is unrelated to the TOC scrollspy and is global to every page,
   so it does not ride along in scrollspy.js. The API is always same-origin
   (the site and the API are served by one process, lagen serve), so requests
   are relative ('/api/v1/...') -- never a baked absolute base, which can only
   go stale and point a cached page at the wrong/dead port. Debounced; renders
   the top hits as links to each document's matching paragraph. */
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

  /* -- local quick-jump: lenient pinpoint grammar over the page's own anchors --
     Anchor id schemes (what the renderers mint): SFS "K4", "P4", "K4P2", "P4a";
     EU articles "4" / "4.2" / "6.2.a", recitals "recital-42", annexes
     "bilaga-3". A pattern only produces a hit when the anchor exists in the
     page, so "4" offers "4 §" on a statute and "Artikel 4" on an EU act. */

  var ROMAN = { i: 1, v: 5, x: 10, l: 50, c: 100 };
  function fromRoman(s) {
    var n = 0, prev = 0;
    s = s.toLowerCase();
    for (var i = s.length - 1; i >= 0; i--) {
      var v = ROMAN[s[i]];
      if (!v) return null;
      n += v < prev ? -v : v; prev = v;
    }
    return n;
  }

  // candidate (id, label) pairs for a terse pinpoint, most specific reading
  // first; existence in the DOM decides which of them become hits
  function candidates(q) {
    var out = [], m;
    if ((m = q.match(/^§?\s*(\d+)\s*([a-z])?\s*§?$/i))) {
      var pl = m[1] + (m[2] ? m[2].toLowerCase() : '');
      out.push(['P' + pl, (m[2] ? m[1] + ' ' + m[2].toLowerCase() : m[1]) + ' §']);
      if (!/§/.test(q)) out.push([pl, 'Artikel ' + pl]);   // bare "4" reads both ways
    }
    if ((m = q.match(/^\((\d+)\)?$/)))
      out.push(['recital-' + m[1], 'Skäl ' + m[1]]);
    if ((m = q.match(/^sk[äa]l\.?\s*(\d+)$/i)))
      out.push(['recital-' + m[1], 'Skäl ' + m[1]]);
    if ((m = q.match(/^(\d+)\s*:$/)))
      out.push(['K' + m[1], m[1] + ' kap.']);
    if ((m = q.match(/^(?:kap\.?\s*(\d+)|(\d+)\s*kap\.?)$/i)))
      out.push(['K' + (m[1] || m[2]), (m[1] || m[2]) + ' kap.']);
    if ((m = q.match(/^(\d+)\s*:\s*(\d+)\s*([a-z])?$/i)) ||
        (m = q.match(/^(\d+)\s*kap\.?\s*(\d+)\s*([a-z])?\s*§?$/i))) {
      var l = m[3] ? m[3].toLowerCase() : '';
      out.push(['K' + m[1] + 'P' + m[2] + l,
                m[1] + ' kap. ' + m[2] + (l ? ' ' + l : '') + ' §']);
    }
    if ((m = q.match(/^art(?:ikel|icle)?\.?\s*(\d+)\s*([a-z])?(?:[.\s]+(\d+))?$/i))) {
      var id = m[1] + (m[2] ? m[2].toLowerCase() : '') + (m[3] ? '.' + m[3] : '');
      out.push([id, 'Artikel ' + id]);
    }
    if ((m = q.match(/^(\d+)\.(\d+)(?:\.([a-z]))?$/)))
      out.push([m[1] + '.' + m[2] + (m[3] ? '.' + m[3] : ''),
                'Artikel ' + m[1] + '.' + m[2] + (m[3] ? ' ' + m[3] : '')]);
    if ((m = q.match(/^bil(?:aga)?\.?\s*(\d+|[ivxlc]+)$/i))) {
      var n = /^\d+$/.test(m[1]) ? +m[1] : fromRoman(m[1]);
      if (n) out.push(['bilaga-' + n, 'Bilaga ' + m[1].toUpperCase()]);
    }
    return out;
  }

  function localHits(q) {
    var seen = {}, hits = [];
    candidates(q.trim()).forEach(function (c) {
      var el = lagenDom.ownEl(c[0]);
      if (!el || seen[c[0]]) return;
      seen[c[0]] = true;
      hits.push({ id: c[0], label: c[1],
                  snip: el.textContent.replace(/💬|¶/g, ' ')
                          .replace(/\s+/g, ' ').trim().slice(0, 180) });
    });
    return hits;
  }

  function jump(id) {
    var el = lagenDom.ownEl(id);
    if (!el) return;
    lagenDom.flash(el);
    history.replaceState(null, '', '#' + id);
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

  // `total` is null while the API round-trip is pending: local hits paint
  // instantly, the emptiness verdict ("Inga träffar") waits for the API
  function render(local, items, total, q) {
    if (!results) return;
    var docTitle = document.querySelector('.gr-main h1');
    var localHtml = local.map(function (h) {
      return '<a class="search-hit local" data-local="' + esc(h.id) + '" ' +
        'href="#' + esc(h.id) + '">' +
        '<span class="hit-title">' + esc(h.label) +
        '<span class="hit-here">på denna sida</span></span>' +
        (docTitle ? '<span class="hit-sub">' + esc(docTitle.textContent) + '</span>' : '') +
        (h.snip ? '<span class="hit-snip">' + esc(h.snip) + '</span>' : '') + '</a>';
    }).join('');
    if (!local.length && !items.length) {
      refine.hidden = true;
      results.innerHTML = total === null ? '' :
        '<div class="search-note">Inga träffar för ”' + esc(q) + '”.</div>';
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
    if (total === null || !total) {
      refine.hidden = true;
    } else {
      refine.href = '/sok/?q=' + encodeURIComponent(q);
      refine.innerHTML = 'Avgränsa ' +
        esc(new Intl.NumberFormat('sv-SE').format(total)) + ' träffar';
      refine.hidden = false;
    }
    results.innerHTML = localHtml + hitHtml;
    // the first hit is the resolved target for a citation-shaped query -- a
    // local pinpoint when the page itself has the anchor, else the API's
    // pinned hit ("avtalslagen 36" -> §36); selecting it means Enter goes there
    select(0);
  }
  function go() {
    // navigate to the selected hit (the first by default == the resolved target)
    var hs = choices();
    if (!hs.length) return false;
    var local = hs[sel].getAttribute && hs[sel].getAttribute('data-local');
    if (local) { jump(local); close(); return true; }
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
    var local = localHits(q);
    render(local, [], null, q);          // instant, before the API answers
    if (andGo && local.length) { go(); return; }
    fetch('/api/v1/search?limit=8&q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) { if (mine === seq) { render(local, d.results || [], d.total || 0, q); if (andGo) go(); } })
      .catch(function () {
        // local hits (already painted) survive, but the outage must show:
        // silently degrading to same-page pinpoints would hide that corpus
        // search is down
        if (mine === seq && results) {
          refine.hidden = true;
          results.insertAdjacentHTML('beforeend',
            '<div class="search-note">Sökningen kunde inte nås.</div>');
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
    // a local hit is a same-page jump: scroll + flash instead of a hash
    // navigation, which would go to the first matching id in DOM order (an
    // imported pane can shadow the page's own anchor)
    results.addEventListener('click', function (e) {
      var a = e.target.closest('a[data-local]');
      if (a) { e.preventDefault(); jump(a.getAttribute('data-local')); close(); }
    });
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
