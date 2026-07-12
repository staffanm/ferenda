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
})();
