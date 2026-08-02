/* Shared DOM vocabulary for the page-chrome scripts (loaded first; scrollspy,
   search and popover build on it via window.lagenDom).

   Its reason to exist is the split reading view: once popover.js imports
   another document into a pane, one DOM holds several documents whose node
   ids collide (two statutes both have #P1). The rule for resolving an anchor
   against the page's *own* document -- and the id-attribute selector and
   landing-flash idioms that ride along -- must live in exactly one place, or
   the scripts drift on it (rule:second-use-goes-to-lib, in spirit). */
(function () {
  // attribute selector for an anchor id -- node ids may contain characters a
  // bare #-selector chokes on (dots in EU sub-article ids like "6.2.a")
  function sel(id) {
    return '[id="' + String(id).replace(/"/g, '') + '"]';
  }

  // the page's own element for an anchor id -- never one inside an imported
  // split-view pane (another document's ids), the search palette or a popover
  function ownEl(id) {
    var els = document.querySelectorAll(sel(id));
    for (var i = 0; i < els.length; i++)
      if (!els[i].closest('[data-pane], .search-overlay, .lagen-popover'))
        return els[i];
    return null;
  }

  // bring a jump target into view with the landing flash (restarted so
  // repeated jumps to the same target keep flashing)
  function flash(el) {
    el.scrollIntoView({ block: 'start' });
    el.classList.remove('jump-flash');
    void el.offsetWidth;
    el.classList.add('jump-flash');
  }

  // the renderer's JSON island (per-unit context-rail panels) of a document --
  // the live one or a fetched, DOMParser-parsed page
  function island(doc) {
    var s = doc.getElementById('lagen-context');
    if (!s) return {};
    try { return JSON.parse(s.textContent); } catch (e) { return {}; }
  }

  // One search hit -> the three things both result lists show, so the ⌘K palette
  // (search.js) and the full page (/sok/, fullsearch.js) cannot describe the
  // same hit differently (Q4). `sub` is the citation id where it adds anything
  // over the heading, else what sort of document this is -- a lagrådsremiss or
  // a promemoria carries no number, so its identifier *is* its title and the
  // full page used to show a bare heading with no hint of its kind.
  function hitFields(r) {
    var frag = r.fragments && r.fragments[0];
    var title = r.display || r.title || r.identifier || r.uri;
    // a citation-resolved hit leads with the provision it landed on -- the
    // reader asked for "4 kap. 4 § brottsbalken", so "Brottsbalk (1962:700)"
    // alone gave no sign the pin had worked (Q2)
    var sub = (frag && frag.label) ||
              ((r.identifier && r.identifier !== title) ? r.identifier
                 : (r.kind_label || ''));
    return {
      target: (r.url || '#') + (frag && frag.pinpoint ? '#' + frag.pinpoint : ''),
      title: title,
      sub: sub,
      snippet: (frag && frag.highlight && frag.highlight[0]) ||
               (r.highlight && r.highlight[0]) || ''
    };
  }

  window.lagenDom = { sel: sel, ownEl: ownEl, flash: flash, island: island,
                      hitFields: hitFields };
})();
