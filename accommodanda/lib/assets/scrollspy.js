/* A throttled scroll handler that (1) highlights the TOC entry for the
   section at the top of the viewport, and (2) swaps the context rail to the
   active paragraph's panel, read from the JSON island the renderer emitted.
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
    var rail = root.querySelector('aside.rail');
    var marks = Array.prototype.slice.call(root.querySelectorAll('[data-rail]'));
    var EMPTY = '<div class="rail-empty">Ingen rättspraxis, förarbeten eller annan ' +
                'kontext har ännu knutits till denna del.</div>';
    // the document-level panel (commentary on the statute as a whole), keyed '' --
    // shown when no single paragraph is in focus (at the top of the document)
    var DEFAULT = island[''] || EMPTY;
    if (rail) rail.innerHTML = DEFAULT;

    var activeLink = -1, activeRail = '', activeMark = null, ticking = false;

    // swap the rail to a unit's panel and mark it active (idempotent per unit)
    function applyRail(best) {
      if (best === activeMark) return;
      var key = best ? best.getAttribute('data-rail') : '';
      activeRail = key;
      if (rail) rail.innerHTML = (key && island[key]) ? island[key] : DEFAULT;
      if (activeMark) activeMark.classList.remove('rail-active');
      activeMark = best;
      if (best) best.classList.add('rail-active');
    }

    // a clickable 💬 in the right gutter of every context-bearing unit -- a
    // discoverable affordance that pulls that unit's panel into the rail and
    // brings the unit into focus. Built here (not in the artifact) so it is global
    // across every source without touching the per-source renderers.
    if (rail) marks.forEach(function (el) {
      // skip a container whose own context-bearing descendant carries the marker,
      // so nested units (SFS paragraf > stycke) show one dot, not two stacked
      if (el.querySelector('[data-rail]')) return;
      var dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'rail-dot';
      dot.textContent = '💬';
      dot.setAttribute('aria-label', 'Visa kontext för denna del');
      dot.addEventListener('click', function (e) {
        e.preventDefault();
        applyRail(el);
        el.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      el.appendChild(dot);
    });

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
      if (rail && marks.length) {
        var best = null;
        for (var j = 0; j < marks.length; j++) {
          if (marks[j].getBoundingClientRect().top <= LINE) best = marks[j];
        }
        applyRail(best);
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
    update();
    return function destroy() {
      document.removeEventListener('scroll', onScroll, { capture: true });
    };
  }

  window.lagenScrollspy = spy;

  var body = document.querySelector('.gr-body');
  if (body) spy(body, lagenDom.island(document));
})();
