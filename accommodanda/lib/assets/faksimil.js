/* The facsimile viewer: every förarbete page anchor is a button (the page
   number itself) that loads that printed page's retina PNG from
   /api/v1/facsimile -- rendered on demand server-side, disk-cached, browser-
   cached immutable -- and shows it inline under the anchor. Plain DOM.

   The two controls are a *tab row*, which is what they already looked like: the
   page number and "Original" sit side by side as adjacent segments, so clicking
   the lit one to turn it off read as a toggle disguised as tabs. Selecting a tab
   now switches to it and leaves it selected; the other tab is what switches
   back. aria-selected carries the state, and the selected tab stops responding
   to clicks rather than un-selecting itself. */
(function () {
  document.addEventListener('click', function (e) {
    var t = e.target.closest('.sid > button');
    if (!t) return;
    var span = t.parentNode;
    var b = span.querySelector('button[data-fax]');
    var nr = span.querySelector('.sid-nr');
    if (!b) return;
    var next = span.nextElementSibling;
    var showing = next && next.classList.contains('faksimil');
    function select(fax) {
      if (b) b.setAttribute('aria-selected', fax ? 'true' : 'false');
      if (nr) nr.setAttribute('aria-selected', fax ? 'false' : 'true');
    }
    if (t === nr) {                 // back to the text
      if (showing) next.remove();
      b.removeAttribute('aria-controls');   // the panel it named is gone
      select(false);
      return;
    }
    if (showing) return;            // already the selected tab: not a toggle
    select(true);
    var img = document.createElement('img');
    img.className = 'faksimil';
    img.alt = 'Faksimil av sidan ' + b.textContent;
    img.decoding = 'async';
    // the panel the tablist controls. Declared here rather than in the template
    // because this is where the element comes into existence -- and declared at
    // all because half the pattern is worse than none: the markup already tells
    // assistive tech there is a tab set, so without a panel bound to the
    // selected tab it announces a control over nothing.
    img.id = span.id + '-faksimil';
    img.setAttribute('role', 'tabpanel');
    img.setAttribute('aria-labelledby', b.id || (b.id = span.id + '-fax-tab'));
    b.setAttribute('aria-controls', img.id);
    span.classList.add('fax-loading');
    img.onload = function () { span.classList.remove('fax-loading'); };
    img.onerror = function () {
      span.classList.remove('fax-loading');
      img.remove();
    };
    img.src = b.dataset.fax;
    span.parentNode.insertBefore(img, span.nextSibling);
  });
})();
