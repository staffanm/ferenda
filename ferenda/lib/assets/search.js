/* The ⌘K command palette -- live full-text search against the REST API, led
   by instant *local* hits: a terse pinpoint typed on a document page ("4",
   "4 §", "11:2", "4:", "art 5", "(42", "skäl 42", "bilaga III") resolves
   against the anchors of the page itself, no network, and shows the target's
   own text so the reader knows where Enter will land; a word query is looked
   for in the page's own text as well, and the places it occurs lead the list
   the same way. Its own script: the
   search UI is unrelated to the TOC scrollspy and is global to every page,
   so it does not ride along in scrollspy.js. The API is always same-origin
   (the site and the API are served by one process, lagen serve), so requests
   are relative ('/api/v1/...') -- never a baked absolute base, which can only
   go stale and point a cached page at the wrong/dead port. Debounced; renders
   the top hits as links to each document's matching paragraph. */
(function () {
  var overlay = null, results = null, refine = null, timer = null, seq = 0, sel = 0;
  var spin = null, spinTimer = null, slowTimer = null, moreOnPage = false;

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

  /* -- on-page full text: the words of THIS document, answered as citable
     places. A word query typed while reading an act is usually about the act in
     front of the reader, and the corpus index answers it badly: it ranks the
     document as a whole and links to the top of it, and an EU act's recitals are
     not indexed as fragments at all. "senaste utvecklingen" on the GDPR page
     means skäl 83, artikel 25.1 and artikel 32.1 -- three places a reader could
     otherwise reach only with the browser's own find, which names none of them.
     Phrase match, like that find: the query is looked for as typed. */

  var MAX_TEXT = 3;        // on-page text hits shown before the corpus answer
  var SNIP = 60;           // characters of context on each side of the match
  var pageText = null;     // the page's text index, rebuilt on each palette open

  // An anchor id -> the pinpoint a reader recognises, in the palette's own
  // forms: the inverse of `candidates`, which mints the same labels from a
  // query. It reads the anchor grammars lib/pinpoint.py reads, but it is NOT
  // that function ported -- a hit row heads its own line, so the label is
  // capitalised ("Skäl 83" where Python writes "skäl 83"); a tail that only
  // disambiguates is dropped from the label though the jump keeps it in the id
  // ("25.1.S1" -> "Artikel 25.1", "A5-2" -> "Artikel 5"); and an annex
  // ("bilaga-3") has a form here and none there. '' for an id with no reader
  // form -- page furniture, an editorial section slug -- which is what keeps an
  // on-page hit anchored to a place the reader can cite.
  var SEG = { K: 'kap.', P: '§', O: 'mom.', S: 'st', N: 'p', M: 'men.' };
  function anchorLabel(id) {
    var m;
    if ((m = id.match(/^recital-(\d+)$/))) return 'Skäl ' + m[1];
    if ((m = id.match(/^bilaga-(\d+)$/))) return 'Bilaga ' + m[1];
    if ((m = id.match(/^sid(\d+)$/))) return 's. ' + m[1];
    // an EU article anchor is all digits and dots; a trailing ".S<n>" is the
    // stycke the words sit in, which the jump keeps and the label leaves off
    if ((m = id.match(/^(\d+[a-z]?(?:\.\d+)*)(?:\.([a-z]))?(?:\.S\d+)?$/)))
      return 'Artikel ' + m[1] + (m[2] ? ' ' + m[2] : '');
    // the CoE treaty grammar: "A6" -> "Artikel 6", "A6P1" -> "Artikel 6
    // punkt 1", "A3Lh" -> "Artikel 3 led h". A treaty anchors every
    // provision this way and nothing else in the corpus does, so without
    // this branch the folkrätt pages answered no on-page word search at
    // all -- every one of EKMR's 201 anchors landed here and got ''.
    if ((m = id.match(
        /^A(\d+[A-Za-z]?|[IVXLCDM]+)(?:\.(\d+))?(?:-\d+)?(?:P(\d+)(?:-\d+)?)?(?:L([a-z])(?:-\d+)?)?$/)))
      return 'Artikel ' + m[1] + (m[2] ? '.' + m[2] : '') +
        (m[3] ? ' punkt ' + m[3] : '') + (m[4] ? ' led ' + m[4] : '');
    var segs = id.match(/[KPOSNM][0-9a-zåäö]+/g);
    if (segs && segs.join('') === id) {
      return segs.map(function (seg) {
        // an anchor writes an inserted paragraf's letter tight against its
        // number ("P52u"); a citation writes it apart ("52 u §")
        return seg.slice(1).replace(/^(\d+)([a-zåäö]+)$/, '$1 $2') +
          ' ' + SEG[seg[0]];
      }).join(' ');
    }
    return '';
  }

  // the page's own body text as one whitespace-normalised string, with the text
  // node each offset falls in. Built once per palette open, so a query costs one
  // indexOf over it rather than a walk of a 3 MB document per keystroke.
  function textIndex() {
    if (pageText) return pageText;
    var root = document.querySelector('.gr-main');
    var walk = root && document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var nodes = [], starts = [], text = '', block = null, prev = null, n;
    while (walk && (n = walk.nextNode())) {
      var p = n.parentElement;
      // the exclusions ownEl makes, for the same reason: an imported pane or a
      // popover is another document's text, not this page's
      if (!p || p.closest('script, style, [data-pane], .search-overlay, ' +
                          '.lagen-popover')) continue;
      // the permalink pilcrow and the comment marker are affordances, not
      // text -- the same two localHits keeps out of a pinpoint's snippet
      var v = n.nodeValue.replace(/[💬¶]/g, '').replace(/\s+/g, ' ');
      if (!v) continue;
      // Inside a block the markup's own whitespace separates the words, with
      // doubles collapsed -- so a phrase still matches across an inline link
      // ("senaste <a>utvecklingen</a>"), and a comma after one is not pushed
      // off it ("artikel 32 ,"). Between blocks the markup carries no
      // whitespace at all ("…24.3.</p><h3>Artikel 25"), so one space goes in.
      block = p.closest('p, li, h1, h2, h3, h4, h5, td, th, blockquote, div');
      if (text) {
        if (/ $/.test(text)) v = v.replace(/^ /, '');
        else if (block !== prev && !/^ /.test(v)) v = ' ' + v;
      }
      prev = block;
      nodes.push(n); starts.push(text.length); text += v;
    }
    pageText = { nodes: nodes, starts: starts, text: text,
                 low: text.toLowerCase() };
    return pageText;
  }

  function nodeAt(idx, off) {
    var lo = 0, hi = idx.starts.length - 1, best = 0;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (idx.starts[mid] <= off) { best = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return idx.nodes[best];
  }

  // the citable place a match sits in -- the nearest ancestor whose id the
  // palette can both name and jump to
  function anchorOf(node) {
    for (var el = node.parentElement; el; el = el.parentElement) {
      if (el.id && anchorLabel(el.id) && lagenDom.ownEl(el.id) === el)
        return el.id;
      if (el.classList.contains('gr-main')) break;
    }
    return null;
  }

  function snippet(idx, at, len) {
    var s = Math.max(0, at - SNIP), e = Math.min(idx.text.length, at + len + SNIP);
    return (s ? '…' : '') + esc(idx.text.slice(s, at)) +
      '<em>' + esc(idx.text.slice(at, at + len)) + '</em>' +
      esc(idx.text.slice(at + len, e)) + (e < idx.text.length ? '…' : '');
  }

  // Up to MAX_TEXT + 1 places on this page where `q` occurs, in document order,
  // one per anchor and none already offered as a pinpoint hit (`taken`). The
  // extra one is not shown: it is how `run` knows to say there are more, so a
  // capped list never reads as the whole answer.
  function textHits(q, taken) {
    var needle = q.trim().toLowerCase().replace(/\s+/g, ' ');
    var idx = textIndex(), out = [], seen = {}, at = 0, i;
    while (out.length <= MAX_TEXT && (i = idx.low.indexOf(needle, at)) >= 0) {
      at = i + needle.length;
      var id = anchorOf(nodeAt(idx, i));
      if (!id || seen[id] || taken[id]) continue;
      seen[id] = true;
      out.push({ id: id, label: anchorLabel(id),
                 snipHtml: snippet(idx, i, needle.length) });
    }
    return out;
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

  // roll a count from its previous value to `to` odometer-style over `ms` (S2),
  // so changing the query (avta -> avtal) tweens 46 690 -> 45 358 rather than
  // hard-swapping. Honours prefers-reduced-motion (and equal values) by jumping.
  var NUM = new Intl.NumberFormat('sv-SE');
  function rollNumber(el, from, to, ms) {
    if (from === to ||
        (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      el.textContent = NUM.format(to);
      return;
    }
    var start = null;
    // paint the start value now, then tween from the next animation frame --
    // calling frame() synchronously would pass ts === undefined, making p NaN,
    // and `NaN < 1` is false, so the odometer would stick on "NaN" forever
    el.textContent = NUM.format(from);
    requestAnimationFrame(function frame(ts) {
      if (!el.isConnected) return;             // superseded by a newer query
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / ms);
      var eased = 1 - Math.pow(1 - p, 3);      // easeOutCubic
      el.textContent = NUM.format(Math.round(from + (to - from) * eased));
      if (p < 1) requestAnimationFrame(frame);
    });
  }

  // `total` is null while the API round-trip is pending: local hits paint
  // instantly, the emptiness verdict ("Inga träffar") waits for the API
  function render(local, items, total, q) {
    if (!results) return;
    var docTitle = document.querySelector('.gr-main h1');
    var localHtml = local.map(function (h) {
      // a pinpoint hit shows the target's own opening words (escaped); a text
      // hit shows the match in its context, with the match itself marked
      var snip = h.snipHtml || (h.snip ? esc(h.snip) : '');
      return '<a class="search-hit local" data-local="' + esc(h.id) + '" ' +
        'href="#' + esc(h.id) + '">' +
        '<span class="hit-title">' + esc(h.label) +
        '<span class="hit-here">på denna sida</span></span>' +
        (docTitle ? '<span class="hit-sub">' + esc(docTitle.textContent) + '</span>' : '') +
        (snip ? '<span class="hit-snip">' + snip + '</span>' : '') + '</a>';
    }).join('');
    // the on-page list is capped at MAX_TEXT so the corpus answer is never
    // pushed off the palette -- say so, or the cap reads as "that is all there
    // is on this page"
    if (moreOnPage && local.length)
      localHtml += '<div class="search-note search-more">Fler träffar på ' +
        'denna sida än de ' + MAX_TEXT + ' som visas.</div>';
    if (!local.length && !items.length) {
      refine.hidden = true;
      results.innerHTML = total === null ? '' :
        '<div class="search-note">Inga träffar för ”' + esc(q) + '”.</div>';
      return;
    }
    var hitHtml = items.map(function (r) {
      // shaped by lagenDom.hitFields, shared with /sok/ so the two result lists
      // cannot describe the same hit differently (Q4): the heading is the page
      // title, the sub is the citation id where it adds anything over that
      // heading, else what sort of document it is, and a fragment hit
      // deep-links to its paragraph anchor (the node id == pinpoint)
      var h = lagenDom.hitFields(r);
      return '<a class="search-hit" href="' + esc(h.target) + '">' +
        '<span class="hit-title">' + esc(h.title) + '</span>' +
        (h.sub ? '<span class="hit-sub">' + esc(h.sub) + '</span>' : '') +
        (h.snippet ? '<span class="hit-snip">' + h.snippet + '</span>' : '') + '</a>';
    }).join('');
    if (total === null || !total) {
      refine.hidden = true;
      refine._count = 0;              // next appearance counts up from zero
    } else {
      refine.href = '/sok/?q=' + encodeURIComponent(q);
      refine.innerHTML = 'Avgränsa <span class="refine-count"></span> träffar';
      rollNumber(refine.querySelector('.refine-count'), refine._count || 0, total, 150);
      refine._count = total;
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
  /* -- in-flight feedback: the first search after prod has sat idle can take
     10+ s (OpenSearch reads a 31 GB index back off a slow disk into a page
     cache it doesn't fit in). A ring at the input's right edge after 400 ms
     says work is happening; a note after 2 s says it is slower than usual, so
     the reader waits instead of concluding the dialog is broken. The 400 ms
     grace keeps the warm path (~100 ms) spinner-free. */
  function waitOn(mine) {
    spinTimer = setTimeout(function () {
      if (mine === seq && spin) spin.hidden = false;
    }, 400);
    slowTimer = setTimeout(function () {
      if (mine === seq && results)
        results.insertAdjacentHTML('beforeend', '<div class="search-note ' +
          'search-slow">Det här tar längre tid än vanligt – ett ögonblick …</div>');
    }, 2000);
  }
  function waitOff() {
    clearTimeout(spinTimer); clearTimeout(slowTimer);
    if (spin) spin.hidden = true;
    // both transient notes go: the too-short one must not survive into the
    // query that clears the floor, where `loading` keeps the old innerHTML and
    // only dims it -- the reader would read "skriv minst 3 tecken" over a
    // three-character search in flight
    if (results) {
      Array.prototype.forEach.call(
        results.querySelectorAll('.search-slow, .search-too-short'),
        function (note) { note.remove(); });
    }
  }
  function run(q, andGo) {
    var mine = ++seq;
    waitOff();
    moreOnPage = false;
    if (!q.trim()) {
      if (results) results.innerHTML = '';
      if (refine) refine.hidden = true;
      return;
    }
    var local = localHits(q);
    // Too short to ask the corpus as you type (lagenDom.tooShort). `andGo` is
    // Enter -- an explicit search, which goes through at any length, so "EU"
    // and "JO" are still askable. The page's own pinpoints answer either way:
    // they are a local scan, not an index query. The note says both why the
    // rest of the list is empty and how to search anyway, so a reader two
    // letters in reads neither "nothing found" nor "refused".
    if (!andGo && lagenDom.tooShort(q)) {
      if (results) results.classList.remove('loading');
      render(local, [], null, q);       // also hides the refine link (total null)
      // The note goes out even when local pinpoints just painted: skipping the
      // index to save cost degrades the answer exactly the way skipping it for
      // an outage does (see the catch handler below), and a list of same-page
      // hits with no note reads as "this is the whole answer".
      // Enter is only an escape hatch when no local hit is selected: with one
      // painted, `go()` jumps there and `run(q, true)` never fires, so naming
      // Enter would promise something this state cannot do. Then the way on is
      // the third character, which the first sentence already says.
      if (results) {
        results.insertAdjacentHTML('beforeend',
          '<div class="search-note search-too-short">' +
          lagenDom.tooShortNote(local.length ? '' : 'Tryck Enter') + '</div>');
      }
      return;
    }
    // The page's own text answers the same query the corpus does, under the same
    // floor: it is a local scan, but a two-letter substring matches everywhere
    // and would fill the palette with noise. A place already offered as a
    // pinpoint hit is not offered twice.
    var taken = {};
    local.forEach(function (h) { taken[h.id] = true; });
    var onPage = textHits(q, taken);
    moreOnPage = onPage.length > MAX_TEXT;
    local = local.concat(onPage.slice(0, MAX_TEXT));
    // Paint local pinpoints instantly when the query has any; otherwise KEEP the
    // previous query's API hits on screen (dimmed) until the new ones arrive --
    // wiping to empty on every keystroke makes the whole list flash away and
    // reappear ~a search later ("arbets" -> blank -> "arbetsm").
    if (local.length) render(local, [], null, q);
    else if (results) results.classList.add('loading');
    if (andGo && local.length) { go(); return; }
    waitOn(mine);
    fetch('/api/v1/search?limit=8&q=' + encodeURIComponent(q))
      // a 503 (search cluster down) carries a JSON body, so r.json() alone
      // would read it as 0 hits -- an outage rendered as "Inga träffar"
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { if (mine === seq && results) { waitOff(); results.classList.remove('loading'); render(local, d.results || [], d.total || 0, q); if (andGo) go(); } })
      .catch(function () {
        // local hits (already painted) survive, but the outage must show:
        // silently degrading to same-page pinpoints would hide that corpus
        // search is down
        if (mine === seq && results) {
          waitOff();
          results.classList.remove('loading');
          refine.hidden = true;
          results.insertAdjacentHTML('beforeend',
            '<div class="search-note">Sökningen kunde inte nås.</div>');
        }
      });
  }
  function open() {
    if (overlay) return;
    // the page may have changed since the last open (a split-view pane, a
    // lydelse switch), so the text index is built fresh for this session
    pageText = null;
    overlay = document.createElement('div');
    overlay.className = 'search-overlay';
    // the refine link sits to the *right* of the input (S2)
    overlay.innerHTML = '<div class="search-box"><div class="search-input-row">' +
      '<input autofocus placeholder="Sök lag, paragraf, rättsfall…">' +
      '<span class="search-spin" hidden></span>' +
      '<a class="search-refine" href="/sok/" hidden></a></div>' +
      '<div class="search-results"></div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    var input = overlay.querySelector('input');
    spin = overlay.querySelector('.search-spin');
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
    if (!overlay) return;
    waitOff();
    seq++;              // retire any in-flight fetch: a response landing after
                        // a close+reopen must not paint the old query's hits
    overlay.remove();
    overlay = null; results = null; refine = null; spin = null;
  }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); }
    if (e.key === 'Escape') close();
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-search]')) { e.preventDefault(); open(); }
  });
  // The shortcut hint is server-rendered as the Mac glyph (⌘K); rewrite it to
  // "Ctrl K" off the Mac so the label matches the accelerator that actually
  // works there (Ctrl+K). (S1)
  var isMac = /Mac|iPhone|iPad|iPod/.test((navigator.userAgentData
      && navigator.userAgentData.platform) || navigator.platform || '');
  if (!isMac) {
    Array.prototype.forEach.call(document.querySelectorAll('.masthead .search .k'),
      function (el) { el.textContent = 'Ctrl K'; });
  }
})();
