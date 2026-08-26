/* Browser-owned PDF collections. The editable draft lives in localStorage;
   the bookmark carries a compact, versioned recipe in its fragment. The
   server receives the complete manifest only when it inspects or renders it. */
(function () {
  var STORAGE = 'lagen_samling_v1';
  var refreshToolbar = function () {};
  var ADD_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16' +
    'a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path>' +
    '<path d="M12 11v7M8.5 14.5h7"></path></svg>';
  var COLLECTION_ICON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><path d="M4 5v14a2 2 0 0 0 2 2h13"></path>' +
    '<rect x="7" y="3" width="14" height="15" rx="1"></rect>' +
    '<path d="M10 7h8M10 11h8"></path></svg>';

  function fresh() {
    return { version: 1, title: 'Författningssamling', subtitle: '',
      cover: true, toc: true, columns: 1, context: [], items: [] };
  }

  function normalized(value) {
    var state = fresh();
    if (!value || value.version !== 1 || !Array.isArray(value.items)) {
      throw new Error('ogiltig samling');
    }
    if (value.items.length > 1000) throw new Error('för många dokument');
    if (value.title != null && (typeof value.title !== 'string' || value.title.length > 200)) {
      throw new Error('ogiltig titel');
    }
    if (value.subtitle != null && (typeof value.subtitle !== 'string' || value.subtitle.length > 400)) {
      throw new Error('ogiltig undertitel');
    }
    if (value.context != null && (!Array.isArray(value.context)
        || value.context.some(function (kind) { return typeof kind !== 'string'; }))) {
      throw new Error('ogiltig kontext');
    }
    state.title = typeof value.title === 'string' ? value.title : state.title;
    state.subtitle = typeof value.subtitle === 'string' ? value.subtitle : '';
    state.cover = value.cover !== false;
    state.toc = value.toc !== false;
    state.columns = value.columns === 2 ? 2 : 1;
    state.context = Array.isArray(value.context) ? value.context.slice() : [];
    var paths = {};
    state.items = value.items.map(function (item) {
      if (!item || typeof item.path !== 'string' || item.path.length < 2
          || item.path.length > 300 || item.path.charAt(0) !== '/'
          || /[?#\\]/.test(item.path)) throw new Error('ogiltig dokumentsökväg');
      if (paths[item.path]) throw new Error('samma dokument får inte förekomma flera gånger');
      paths[item.path] = true;
      if (item.start != null && ['direct', 'page', 'recto'].indexOf(item.start) === -1) {
        throw new Error('ogiltigt startläge');
      }
      if (item.sections != null && (!Array.isArray(item.sections)
          || item.sections.length > 500
          || item.sections.some(function (section) { return typeof section !== 'string'; }))) {
        throw new Error('ogiltigt avsnittsval');
      }
      return { path: item.path, start: item.start || 'direct', amendments: item.amendments !== false,
        preamble: item.preamble !== false,
        sections: Array.isArray(item.sections) ? item.sections.slice() : [],
        title: item.title || '', label: item.label || '', info: item.info || null };
    });
    if (state.columns === 2) state.context = [];
    return state;
  }

  function manifest(state) {
    return { version: 1, title: state.title, subtitle: state.subtitle,
      cover: state.cover, toc: state.toc, columns: state.columns,
      context: state.columns === 2 ? [] : state.context.slice(),
      items: state.items.map(function (item) {
        return { path: item.path, start: item.start,
          amendments: item.amendments, preamble: item.preamble,
          sections: item.sections.slice() };
      }) };
  }

  function bytesTo64(text) {
    var bytes = new TextEncoder().encode(text), binary = '';
    for (var i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function from64(text) {
    var padded = text.replace(/-/g, '+').replace(/_/g, '/');
    padded += '='.repeat((4 - padded.length % 4) % 4);
    var binary = atob(padded), bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }

  function wire(state) {
    var compact = { v: 1, i: state.items.map(function (item) {
      var flags = (item.amendments ? 0 : 1) | (item.preamble ? 0 : 2);
      var row = [item.path.replace(/^\//, '')];
      if (item.start !== 'direct' || flags || item.sections.length) {
        row[1] = item.start === 'page' ? 'p' : item.start === 'recto' ? 'r' : '';
      }
      if (flags || item.sections.length) row[2] = flags;
      if (item.sections.length) row[3] = item.sections;
      return row;
    }) };
    if (state.title !== 'Författningssamling') compact.t = state.title;
    if (state.subtitle) compact.s = state.subtitle;
    if (!state.cover) compact.c = 0;
    if (!state.toc) compact.o = 0;
    if (state.columns === 2) compact.l = 2;
    if (state.context.length) compact.k = state.context;
    return 'j1.' + bytesTo64(JSON.stringify(compact));
  }

  function unwire(encoded) {
    if (encoded.charAt(0) === '#') encoded = encoded.slice(1);
    if (!encoded.startsWith('j1.')) throw new Error('okänd länkversion');
    var compact = JSON.parse(from64(encoded.slice(3)));
    if (compact.v !== 1 || !Array.isArray(compact.i)) throw new Error('ogiltig samling');
    return normalized({ version: 1, title: compact.t || 'Författningssamling',
      subtitle: compact.s || '', cover: compact.c !== 0, toc: compact.o !== 0,
      columns: compact.l === 2 ? 2 : 1, context: compact.k || [],
      items: compact.i.map(function (row) {
        if (!Array.isArray(row) || typeof row[0] !== 'string') {
          throw new Error('ogiltig dokumentrad');
        }
        var flags = row[2] || 0;
        return { path: '/' + row[0],
          start: row[1] === 'p' ? 'page' : row[1] === 'r' ? 'recto' : 'direct',
          amendments: !(flags & 1), preamble: !(flags & 2),
          sections: row[3] || [] };
      }) });
  }

  function load() {
    var stored = localStorage.getItem(STORAGE);
    if (!stored) return fresh();
    try { return normalized(JSON.parse(stored)); }
    catch (reason) { localStorage.removeItem(STORAGE); return fresh(); }
  }

  function store(state, animate) {
    localStorage.setItem(STORAGE, JSON.stringify(state));
    refreshToolbar(state, !!animate);
  }

  function collectionUrl(state) {
    return location.origin + '/samling#' + wire(state);
  }

  function editor() {
    var page = document.querySelector('[data-collection-editor]');
    if (!page) return false;
    var state, initialError = '';
    try { state = location.hash ? unwire(location.hash) : load(); }
    catch (reason) { state = load(); initialError = 'Samlingslänken kunde inte läsas: ' + reason.message + '.'; }
    store(state);
    var list = page.querySelector('.collection-list');
    var empty = page.querySelector('.collection-empty');
    var count = page.querySelector('.collection-count');
    var error = page.querySelector('.collection-error');
    var title = page.querySelector('[name=collection-title]');
    var subtitle = page.querySelector('[name=collection-subtitle]');
    var cover = page.querySelector('[name=collection-cover]');
    var toc = page.querySelector('[name=collection-toc]');
    var context = page.querySelector('[name=collection-context]');
    var contextKinds = page.querySelector('.collection-context-kinds');
    var directLink = page.querySelector('[data-collection-link]');

    function fail(message) {
      error.textContent = message;
      error.hidden = !message;
    }

    function save() {
      store(state);
      history.replaceState(null, '', '/samling#' + wire(state));
      directLink.href = collectionUrl(state);
      directLink.textContent = collectionUrl(state);
    }

    function move(from, to) {
      if (to < 0 || to >= state.items.length || from === to) return;
      state.items.splice(to, 0, state.items.splice(from, 1)[0]);
      save(); render();
    }

    function button(text, action, label) {
      var control = document.createElement('button');
      control.type = 'button'; control.textContent = text;
      if (label) control.setAttribute('aria-label', label);
      control.addEventListener('click', action);
      return control;
    }

    function option(value, text) {
      var element = document.createElement('option');
      element.value = value; element.textContent = text;
      return element;
    }

    function documentRow(item, index) {
      var row = document.createElement('li');
      row.className = 'collection-document-row';
      row.draggable = true;
      row.dataset.index = String(index);
      row.addEventListener('dragstart', function (event) {
        event.dataTransfer.setData('text/x-lagen-collection-index', String(index));
      });
      row.addEventListener('dragover', function (event) { event.preventDefault(); });
      row.addEventListener('drop', function (event) {
        event.preventDefault();
        move(Number(event.dataTransfer.getData('text/x-lagen-collection-index')), index);
      });

      var head = document.createElement('div'); head.className = 'collection-document-head';
      var names = document.createElement('div'); names.className = 'collection-document-name';
      var identifier = document.createElement('div'); identifier.className = 'eyebrow';
      identifier.textContent = item.label || item.path;
      var heading = document.createElement('h3');
      var link = document.createElement('a'); link.href = item.path;
      link.textContent = item.title || item.path; heading.appendChild(link);
      names.appendChild(identifier); names.appendChild(heading); head.appendChild(names);
      var ordering = document.createElement('div'); ordering.className = 'collection-ordering';
      ordering.appendChild(button('↑', function () { move(index, index - 1); }, 'Flytta upp'));
      ordering.appendChild(button('↓', function () { move(index, index + 1); }, 'Flytta ned'));
      ordering.appendChild(button('Ta bort', function () {
        state.items.splice(index, 1); save(); render(); inspect();
      }));
      head.appendChild(ordering); row.appendChild(head);

      var settings = document.createElement('div'); settings.className = 'collection-document-settings';
      var startLabel = document.createElement('label'); startLabel.appendChild(document.createTextNode('Start '));
      var start = document.createElement('select');
      start.appendChild(option('direct', 'Direkt efter'));
      start.appendChild(option('page', 'Nästa sida'));
      start.appendChild(option('recto', 'Nästa högersida'));
      start.value = index === 0 ? 'recto' : item.start;
      start.disabled = index === 0;
      start.addEventListener('change', function () { item.start = start.value; save(); });
      startLabel.appendChild(start); settings.appendChild(startLabel);

      if (item.info && item.info.amendments) {
        var amendments = document.createElement('label');
        var amendmentBox = document.createElement('input'); amendmentBox.type = 'checkbox';
        amendmentBox.checked = item.amendments;
        amendmentBox.addEventListener('change', function () {
          item.amendments = amendmentBox.checked; save();
        });
        amendments.appendChild(amendmentBox);
        amendments.appendChild(document.createTextNode(' Ändringar och övergångsbestämmelser'));
        settings.appendChild(amendments);
      }
      if (item.info && item.info.preamble) {
        var preamble = document.createElement('label');
        var preambleBox = document.createElement('input'); preambleBox.type = 'checkbox';
        preambleBox.checked = item.preamble;
        preambleBox.addEventListener('change', function () {
          item.preamble = preambleBox.checked; save();
        });
        preamble.appendChild(preambleBox);
        preamble.appendChild(document.createTextNode(' Preambel'));
        settings.appendChild(preamble);
      }
      row.appendChild(settings);

      if (item.info && item.info.outline.length) {
        var details = document.createElement('details'); details.className = 'collection-sections';
        var summary = document.createElement('summary');
        summary.textContent = item.sections.length
          ? item.sections.length + ' valda avsnitt' : 'Hela dokumentet';
        details.appendChild(summary);
        var limited = document.createElement('label');
        var limitedBox = document.createElement('input'); limitedBox.type = 'checkbox';
        limitedBox.checked = item.sections.length > 0;
        limited.appendChild(limitedBox); limited.appendChild(document.createTextNode(' Endast valda avsnitt'));
        details.appendChild(limited);
        var outline = document.createElement('div'); outline.className = 'collection-outline';
        var sectionBoxes = [];
        function updateSectionControls() {
          summary.textContent = item.sections.length
            ? item.sections.length + ' valda avsnitt' : 'Hela dokumentet';
          sectionBoxes.forEach(function (box) {
            box.checked = item.sections.indexOf(box.value) !== -1;
            box.disabled = !limitedBox.checked;
          });
        }
        item.info.outline.forEach(function (entry) {
          var label = document.createElement('label');
          label.style.setProperty('--outline-level', String(entry.level));
          var box = document.createElement('input'); box.type = 'checkbox'; box.value = entry.id;
          box.checked = item.sections.indexOf(entry.id) !== -1;
          box.disabled = !limitedBox.checked;
          box.addEventListener('change', function () {
            if (box.checked && item.sections.indexOf(entry.id) === -1) item.sections.push(entry.id);
            if (!box.checked) item.sections = item.sections.filter(function (id) { return id !== entry.id; });
            if (!item.sections.length) limitedBox.checked = false;
            save(); updateSectionControls();
          });
          sectionBoxes.push(box);
          label.appendChild(box); label.appendChild(document.createTextNode(' ' + entry.label));
          outline.appendChild(label);
        });
        limitedBox.addEventListener('change', function () {
          if (!limitedBox.checked) item.sections = [];
          else if (!item.sections.length && item.info.outline.length) {
            item.sections = [item.info.outline[0].id];
          }
          save(); updateSectionControls();
        });
        details.appendChild(outline); row.appendChild(details);
      }
      return row;
    }

    function allContextKinds() {
      var found = {}, answer = [];
      state.items.forEach(function (item) {
        if (!item.info) return;
        item.info.context.forEach(function (kind) {
          if (found[kind.key]) return;
          found[kind.key] = true; answer.push(kind);
        });
      });
      return answer;
    }

    function renderContext() {
      // the legend names the group for a screen reader, so only the
      // generated labels go -- innerHTML = '' took it away as well
      var kinds = allContextKinds();
      Array.prototype.forEach.call(contextKinds.querySelectorAll('label'),
        function (label) { label.remove(); });
      kinds.forEach(function (kind) {
        var label = document.createElement('label'), box = document.createElement('input');
        box.type = 'checkbox'; box.value = kind.key;
        box.checked = state.context.indexOf(kind.key) !== -1;
        box.addEventListener('change', function () {
          if (box.checked && state.context.indexOf(kind.key) === -1) state.context.push(kind.key);
          if (!box.checked) state.context = state.context.filter(function (key) { return key !== kind.key; });
          save();
        });
        label.appendChild(box); label.appendChild(document.createTextNode(' ' + kind.label));
        contextKinds.appendChild(label);
      });
      context.checked = state.context.length > 0;
      context.disabled = state.columns === 2 || !kinds.length;
      contextKinds.disabled = !context.checked || context.disabled;
    }

    function render() {
      title.value = state.title; subtitle.value = state.subtitle;
      cover.checked = state.cover; toc.checked = state.toc;
      page.querySelector('[name=collection-columns][value="' + state.columns + '"]').checked = true;
      list.innerHTML = '';
      state.items.forEach(function (item, index) { list.appendChild(documentRow(item, index)); });
      empty.hidden = state.items.length > 0;
      count.textContent = state.items.length + ' dokument';
      page.querySelector('[data-collection-create]').disabled = !state.items.length;
      renderContext();
    }

    function inspect() {
      if (!state.items.length) return;
      fetch('/internal-api/v1/pdf/samling/inspektera', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: state.items.map(function (item) { return item.path; }) }) })
        .then(function (response) {
          if (!response.ok) throw new Error('servern svarade ' + response.status);
          return response.json();
        }).then(function (answer) {
          answer.documents.forEach(function (info) {
            var item = state.items.find(function (candidate) { return candidate.path === info.path; });
            item.title = info.title; item.label = info.label; item.info = info;
          });
          fail(''); save(); render();
        }).catch(function (reason) { fail('Dokumenten kunde inte läsas: ' + reason.message + '.'); });
    }

    title.addEventListener('input', function () { state.title = title.value; save(); });
    subtitle.addEventListener('input', function () { state.subtitle = subtitle.value; save(); });
    cover.addEventListener('change', function () { state.cover = cover.checked; save(); });
    toc.addEventListener('change', function () { state.toc = toc.checked; save(); });
    Array.prototype.forEach.call(page.querySelectorAll('[name=collection-columns]'), function (box) {
      box.addEventListener('change', function () {
        if (!box.checked) return;
        state.columns = Number(box.value);
        if (state.columns === 2) {
          state.context = [];
          state.toc = false;
          state.items.forEach(function (item) { item.amendments = false; });
        }
        save(); render();
      });
    });
    context.addEventListener('change', function () {
      state.context = context.checked ? allContextKinds().map(function (kind) { return kind.key; }) : [];
      save(); renderContext();
    });

    page.querySelector('[data-collection-clear]').addEventListener('click', function () {
      state = fresh(); save(); render();
    });
    page.querySelector('[data-collection-copy]').addEventListener('click', function () {
      var control = this, status = page.querySelector('.collection-copy-status');
      var url = collectionUrl(state);
      function copied() {
        control.title = 'Länken är kopierad';
        control.setAttribute('aria-label', 'Länken är kopierad');
        control.classList.add('copied');
        status.textContent = 'Länken är kopierad.';
        setTimeout(function () {
          control.title = 'Kopiera direktlänken';
          control.setAttribute('aria-label', 'Kopiera direktlänken');
          control.classList.remove('copied');
          status.textContent = '';
        }, 2000);
      }
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(copied);
      else {
        var input = document.createElement('textarea'); input.value = url;
        document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); copied();
      }
    });
    page.querySelector('[data-collection-export]').addEventListener('click', function () {
      var blob = new Blob([JSON.stringify(manifest(state), null, 2) + '\n'], { type: 'application/json' });
      var link = document.createElement('a'); link.href = URL.createObjectURL(blob);
      link.download = 'lagen-nu-samling.json'; document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(link.href);
    });
    page.querySelector('.collection-import input').addEventListener('change', function () {
      var input = this; if (!input.files.length) return;
      input.files[0].text().then(function (text) {
        state = normalized(JSON.parse(text)); save(); render(); inspect();
      }).catch(function (reason) { fail('Samlingen kunde inte läsas: ' + reason.message + '.'); });
    });
    page.querySelector('[data-collection-create]').addEventListener('click', function () {
      fail('');
      if (!state.items.length) return fail('Samlingen innehåller inga dokument.');
      var download = page.querySelector('[name=collection-download]').checked;
      var target = '/internal-api/v1/pdf/samling/vanta' + (download ? '?download=1' : '') + '#' + wire(state);
      if (!window.open(target, '_blank')) fail('Webbläsaren blockerade den nya fliken.');
    });

    save(); render(); if (initialError) fail(initialError); inspect();
    window.addEventListener('storage', function (event) {
      if (event.key === STORAGE) { state = load(); render(); inspect(); }
    });
    return true;
  }

  function collectionToolbar() {
    var tools = document.querySelector('.masthead .mast-tools');
    if (!tools) return;
    var control = document.createElement('a');
    control.className = 'collection-toolbar';
    control.innerHTML = COLLECTION_ICON + '<span class="mast-badge"></span>';
    tools.insertBefore(control, tools.firstChild);

    function render(state, animate) {
      var count = state.items.length;
      control.hidden = !count;          // an empty collection shows no icon
      // the control is hidden, not removed, so its attributes stay in the DOM:
      // clearing them keeps the last collection's recipe out of the href
      control.href = '/samling' + (count ? '#' + wire(state) : '');
      control.title = count ? 'Visa samlingen (' + count + ')' : 'Samlingen är tom';
      control.setAttribute('aria-label', control.title);
      control.querySelector('.mast-badge').textContent = count || '';
      if (animate && count) {
        control.classList.remove('collection-arrived');
        void control.offsetWidth;
        control.classList.add('collection-arrived');
      }
    }
    control.addEventListener('animationend', function () {
      control.classList.remove('collection-arrived');
    });
    refreshToolbar = render;
    render(load(), false);
    window.addEventListener('storage', function (event) {
      if (event.key === STORAGE) render(load(), false);
    });
  }

  function wait() {
    var page = document.querySelector('[data-collection-wait]');
    if (!page) return false;
    var state;
    try { state = unwire(location.hash); }
    catch (reason) {
      page.querySelector('#fas').textContent = 'Ogiltig samlingslänk';
      page.querySelector('#note').textContent = reason.message; return true;
    }
    var body = manifest(state), download = new URL(location.href).searchParams.get('download') === '1';
    var bar = page.querySelector('#bar'), phase = page.querySelector('#fas');
    var left = page.querySelector('#kvar'), note = page.querySelector('#note');
    page.querySelector('.doc a').textContent = state.title;

    function fail(message) { phase.textContent = 'Det gick inte'; phase.className = 'left err';
      left.textContent = ''; note.textContent = message; }
    function show(status) {
      bar.style.width = Math.round(status.andel * 100) + '%';
      var text = status.fas;
      if (status.sida) text += ' — sida ' + status.sida + (status.sidor
        ? (status.exakt ? ' av ' : ' av ca ') + status.sidor : '');
      phase.textContent = text;
      left.textContent = status.kvar == null ? '' : status.kvar > 90
        ? 'ca ' + Math.round(status.kvar / 60) + ' min kvar'
        : 'ca ' + Math.max(1, status.kvar) + ' s kvar';
    }
    function done(id) {
      bar.style.width = '100%'; phase.textContent = 'klar'; left.textContent = '';
      var result = '/internal-api/v1/pdf/jobb/' + id + '/resultat' + (download ? '?download=1' : '');
      if (!download) return location.replace(result);
      var link = document.createElement('a'); link.href = result;
      document.body.appendChild(link); link.click(); link.remove();
      note.textContent = 'PDF:en laddas ned.';
    }
    function poll(id) {
      fetch('/internal-api/v1/pdf/jobb/' + id).then(function (response) {
        if (!response.ok) throw new Error('servern svarade ' + response.status);
        return response.json();
      }).then(function (status) {
        if (status.fel) return fail('Renderingen misslyckades (' + status.fel + ').');
        if (status.klar) return done(id);
        show(status); setTimeout(function () { poll(id); }, 1000);
      }).catch(function (reason) { fail('Kontakten med servern bröts (' + reason.message + ').'); });
    }
    fetch('/internal-api/v1/pdf/samling/jobb', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (response) {
        if (!response.ok) return response.json().then(function (answer) {
          throw new Error(answer.detail || ('servern svarade ' + response.status));
        });
        return response.json();
      }).then(function (status) {
        if (status.klar) done(status.id); else { show(status); poll(status.id); }
      }).catch(function (reason) { fail('Renderingen kunde inte starta (' + reason.message + ').'); });
    return true;
  }

  function documentControl() {
    var toc = document.querySelector('aside.toc-col');
    if (!toc || location.pathname === '/samling') return;
    var row = toc.querySelector('.toc-list'); if (!row) return;
    var state = load(), path = location.pathname;
    var control = document.createElement('button'); control.type = 'button';
    control.className = 'collection-add';
    control.innerHTML = ADD_ICON;

    function render() {
      var included = state.items.some(function (item) { return item.path === path; });
      control.title = included ? 'Visa samlingen' : 'Lägg till i samling';
      control.setAttribute('aria-label', control.title);
      control.setAttribute('aria-pressed', included ? 'true' : 'false');
    }
    control.addEventListener('click', function () {
      var existing = state.items.some(function (item) { return item.path === path; });
      if (existing) { location.href = '/samling#' + wire(state); return; }
      var heading = document.querySelector('.frontmatter h1');
      var eyebrow = document.querySelector('.frontmatter .eyebrow');
      state.items.push({ path: path, start: 'direct', amendments: true, preamble: true,
        sections: [], title: heading ? heading.textContent.trim() : path,
        label: eyebrow ? eyebrow.textContent.trim() : path, info: null });
      store(state, true); render();
    });
    row.appendChild(control); render();
    window.addEventListener('storage', function (event) {
      if (event.key === STORAGE) { state = load(); render(); }
    });
  }

  window.lagenCollection = { fresh: fresh, normalized: normalized,
    manifest: manifest, wire: wire, unwire: unwire };
  var isEditor = editor();
  var isWait = !isEditor && wait();
  collectionToolbar();
  if (!isEditor && !isWait) documentControl();
})();
