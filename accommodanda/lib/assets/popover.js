/* Hover previews and the split reading view.

   Every internal link in the reading column and the context rail -- a lagrum
   reference, a defined term, a författningskommentar pointer -- gets a hover
   popover showing the target's actual rendered text (the replacement for the
   old title-attribute tooltip: richer, styled, and it can hold an action).
   The pages are fully static, so a preview is just the target page fetched
   same-origin and the fragment's element lifted out; a same-page target is
   read straight from the DOM, no network.

   The popover's ↗ escalates to a split view: the target document opens in its
   own pane above the current one, each pane with its own scrollbar and a slim
   chrome bar (title, move up/down, close) plus draggable dividers. Imported
   pane content is marked [data-pane] so search.js's local quick-jump and this
   script's own lookups can keep resolving anchors against the page's *own*
   document, whose ids an imported document may duplicate. */
(function () {
  var pop = null, showTimer = null, hideTimer = null, anchorA = null;
  var stack = null;                    // the split-view pane container, when active
  var docs = {};                       // pathname -> Promise<Document> (fetched pages)

  /* ---------------- target resolution ---------------- */

  // (path, frag) for an eligible content link, else null. A bare-#hash link
  // points into the document of the pane it sits in (imported panes carry
  // another document); everything else must be a same-origin page path.
  function targetOf(a) {
    var raw = a.getAttribute('href');
    if (!raw || raw === '#') return null;
    var paneDoc = a.closest('[data-pane]');
    if (raw.charAt(0) === '#')
      return { path: paneDoc ? paneDoc.getAttribute('data-pane') : location.pathname,
               frag: decodeURIComponent(raw.slice(1)) };
    var url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return null;
    return { path: url.pathname,
             frag: decodeURIComponent(url.hash.replace(/^#/, '')) };
  }

  // index / aggregation views (frontpage, browse listings, search) are the
  // solo-column pages; a reader following a link from a list already knows
  // where they are going, so previews add only noise there (L1). Document
  // pages -- and imported panes, which are always documents -- keep them.
  var indexPage = !!document.querySelector('.gr-body.solo');

  function eligible(a) {
    if (indexPage && !a.closest('[data-pane]')) return false;
    if (!a.closest('.gr-main, .rail, [data-pane]')) return false;
    if (a.closest('nav.toc, .search-overlay, .lagen-popover, .pane-bar')) return false;
    if (a.classList.contains('pilcrow') || a.classList.contains('ext') ||
        a.closest('sup.fnref') || /^#fn/.test(a.getAttribute('href') || '')) return false;
    return !!targetOf(a);
  }

  function getDoc(path) {
    if (path === location.pathname) return Promise.resolve(document);
    if (!docs[path])
      docs[path] = fetch(path).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      }).then(function (t) {
        return new DOMParser().parseFromString(t, 'text/html');
      });
    return docs[path];
  }

  // the anchor's element within `doc` -- for the live document, never one
  // inside an imported pane (another document's ids)
  function fragEl(doc, frag) {
    // a whole-document preview leads straight into the text: the title is
    // already in the popover head, so the body shows the opening prose, not
    // the frontmatter's dl.meta block (L2)
    if (!frag) return documentLede(doc) || ownMain(doc);
    return doc === document ? lagenDom.ownEl(frag)
                            : doc.querySelector(lagenDom.sel(frag));
  }

  // the first couple of real prose paragraphs of a document's reading column,
  // skipping the frontmatter (eyebrow/title/subtitle/dl.meta/source link) and
  // any short label lines, as a synthesized excerpt element -- or null if the
  // document has no prose (a stub concept, a bare listing)
  function documentLede(doc) {
    var main = ownMain(doc);
    var ps = main.querySelectorAll('p');
    var wrap = doc.createElement('div');
    // the frontmatter and the pre-text furniture (banners, förarbeten list,
    // version compare, the inbound-citation panel) are not the document's prose
    var SKIP = 'header.frontmatter, .forarbeten, .inbound-doc, details, ' +
      '.version-banner, .expired-banner, .diff-note';
    for (var i = 0; i < ps.length && wrap.childNodes.length < 2; i++) {
      if (ps[i].closest(SKIP)) continue;
      if (ps[i].textContent.trim().length < 20) continue;
      wrap.appendChild(ps[i].cloneNode(true));
    }
    return wrap.childNodes.length ? wrap : null;
  }

  // the document's *own* reading column / grid -- in the live document,
  // imported panes contribute main.gr-main and .gr-body elements of their own
  // (and sit earlier in DOM order), so the one outside every [data-pane] is
  // the page's. Every rendered page has both (render.page); a page without
  // them cannot host previews or panes, so this fails loudly rather than
  // handing back another document's column.
  function ownMain(doc) {
    var ms = doc.querySelectorAll('main.gr-main');
    for (var i = 0; i < ms.length; i++)
      if (!ms[i].closest('[data-pane]')) return ms[i];
  }

  function ownBody(doc) {
    var bs = doc.querySelectorAll('.gr-body');
    for (var i = 0; i < bs.length; i++)
      if (!bs[i].closest('[data-pane]')) return bs[i];
  }

  function docTitle(doc) {
    var h1 = ownMain(doc).querySelector('h1');
    return h1 ? h1.textContent.trim() : doc.title;
  }

  function fragLabel(el) {
    var n = el.querySelector('.paragraf-gutter .n, h2, h3, h4');
    return n ? n.textContent.trim() : (el.id || '');
  }

  // a display clone of the target: live-page furniture (scrollspy's 💬 dots,
  // permalink pilcrows) stripped, ids dropped so the clone can never shadow
  // the page's own anchors
  function excerpt(el) {
    var c = el.cloneNode(true);
    var junk = c.querySelectorAll('.rail-dot, .pilcrow');
    for (var i = 0; i < junk.length; i++) junk[i].remove();
    var ids = c.querySelectorAll('[id]');
    for (var j = 0; j < ids.length; j++) ids[j].removeAttribute('id');
    c.removeAttribute('id');
    return c;
  }

  /* ---------------- popover ---------------- */

  function hidePop() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    if (pop) { pop.remove(); pop = null; anchorA = null; }
  }
  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hidePop, 300);
  }

  function position(a) {
    var r = a.getBoundingClientRect();
    var w = Math.min(560, window.innerWidth - 24);
    var left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12));
    pop.style.width = w + 'px';
    pop.style.left = left + 'px';
    var h = pop.offsetHeight;
    if (r.bottom + 8 + h <= window.innerHeight - 8 || r.top < h + 16)
      pop.style.top = Math.min(r.bottom + 8, window.innerHeight - h - 8) + 'px';
    else
      pop.style.top = (r.top - h - 8) + 'px';
  }

  function show(a) {
    var t = targetOf(a);
    if (!t) return;
    hidePop();
    anchorA = a;
    pop = document.createElement('div');
    pop.className = 'lagen-popover';
    pop.innerHTML = '<div class="pop-head"><a class="pop-title"></a>' +
      '<button type="button" class="pop-expand" ' +
      'title="Öppna i delad vy" aria-label="Öppna i delad vy">↗</button></div>' +
      '<div class="pop-body"><div class="pop-loading">Hämtar …</div></div>';
    document.body.appendChild(pop);
    pop.addEventListener('mouseenter', function () { clearTimeout(hideTimer); });
    pop.addEventListener('mouseleave', scheduleHide);
    pop.querySelector('.pop-expand').addEventListener('click', function () {
      hidePop();
      expand(t.path, t.frag);
    });
    var title = pop.querySelector('.pop-title');
    title.href = t.path + (t.frag ? '#' + t.frag : '');
    title.textContent = a.textContent.trim();
    position(a);
    // two-arg then, not .catch: the error handler covers the fetch only. A
    // bug in the assembly below must surface as an uncaught rejection, not
    // masquerade as a network problem (rule:narrow-what-you-catch).
    getDoc(t.path).then(function (doc) {
      if (!pop || anchorA !== a) return;
      var el = fragEl(doc, t.frag);
      var label = el && t.frag ? fragLabel(el) : '';
      title.textContent = (label ? label + ' · ' : '') + docTitle(doc);
      var body = pop.querySelector('.pop-body');
      body.innerHTML = '';
      if (el) body.appendChild(excerpt(el));
      else body.innerHTML = '<div class="pop-loading">Målet hittades inte i dokumentet.</div>';
      position(a);
    }, function () {
      if (!pop || anchorA !== a) return;
      pop.querySelector('.pop-body').innerHTML =
        '<div class="pop-loading">Förhandsvisningen kunde inte hämtas.</div>';
    });
  }

  document.addEventListener('mouseover', function (e) {
    if (!e.target.closest) return;
    var a = e.target.closest('a[href]');
    if (pop && (pop.contains(e.target) || a === anchorA)) { clearTimeout(hideTimer); return; }
    if (a && eligible(a)) {
      clearTimeout(showTimer);
      showTimer = setTimeout(function () { show(a); }, 250);
    } else if (pop) {
      scheduleHide();
    } else {
      clearTimeout(showTimer);
    }
  });
  document.addEventListener('focusin', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (a && eligible(a)) show(a);
    else if (pop && !pop.contains(e.target)) hidePop();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hidePop();
  });
  // any real scroll makes the fixed-position popover point at nothing;
  // scrolling *inside* the popover body is the one exception
  document.addEventListener('scroll', function (e) {
    if (pop && !pop.contains(e.target)) hidePop();
  }, true);

  /* ---------------- split view ---------------- */

  function panes() {
    return stack ? Array.prototype.slice.call(stack.querySelectorAll('.pane')) : [];
  }

  function paneBar(titleText, href, closable) {
    var bar = document.createElement('div');
    bar.className = 'pane-bar';
    bar.innerHTML = '<a class="pane-title"></a><span class="pane-actions">' +
      '<button type="button" data-move="-1" title="Flytta upp" aria-label="Flytta upp">↑</button>' +
      '<button type="button" data-move="1" title="Flytta ned" aria-label="Flytta ned">↓</button>' +
      (closable ? '<button type="button" data-close title="Stäng" aria-label="Stäng">×</button>' : '') +
      '</span>';
    var t = bar.querySelector('.pane-title');
    t.textContent = titleText;
    t.href = href;
    return bar;
  }

  function relayout() {
    var ds = stack.querySelectorAll('.pane-divider');
    for (var i = 0; i < ds.length; i++) ds[i].remove();
    var ps = panes();
    for (var j = 1; j < ps.length; j++) {
      var d = document.createElement('div');
      d.className = 'pane-divider';
      d.addEventListener('pointerdown', startResize);
      stack.insertBefore(d, ps[j]);
    }
  }

  function startResize(e) {
    e.preventDefault();
    var d = e.target, prev = d.previousElementSibling, next = d.nextElementSibling;
    var y0 = e.clientY, h0 = prev.getBoundingClientRect().height;
    var max = h0 + next.getBoundingClientRect().height - 80;
    d.setPointerCapture(e.pointerId);
    function move(ev) {
      var h = Math.max(80, Math.min(max, h0 + ev.clientY - y0));
      prev.style.flex = '0 0 ' + h + 'px';
      next.style.flex = '1 1 0';
    }
    function up() {
      d.removeEventListener('pointermove', move);
      d.removeEventListener('pointerup', up);
    }
    d.addEventListener('pointermove', move);
    d.addEventListener('pointerup', up);
  }

  function ensureStack() {
    if (stack) return;
    var gb = document.querySelector('.gr-body');
    stack = document.createElement('div');
    stack.className = 'pane-stack';
    document.body.insertBefore(stack, gb);
    var main = document.createElement('section');
    main.className = 'pane pane-main';
    main.appendChild(paneBar(docTitle(document), location.pathname, false));
    var scroll = document.createElement('div');
    scroll.className = 'pane-scroll';
    scroll.appendChild(gb);
    main.appendChild(scroll);
    stack.appendChild(main);
    document.body.classList.add('split');
    // while split, a hash link must scroll its *own* pane: native hash
    // navigation goes to the first matching id in DOM order, which an
    // imported document above can shadow
    stack.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a) return;
      var pane = a.closest('.pane');
      var frag = decodeURIComponent(a.getAttribute('href').slice(1));
      var el = pane.querySelector(lagenDom.sel(frag));
      if (el) { e.preventDefault(); lagenDom.flash(el); }
    });
  }

  function unsplitIfAlone() {
    var ps = panes();
    if (ps.length !== 1 || !ps[0].classList.contains('pane-main')) return;
    var gb = ps[0].querySelector('.pane-scroll > .gr-body');
    document.body.insertBefore(gb, stack);
    stack.remove();
    stack = null;
    document.body.classList.remove('split');
  }

  function expand(path, frag) {
    // two-arg then, as in show(): the error handler covers the fetch only
    getDoc(path).then(function (doc) {
      ensureStack();
      var pane = document.createElement('section');
      pane.className = 'pane';
      pane.appendChild(paneBar(docTitle(doc), path + (frag ? '#' + frag : ''), true));
      var scroll = document.createElement('div');
      scroll.className = 'pane-scroll';
      // the whole reading grid rides along -- TOC, reading column *and*
      // context rail -- so the pane is a full surface with its own scrollspy.
      // Strip what must not duplicate: the rail's global id and the live
      // page's scrollspy furniture (a same-document import carries 💬 dots;
      // this pane's own spy instance rebuilds them bound to its own rail)
      var body = document.importNode(ownBody(doc), true);
      body.setAttribute('data-pane', path);
      var junk = body.querySelectorAll('.rail-dot');
      for (var i = 0; i < junk.length; i++) junk[i].remove();
      var rail = body.querySelector('aside.rail');
      if (rail) rail.removeAttribute('id');
      scroll.appendChild(body);
      pane.appendChild(scroll);
      stack.insertBefore(pane, stack.firstChild);   // new material reads on top
      relayout();
      var destroySpy = window.lagenScrollspy(body, lagenDom.island(doc));
      pane.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('.pane-bar button');
        if (!b) return;
        if (b.hasAttribute('data-close')) {
          destroySpy(); pane.remove(); relayout(); unsplitIfAlone();
        } else if (b.hasAttribute('data-move')) {
          var dir = +b.getAttribute('data-move'), ps = panes(),
              i2 = ps.indexOf(pane) + dir;
          if (i2 >= 0 && i2 < ps.length) {
            stack.insertBefore(pane, dir > 0 ? ps[i2].nextSibling : ps[i2]);
            relayout();
          }
        }
      });
      if (frag) {
        var el = body.querySelector(lagenDom.sel(frag));
        if (el) lagenDom.flash(el);
      }
    }, function () {
      // surfaced where the user acted: the popover is gone, so a transient
      // note beats a silently-dead ↗
      var note = document.createElement('div');
      note.className = 'pane-error';
      note.textContent = 'Dokumentet kunde inte hämtas.';
      document.body.appendChild(note);
      setTimeout(function () { note.remove(); }, 3000);
    });
  }

  // the main pane's own bar (move/close for symmetry with imported panes:
  // moving the *other* pane is equivalent, so it only carries move buttons)
  document.addEventListener('click', function (e) {
    if (!stack) return;
    var b = e.target.closest && e.target.closest('.pane-main .pane-bar button');
    if (!b || !b.hasAttribute('data-move')) return;
    var pane = b.closest('.pane'), dir = +b.getAttribute('data-move'),
        ps = panes(), i2 = ps.indexOf(pane) + dir;
    if (i2 >= 0 && i2 < ps.length) {
      stack.insertBefore(pane, dir > 0 ? ps[i2].nextSibling : ps[i2]);
      relayout();
    }
  });
})();
