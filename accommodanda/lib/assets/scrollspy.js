/* A throttled scroll handler that (1) highlights the TOC entry for the
   section at the top of the viewport, and (2) drives the context rail, read
   from the JSON island the renderer emitted.

   The rail is a column of *entries*, one per context-bearing location, each
   absolutely positioned beside the location it belongs to. An entry that is not
   in focus shows a single summary line naming its largest kind of context and
   how much else it has ("Rättsfall (5) + 2 ytterligare", C4); the entry in
   focus expands, in place, into the full panel (C5). A location with no context
   has no entry, and a document with no context at all leaves the column empty --
   the column draws no surface of its own, so there is nothing there to read as a
   rail, and no "nothing here yet" placeholder either (C1).

   Only the focused panel's HTML is ever in the DOM: a large statute's island is
   megabytes, and mounting every panel at once would double the page's node
   count for content that is one line away anyway.

   Instantiable per reading surface: the page's own .gr-body at load, and each
   imported split-view pane (popover.js) gets its own instance over its own
   TOC/rail -- window.lagenScrollspy(root, island) returns a destroy function.
   The ⌘K search palette is a separate script (search.js). Plain DOM, no deps. */
(function () {
  function spy(root, island) {
    island = island || {};

    var toc = root.querySelector('nav.toc');
    var links = toc ? Array.prototype.slice.call(toc.querySelectorAll('a')) : [];
    // anchor targets resolved *within this root*: in a split view several
    // documents coexist in one DOM and their ids collide across panes
    var targets = links.map(function (a) {
      return root.querySelector(
        lagenDom.sel(decodeURIComponent(a.getAttribute('href').slice(1))));
    });

    // The TOC is a flat list whose nesting lives only in the lvlN class. Recover
    // each entry's parent (the nearest preceding entry at a shallower level, -1 for
    // a top-level entry) so the scrollspy can collapse the outline to just the
    // active section's ancestor path.
    var levels = links.map(function (a) {
      var m = a.className.match(/lvl(\d)/);
      return m ? +m[1] : 1;
    });
    var parents = (function () {
      var par = [], stack = [];      // stack[level] = last index seen at that level
      for (var i = 0; i < levels.length; i++) {
        var lv = levels[i];
        par[i] = -1;
        for (var p = lv - 1; p >= 1; p--) {
          if (stack[p] != null) { par[i] = stack[p]; break; }
        }
        stack[lv] = i;
        for (var d = lv + 1; d < stack.length; d++) stack[d] = null;  // deeper resets
      }
      return par;
    })();

    // Show top-level entries always, plus the active entry, its ancestors, and the
    // direct children of any node on that path -- every other branch is hidden.
    function collapse(active) {
      var expanded = {};             // nodes whose children should stay visible
      for (var i = active; i >= 0; i = parents[i]) expanded[i] = true;
      for (var j = 0; j < links.length; j++) {
        var show = parents[j] < 0 || expanded[parents[j]];
        links[j].classList.toggle('toc-collapsed', !show);
      }
    }

    /* ---------------- context rail ---------------- */

    var rail = root.querySelector('aside.rail');
    var entries = [], activeEntry = null, panelBox = null, panel = null;

    // The summary line for a collapsed entry, composed from the panel's own
    // sections: the first (highest-priority) one by name and size, then how many
    // other kinds wait behind it. Read from data-label/data-n rather than from
    // the rendered text, so the line cannot drift from the panel it stands for.
    function stubLine(html) {
      var probe = document.createElement('div');
      probe.innerHTML = html;
      var secs = probe.querySelectorAll('.rail-sec');
      if (!secs.length) return null;
      var line = document.createElement('button');
      line.type = 'button';
      line.className = 'rail-stub';
      var label = document.createElement('span');
      label.className = 'rail-stub-label';
      label.textContent = secs[0].getAttribute('data-label') || '';
      line.appendChild(label);
      var n = +secs[0].getAttribute('data-n') || 0;
      if (n > 1) {
        var count = document.createElement('span');
        count.className = 'rail-stub-n';
        count.textContent = '(' + n + ')';
        line.appendChild(count);
      }
      if (secs.length > 1) {
        var more = document.createElement('span');
        more.className = 'rail-stub-more';
        more.textContent = '+ ' + (secs.length - 1) + ' ytterligare';
        line.appendChild(more);
      }
      line.setAttribute('aria-label', 'Visa kontext för denna del');
      return line;
    }

    function addEntry(el, html) {
      var stub = stubLine(html);
      if (!stub) return;             // a panel with no sections says nothing
      var box = document.createElement('div');
      box.className = 'rail-entry';
      box.appendChild(stub);
      rail.appendChild(box);
      var entry = { el: el, box: box, html: html, top: 0 };
      stub.addEventListener('click', function (e) {
        e.preventDefault();
        setActive(entry);
        el.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      entries.push(entry);
    }

    if (rail) {
      rail.innerHTML = '';
      // one panel serves every entry: a large statute's island is megabytes, and
      // mounting a panel per location would cost more DOM than the statute
      panelBox = document.createElement('div');
      panelBox.className = 'rail-panelbox';
      panel = document.createElement('div');
      panel.className = 'rail-panel';
      panelBox.appendChild(panel);
      rail.appendChild(panelBox);
      // the document-level panel ('') belongs to the page's own head matter, so
      // it rides the frontmatter and is what shows before the first § scrolls up
      var front = root.querySelector('header.frontmatter');
      if (front && island['']) addEntry(front, island['']);
      Array.prototype.slice.call(root.querySelectorAll('[data-rail]'))
        .forEach(function (el) {
          // Every marked element gets its entry, container or not. This used to
          // skip any element with a marked descendant, to stop a § and the
          // stycke that starts on its own first line showing as two entries
          // stacked at the same height -- but the rail builder already prevents
          // that pair by *folding* them into one panel and leaving only one
          // marker behind (`Rail.add`). What the skip actually did was drop the
          // § itself wherever it had a second stycke or a numbered list, which
          // is nearly every § of any length: 49 of YGL's 191 panels, 277 of
          // brottsbalkens 995, and always the paragraf-level ones a citation is
          // most likely to name (1 kap. 4 § YGL is cited 557 times).
          var html = island[el.getAttribute('data-rail')];
          if (html) addEntry(el, html);
        });
    }

    // Absolute placement: each entry's line sits beside the location it
    // annotates. Both rects are read in the same frame, so their difference is
    // the offset inside the rail regardless of scroll position or which element
    // scrolls (a pane in the split view, the window otherwise).
    function place() {
      if (!rail || !entries.length) return;
      var base = rail.getBoundingClientRect().top;
      entries.forEach(function (e) {
        e.top = e.el.getBoundingClientRect().top - base;
        e.box.style.top = e.top + 'px';
      });
      if (activeEntry) panelBox.style.top = activeEntry.top + 'px';
    }

    // The open panel starts where its location does and runs to the foot of the
    // column, with the panel sticky inside it. Anchoring it to the location is
    // what makes the expansion read as the summary line growing (C5); giving it
    // the rest of the column is what keeps a tall panel readable -- bounded by
    // the provision alone, a panel taller than its § would slide straight back
    // off it, which is the whole reason the rail used to be one fixed panel.
    function setActive(entry) {
      if (entry === activeEntry) return;
      if (activeEntry) {
        activeEntry.box.classList.remove('rail-on');
        if (activeEntry.el) activeEntry.el.classList.remove('rail-active');
      }
      activeEntry = entry;
      panelBox.classList.toggle('rail-open', !!entry);
      panel.innerHTML = entry ? entry.html : '';
      if (entry) {
        panelBox.style.top = entry.top + 'px';
        // restart the expand animation: swapping innerHTML alone would not
        // re-run it, so the class is dropped and re-applied across a reflow
        panel.classList.remove('rail-grow');
        void panel.offsetWidth;
        panel.classList.add('rail-grow');
        entry.box.classList.add('rail-on');     // its own line steps aside
        if (entry.el) entry.el.classList.add('rail-active');
      }
    }

    var activeLink = -1, ticking = false;

    function update() {
      ticking = false;
      // the focus line, 120px below the top of this surface's scroll viewport
      // (the pane's scroll container in a split view, the window otherwise).
      // getBoundingClientRect().top is viewport-relative, so it is correct
      // regardless of a node's offsetParent -- a [data-rail] ancestor is
      // position:relative, which makes a nested node's offsetTop reset
      // per-section (the "rail stuck on the section's last paragraf" bug once
      // chapter sections carry commentary).
      var sc = root.closest('.pane-scroll');
      var LINE = (sc ? sc.getBoundingClientRect().top : 0) + 120;
      if (links.length) {
        var idx = 0;
        for (var i = 0; i < targets.length; i++) {
          if (targets[i] && targets[i].getBoundingClientRect().top <= LINE) idx = i;
        }
        if (idx !== activeLink) {
          if (links[activeLink]) links[activeLink].classList.remove('active');
          activeLink = idx;
          var a = links[idx];
          if (a) {
            a.classList.add('active');
            collapse(idx);          // open only this section's branch (offsets after)
            if (a.offsetTop < toc.scrollTop ||
                a.offsetTop > toc.scrollTop + toc.clientHeight - 30) {
              toc.scrollTop = a.offsetTop - toc.clientHeight / 2;
            }
          }
        }
      }
      if (entries.length) {
        var best = entries[0];
        for (var j = 0; j < entries.length; j++) {
          if (entries[j].el.getBoundingClientRect().top <= LINE) best = entries[j];
        }
        setActive(best);
      }
    }
    function onScroll() {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }
    // capture-phase on document, not window: in a split view the document
    // scrolls inside a pane element, whose scroll events don't bubble --
    // capture sees both those and normal window scrolling, and the tracking
    // logic is viewport-relative either way
    document.addEventListener('scroll', onScroll, { passive: true, capture: true });
    // the reading column's height moves with the viewport, with late-arriving
    // web fonts, and whenever a <details> in the text opens -- each invalidates
    // every entry's offset, so re-place rather than trust the first measurement
    var main = root.querySelector('main.gr-main');
    var ro = window.ResizeObserver && main ? new ResizeObserver(place) : null;
    if (ro) ro.observe(main);
    window.addEventListener('resize', place);
    place();
    update();
    return function destroy() {
      document.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', place);
      if (ro) ro.disconnect();
    };
  }

  window.lagenScrollspy = spy;

  // one rail section open at a time: opening an accordion row closes its
  // sibling rows in the same panel. Scoped to siblings, so the nested "+N
  // fler" disclosures inside a section are untouched and split-view panes
  // stay independent of each other. `toggle` does not bubble -- capture.
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d.matches || !d.matches('details.rail-sec') || !d.open) return;
    Array.prototype.forEach.call(d.parentElement.children, function (sib) {
      if (sib !== d && sib.matches('details.rail-sec[open]')) sib.open = false;
    });
  }, true);

  var body = document.querySelector('.gr-body');
  if (body) spy(body, lagenDom.island(document));
})();
