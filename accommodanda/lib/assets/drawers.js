/* Mobile drawers: the bottom toolbar (render.page emits it on document pages)
   opens the TOC as a left drawer and the context rail as a bottom sheet, by
   toggling a body class style.css animates under its mobile breakpoint. Inert
   on desktop -- the toolbar is display:none there, so nothing ever toggles.
   One drawer at a time; a scrim tap, Escape, a re-tap of the toolbar button
   or following a link inside a drawer closes it. Plain DOM, no deps. */
(function () {
  var CLASSES = { toc: 'toc-open', rail: 'rail-open' };
  var scrim = null;

  function setOpen(name, on) {
    document.body.classList.toggle(CLASSES[name], on);
    document.querySelectorAll('[data-drawer="' + name + '"]').forEach(
      function (b) { b.setAttribute('aria-expanded', on); });
  }
  function closeAll() {
    Object.keys(CLASSES).forEach(function (n) { setOpen(n, false); });
    if (scrim) { scrim.remove(); scrim = null; }
  }
  function open(name) {
    Object.keys(CLASSES).forEach(function (n) { setOpen(n, n === name); });
    if (!scrim) {
      scrim = document.createElement('div');
      scrim.className = 'drawer-scrim';
      scrim.addEventListener('click', closeAll);
      document.body.appendChild(scrim);
    }
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-drawer]');
    if (b) {
      var name = b.getAttribute('data-drawer');
      document.body.classList.contains(CLASSES[name]) ? closeAll() : open(name);
      return;
    }
    // opening the search palette (its own script handles [data-search]) must
    // close an open drawer first, so the palette never stacks over a drawer +
    // scrim -- otherwise one Escape would dismiss both at once
    if (e.target.closest('[data-search]')) { closeAll(); return; }
    // following a link inside a drawer (a TOC entry, a rail citation) either
    // scrolls this page or navigates away -- close over it either way
    if (scrim && e.target.closest('.toc-col a, aside.rail a')) closeAll();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAll();
  });

  /* Pin the toolbar to the *visual* viewport.

     `position: fixed; bottom: 0` resolves against the layout viewport, and on
     iOS the two come apart: the browser chrome shrinks as you scroll down, the
     visible area grows under it, and the layout viewport the bar is measured
     against does not follow. The bar then sits where the bottom used to be --
     stranded a chrome's height up the page, with the text it is supposed to
     float over running past it on both sides.

     visualViewport reports where the visible area actually is, so the gap
     between the two bottoms is the correction. Signed, not clamped: the same
     mismatch runs the other way when the chrome expands again, and a pinch-zoom
     pan moves the visual viewport inside the layout one. Where the browser keeps
     them together the gap is 0 and the transform is dropped rather than set to a
     no-op, so nothing is left behind on a desktop that never needed it. */
  var vv = window.visualViewport;
  var bar = document.querySelector('.mobile-bar');
  if (vv && bar) {
    var pending = false;
    var pin = function () {
      pending = false;
      var gap = window.innerHeight - vv.offsetTop - vv.height;
      bar.style.transform = gap ? 'translateY(' + (-gap) + 'px)' : '';
    };
    var schedule = function () {
      if (!pending) { pending = true; requestAnimationFrame(pin); }
    };
    vv.addEventListener('resize', schedule);
    vv.addEventListener('scroll', schedule);
    pin();
  }
})();
