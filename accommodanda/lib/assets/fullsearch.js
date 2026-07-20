/* The complete result list at /sok/: paging and source/type/year facets over
   the same REST endpoint as the command palette. The page shell is static and
   this script is inert everywhere else. */
(function () {
  var root = document.querySelector('.search-page');
  if (!root) return;
  var form = root.querySelector('.full-search-form');
  var input = form.querySelector('input[name="q"]');
  var status = root.querySelector('.full-search-status');
  var facets = root.querySelector('.full-search-facets');
  var results = root.querySelector('.full-search-results');
  var pagination = root.querySelector('.search-pagination');
  var params = new URLSearchParams(location.search);
  var limit = 20, seq = 0, nextCursor = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function number(n) { return new Intl.NumberFormat('sv-SE').format(n); }
  function pageNo() { return Math.max(1, parseInt(params.get('page') || '1', 10) || 1); }
  function resetPaging() {
    params.delete('cursor'); params.delete('page'); params.delete('offset');
  }
  function address() {
    var query = params.toString();
    return '/sok/' + (query ? '?' + query : '');
  }
  function choose(field, value) {
    if (params.get(field) === value) params.delete(field);
    else params.set(field, value);
    resetPaging();
    load(true);
  }
  var SOURCE = {sfs:'Författningar', dv:'Rättsfall', forarbete:'Förarbeten',
    foreskrift:'Myndighetsföreskrifter', eurlex:'EU-rätt', avg:'JO, JK och ARN',
    kommentar:'Lagkommentarer', begrepp:'Begrepp'};
  var KIND = {law:'Författning', case:'Rättsfall', prop:'Proposition', sou:'SOU',
    ds:'Ds', dir:'Kommittédirektiv', regulation:'Förordning', directive:'Direktiv',
    decision:'Beslut', judgment:'Avgörande', treaty:'Fördrag', begrepp:'Begrepp'};

  function facetGroup(field, title, buckets) {
    var selected = params.get(field);
    buckets = (buckets || []).slice();
    if (field === 'year') buckets.sort(function (a, b) { return b.value.localeCompare(a.value); });
    if (selected && !buckets.some(function (b) { return b.value === selected; }))
      buckets.unshift({value:selected, count:0});
    var buttons = buckets.map(function (b) {
      var label = field === 'source' ? (SOURCE[b.value] || b.value) :
                  field === 'kind' ? (KIND[b.value] || b.value) : b.value;
      return '<button type="button" data-facet="' + esc(field) + '" data-value="' +
        esc(b.value) + '" aria-pressed="' + (selected === b.value ? 'true' : 'false') +
        '"><span>' + esc(label) + '</span><span class="facet-count">' +
        esc(number(b.count)) + '</span></button>';
    }).join('');
    return '<fieldset class="search-facet-group"><legend>' + esc(title) +
      '</legend>' + buttons + '</fieldset>';
  }
  function renderFacets(data) {
    var f = data.facets || {};
    facets.innerHTML = facetGroup('source', 'Källa', f.source) +
      facetGroup('kind', 'Typ', f.kind) + facetGroup('year', 'År', f.year);
  }
  function renderResults(data) {
    if (!data.results.length) {
      results.innerHTML = '<p class="empty">Inga träffar med de valda avgränsningarna.</p>';
      return;
    }
    results.innerHTML = data.results.map(function (r) {
      var frag = r.fragments && r.fragments[0];
      var target = (r.url || '#') + (frag && frag.pinpoint ? '#' + frag.pinpoint : '');
      var title = r.display || r.title || r.identifier || r.uri;
      var snip = (frag && frag.highlight && frag.highlight[0]) ||
                 (r.highlight && r.highlight[0]) || '';
      return '<article class="full-search-hit"><h2><a href="' + esc(target) + '">' +
        esc(title) + '</a></h2>' + (r.identifier && r.identifier !== title ?
        '<p class="hit-id">' + esc(r.identifier) + '</p>' : '') +
        (snip ? '<p class="hit-snip">' + snip + '</p>' : '') + '</article>';
    }).join('');
  }
  function renderPagination(total) {
    var current = pageNo(), pages = Math.max(1, Math.ceil(total / limit));
    pagination.innerHTML = '<button type="button" data-page-action="prev"' +
      (current > 1 ? '' : ' disabled') + '>← Föregående</button><span>Sida ' +
      esc(number(current)) + ' av ' + esc(number(pages)) +
      '</span><button type="button" data-page-action="next"' +
      (nextCursor ? '' : ' disabled') + '>Nästa →</button>';
  }
  function load(push, state) {
    var mine = ++seq;            // invalidates an older request even for empty q
    var q = (params.get('q') || '').trim();
    // note: load() must NOT write input.value -- it runs on every as-you-type
    // keystroke, and rewriting the field would strip a trailing space and jump
    // the caret. The input is synced explicitly on initial load / popstate.
    if (push) history.pushState(state || null, '', address());
    if (!q) {
      nextCursor = null;
      status.textContent = 'Skriv ett eller flera sökord.';
      facets.innerHTML = ''; results.innerHTML = ''; pagination.innerHTML = '';
      return;
    }
    status.textContent = 'Söker…';
    var api = new URLSearchParams(params);
    api.delete('page'); api.delete('offset'); api.set('limit', limit);
    fetch('/api/v1/search?' + api.toString()).then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    }).then(function (data) {
      if (mine !== seq) return;
      nextCursor = data.next_cursor || null;
      var first = data.total ? (pageNo() - 1) * limit + 1 : 0;
      var last = data.results.length ? Math.min(first + data.results.length - 1,
                                                data.total) : 0;
      status.textContent = first + '–' + last + ' av ' + number(data.total) + ' träffar';
      renderFacets(data); renderResults(data); renderPagination(data.total);
    }).catch(function () {
      if (mine === seq) status.textContent = 'Sökningen kunde inte nås. Prova igen.';
    });
  }
  // search-as-you-type: debounce the field and query without a full submit (S4).
  // The URL is kept current with replaceState (shareable/refreshable) rather than
  // pushState, so typing doesn't stack a history entry per keystroke; Enter still
  // does an explicit pushState submit below.
  var typingTimer = null;
  function queryFromInput(push) {
    var q = input.value.trim();
    if (q) params.set('q', q); else params.delete('q');
    resetPaging();
    if (push) load(true);
    else { history.replaceState(null, '', address()); load(false); }
  }
  input.addEventListener('input', function () {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(function () { queryFromInput(false); }, 200);
  });
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    clearTimeout(typingTimer);
    queryFromInput(true);
  });
  facets.addEventListener('click', function (e) {
    var button = e.target.closest('[data-facet]');
    if (button) choose(button.dataset.facet, button.dataset.value);
  });
  pagination.addEventListener('click', function (e) {
    var button = e.target.closest('[data-page-action]');
    if (!button || button.disabled) return;
    if (button.dataset.pageAction === 'next' && nextCursor) {
      var previous = address();
      params.set('cursor', nextCursor);
      params.set('page', pageNo() + 1);
      load(true, {previous: previous});
    } else if (button.dataset.pageAction === 'prev') {
      if (history.state && history.state.previous) history.back();
      else { resetPaging(); load(true); }       // a directly opened deep-page URL
    }
    window.scrollTo({top:0, behavior:'smooth'});
  });
  window.addEventListener('popstate', function () {
    params = new URLSearchParams(location.search);
    input.value = (params.get('q') || '').trim();   // load() no longer syncs it
    load(false);
  });
  input.value = (params.get('q') || '').trim();
  load(false);
})();
