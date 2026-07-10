/* The facsimile viewer: every förarbete page anchor is a button (the page
   number itself) that loads that printed page's retina PNG from
   /api/v1/facsimile -- rendered on demand server-side, disk-cached, browser-
   cached immutable -- and toggles it inline under the anchor. Plain DOM. */
(function () {
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.sid > button[data-fax]');
    if (!b) return;
    var span = b.parentNode;
    var next = span.nextElementSibling;
    if (next && next.classList.contains('faksimil')) {
      next.remove();
      return;
    }
    var img = document.createElement('img');
    img.className = 'faksimil';
    img.alt = 'Faksimil av sidan ' + b.textContent;
    img.decoding = 'async';
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
