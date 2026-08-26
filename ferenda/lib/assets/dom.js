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

  // One search hit -> the fields both result lists show, so the ⌘K palette
  // (search.js) and the full page (/sok/, fullsearch.js) cannot describe the
  // same hit differently (Q4). `sub` is the citation id where it adds anything
  // over the heading, else what sort of document this is -- a lagrådsremiss or
  // a promemoria carries no number, so its identifier *is* its title and the
  // full page used to show a bare heading with no hint of its kind.
  //
  // A hit leads to its DOCUMENT. The one exception is `r.pin`: the provision a
  // citation-shaped query resolved to, which is the answer itself. The API's
  // other fragment field, `r.fragments`, holds the passages full text matched
  // inside the document -- supporting detail (`passages`, rendered under the
  // hit on /sok/), never the link. Reading the two as one field is what sent
  // "dataförordningen" into article 47 of the EU Data Act: the act's name
  // stands there because that article amends another regulation by quoting the
  // title, and the palette followed the passage instead of the act.
  function hitFields(r) {
    var pin = r.pin;
    var url = r.url || '#';
    var title = r.display || r.title || r.identifier || r.uri;
    // a citation-resolved hit leads with the provision it landed on -- the
    // reader asked for "4 kap. 4 § brottsbalken", so "Brottsbalk (1962:700)"
    // alone gave no sign the pin had worked (Q2)
    var sub = (pin && pin.label) ||
              ((r.identifier && r.identifier !== title) ? r.identifier
                 : (r.kind_label || ''));
    return {
      url: url,
      target: url + (pin && pin.pinpoint ? '#' + pin.pinpoint : ''),
      title: title,
      sub: sub,
      snippet: (pin && pin.highlight && pin.highlight[0]) ||
               (r.highlight && r.highlight[0]) || '',
      passages: r.fragments || []
    };
  }

  // Shortest query the two surfaces send to /api/v1/search *as you type*.
  // Below it the answer is both useless and expensive: lib/search.py
  // `_text_query` prefixes every word, so "N" leaves as `N*` and Lucene
  // expands it against the whole term dictionary before any filter narrows
  // anything -- measured on prod, 2.6 s and 231,076 hits for one letter, and
  // the palette's own prefixes are the slowest queries the cluster records.
  // An explicit search (Enter) is exempt in both: "EU" and "JO" are real
  // queries, and a reader who submits one has asked for it. Both surfaces
  // query as you type, so the floor lives here rather than in each
  // (rule:second-use-goes-to-lib).
  var MIN_QUERY = 3;
  function tooShort(q) { return q.trim().length < MIN_QUERY; }

  // The note both surfaces show below the floor. The first sentence is always
  // true; `how` names the surface's own way to search the corpus anyway, and
  // is left out where there is none. /sok/ carries a Sök button the palette
  // does not have, and the palette's Enter goes to a local pinpoint when the
  // page has one -- so the affordance is the caller's to name, while the
  // sentence stays single (rule:second-use-goes-to-lib).
  function tooShortNote(how) {
    return 'Skriv minst ' + MIN_QUERY + ' tecken för att söka i hela korpuset.'
           + (how ? ' ' + how + ' för att söka ändå.' : '');
  }

  window.lagenDom = { sel: sel, ownEl: ownEl, flash: flash, island: island,
                      hitFields: hitFields,
                      tooShort: tooShort, tooShortNote: tooShortNote };
})();
