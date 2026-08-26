/* The lightbox for extracted graphics: a formula, map, figure or road sign the
   consolidated text dropped and the .graphics layer recovered from the published
   PDF (lib/page.render_grafik). On the page these are printed small -- a road
   sign is 3.5rem tall in its table cell -- and small is exactly where a sign's
   symbol, a formula's subscripts and a map's labels stop being readable. The
   crop is served at twice the page DPI (facsimile.CROP_DPI), so there is real
   detail behind the thumbnail; this is what opens it.

   Every extracted graphic is a `button.grafik-open` around the crop, so the
   handler binds to the one class and covers both shapes the renderer emits: the
   block <figure> and the road-sign table cell.

   The overlay is built here rather than server-rendered because it exists only
   while it is open -- one at a time, discarded on close, so the page carries no
   markup for a state it is almost never in. Plain DOM, no <dialog>: the reading
   chrome already closes on Escape and a backdrop click (drawers.js, search.js),
   and matching that is worth more than the element's focus trap. Because there
   is no trap, the overlay does not claim to be modal either (see below). */
(function () {
  var box = null;         // the open overlay, or null
  var opener = null;      // the button that opened it, to return focus to

  // the thumbnail's crop URL, asking for the full-size render of the same gap
  function large(src) {
    var url = new URL(src, location.href);
    url.searchParams.set('stor', '1');
    return url.href;
  }

  function close() {
    if (!box) return;
    box.remove();
    box = null;
    // focus goes back where the reader left it, not to the top of the document
    // (an open box always has its opener -- the two are set and cleared here
    // together, and the early return above covers the closed state)
    opener.focus();
    opener = null;
  }

  function open(button) {
    close();
    // the renderer always puts one <img> in the button and always gives it a
    // non-empty alt (page.render_grafik falls back to "<label> ur SFS <nr>",
    // the road-sign cell to "Vägmärke <kod>"), so neither is guarded here
    var img = button.querySelector('img');
    opener = button;
    box = document.createElement('div');
    box.className = 'grafik-lightbox';
    box.setAttribute('role', 'dialog');
    // deliberately NOT aria-modal: nothing here traps focus or hides the page
    // behind it, and announcing a modality Tab does not honour is worse than
    // announcing none. Escape and the backdrop are the way out.
    box.setAttribute('aria-label', img.alt);

    var full = document.createElement('img');
    full.className = 'grafik-full';
    // the same crop, re-rendered for this size: `stor=1` asks the endpoint for
    // the full-size resolution rather than the thumbnail's. Stretching the
    // thumbnail instead would only blur it -- the graphic is vector art in the
    // PDF, so there is always more detail to render. `alt` is the vision pass's
    // or the road-sign scanner's own description, the only caption these have.
    full.src = large(img.currentSrc || img.src);
    full.alt = img.alt;
    box.appendChild(full);

    var cap = document.createElement('p');
    cap.className = 'grafik-full-cap';
    cap.textContent = img.alt;
    box.appendChild(cap);

    var shut = document.createElement('button');
    shut.type = 'button';
    shut.className = 'grafik-close';
    shut.setAttribute('aria-label', 'Stäng');
    shut.textContent = '×';
    box.appendChild(shut);

    document.body.appendChild(box);
    shut.focus();
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('.grafik-open');
    if (b) { e.preventDefault(); open(b); return; }
    if (!box) return;
    // the close button, and the backdrop -- but not the image itself, so a
    // click meant to steady a look at the graphic does not dismiss it
    if (e.target.closest('.grafik-close') || !e.target.closest('.grafik-full'))
      close();
  });

  // Escape closes the lightbox. It reaches drawers.js and search.js too --
  // they listen on `document` as well and are earlier in the bundle, so they
  // run first whatever this one does -- which at worst also shuts a drawer the
  // reader had left open behind the overlay.
  document.addEventListener('keydown', function (e) {
    if (box && e.key === 'Escape') close();
  });
})();
