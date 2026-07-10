/* The inline editor. Loaded on every page but inert until a logged-in session
   is confirmed (GET /auth/me); the static HTML stays identical and cacheable
   for anonymous readers, the edit affordances are grafted on client-side --
   the same approach scrollspy.js uses to inject the rail dots. It reads the
   page's identity from the <meta name="lagen-doc"> injected by
   render_document / the site renderer, attaches an ✎ button to each editable
   node (a §/article for commentary, or the whole body for a concept/
   editorial page), and drives the cart + checkout against the same-origin
   /api/v1/edit/* routes. */
(function () {
  var meta = document.querySelector('meta[name="lagen-doc"]');
  var API = '/api/v1';
  var KIND = meta && meta.dataset.kind;
  var REF = meta && meta.dataset.ref;
  var SOURCE = meta && meta.dataset.source;      // patch identity (if patchable)
  var BASEFILE = meta && meta.dataset.basefile;
  var me = null, cartEl = null;

  function j(url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    if (opts.body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts);
  }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  // the login check that decides whether any edit UI appears. A 401/403 (anon or
  // editing disabled) leaves the page exactly as a reader sees it.
  j(API + '/auth/me').then(function (r) { return r.ok ? r.json() : null; })
    .then(function (u) {
      me = u;
      account();
      if (me) { mountCart(); refreshCart(); enableEditing(); }
    });

  function account() {
    var mast = document.querySelector('header.masthead');
    if (!mast) return;
    var box = el('span', 'ed-account');
    if (me) {
      box.innerHTML = esc(me.name) + ' · <a href="#" class="ed-logout">Logga ut</a>';
      mast.appendChild(box);
      box.querySelector('.ed-logout').addEventListener('click', function (e) {
        e.preventDefault();
        j(API + '/auth/logout', { method: 'POST' }).then(function () { location.reload(); });
      });
    } else {
      box.innerHTML = '<a href="#" class="ed-login">Logga in</a>';
      mast.appendChild(box);
      box.querySelector('.ed-login').addEventListener('click', function (e) { e.preventDefault(); loginPanel(); });
    }
  }

  // ---- attaching edit buttons -------------------------------------------
  function topButton(label, anchor) {
    var main = document.querySelector('main.gr-main') || document.querySelector('main');
    if (!main) return null;
    var b = el('button', 'ed-btn ed-btn-top'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', function (e) { e.preventDefault(); openEditor(anchor); });
    main.insertBefore(b, main.firstChild);
    return b;
  }
  // a "patch source" button beside the commentary one -- opens the source-fix
  // editor for this document (correct a scanning error, or redact personal data).
  // Only for a patchable document (SOURCE is set on the page meta).
  function patchButton(after) {
    if (!SOURCE || !BASEFILE || !after) return;
    var b = el('button', 'ed-btn ed-btn-top ed-btn-patch'); b.type = 'button';
    b.textContent = '🩹 Patcha källtext';
    b.title = 'Rätta eller avidentifiera källtexten för detta dokument';
    b.addEventListener('click', function (e) {
      e.preventDefault();
      window.open(API + '/patch/edit?source=' + encodeURIComponent(SOURCE) +
                  '&basefile=' + encodeURIComponent(BASEFILE), '_blank');
    });
    after.after(b);
  }
  function enableEditing() {
    if (!meta) return;                 // page carries no editable content
    if (KIND === 'kommentar') {
      // the act as a whole (document-level commentary, the "Om dokumentet" rail)
      patchButton(topButton('✎ Kommentera dokumentet', null));
      // and one per commentable node (a §/article/recital/chapter)
      var sel = 'main section.paragraf[id], main h3.artikel[id], main p.recital[id], main h2.kaprubrik[id]';
      document.querySelectorAll(sel).forEach(function (node) {
        var b = el('button', 'ed-btn'); b.type = 'button'; b.textContent = '✎';
        b.title = 'Redigera kommentar till denna del';
        b.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); openEditor(node.id); });
        node.appendChild(b);
      });
    } else {
      topButton('✎ Redigera sidan', null);   // begrepp / editorial: whole body
    }
  }

  // ---- the inline editor ------------------------------------------------
  function openEditor(anchor) {
    var q = API + '/edit/region?kind=' + encodeURIComponent(KIND) + '&ref=' + encodeURIComponent(REF) +
            (anchor ? '&anchor=' + encodeURIComponent(anchor) : '');
    j(q).then(function (r) { return r.json(); }).then(function (v) { editorPanel(anchor, v.markdown); });
  }

  function editorPanel(anchor, text) {
    var ov = overlay(), p = el('div', 'ed-panel'); ov.appendChild(p);
    p.appendChild(el('h3', null, esc(label(KIND, REF, anchor))));
    var ta = el('textarea'); ta.value = text; p.appendChild(ta);
    var tools = el('div', 'ed-tools',
      '<button data-src="sfs">Länk: lag</button>' +
      '<button data-src="eurlex">Länk: EU-rätt</button>' +
      '<button data-src="begrepp">Länk: begrepp</button>');
    p.appendChild(tools);
    var picker = el('div'); p.appendChild(picker);
    var prev = el('div', 'ed-preview'); p.appendChild(prev);
    function render() { prev.innerHTML = mdPreview(ta.value); }
    ta.addEventListener('input', render); render();
    tools.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function (e) { e.preventDefault(); linkPicker(picker, btn.getAttribute('data-src'), ta, render); });
    });
    var row = el('div', 'ed-row', '<button class="ed-cancel">Avbryt</button><button class="ed-save">Lägg i korg</button>');
    p.appendChild(row);
    row.querySelector('.ed-cancel').addEventListener('click', function () { ov.remove(); });
    row.querySelector('.ed-save').addEventListener('click', function () {
      j(API + '/edit/region', { method: 'POST', body: { kind: KIND, ref: REF, anchor: anchor, new_text: ta.value } })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) { alert('Kunde inte spara: ' + (d.detail || r.status)); });
          return r.json().then(function (d) { setCart(d.cart); ov.remove(); });
        });
    });
  }

  function linkPicker(box, src, ta, render) {
    box.innerHTML = '';
    var inp = el('input'); inp.placeholder = 'Sök att länka till…';
    var ul = el('ul', 'ed-search-res'); box.appendChild(inp); box.appendChild(ul); inp.focus();
    var timer;
    inp.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = inp.value.trim(); if (!q) { ul.innerHTML = ''; return; }
        j(API + '/search?q=' + encodeURIComponent(q) + '&source=' + src + '&limit=6')
          .then(function (r) { return r.json(); }).then(function (res) {
            ul.innerHTML = '';
            (res.results || []).forEach(function (hit) {
              var tok = token(src, hit.uri); if (!tok) return;
              var name = hit.display || hit.identifier || hit.title || q;
              var li = el('li', null, esc(name) + ' <small>' + esc(tok) + '</small>');
              li.addEventListener('click', function () {
                insert(ta, '[' + name + '](' + tok + ')'); render(); box.innerHTML = '';
              });
              ul.appendChild(li);
            });
          });
      }, 200);
    });
  }

  function token(src, uri) {
    var m;
    if (src === 'begrepp') { m = uri.match(/\/begrepp\/(.+)$/); return m ? 'begrepp:' + m[1] : null; }
    if (src === 'eurlex') { m = uri.match(/\/ext\/celex\/([^#]+)/); return m ? 'eurlex:' + m[1] : null; }
    m = uri.match(/^https?:\/\/lagen\.nu\/([^#]+)/); return m ? 'sfs:' + m[1] : null;
  }
  function insert(ta, s) {
    var a = ta.selectionStart, b = ta.selectionEnd;
    ta.value = ta.value.slice(0, a) + s + ta.value.slice(b);
    ta.selectionStart = ta.selectionEnd = a + s.length; ta.focus();
  }

  // ---- cart + checkout --------------------------------------------------
  function mountCart() {
    cartEl = el('button', 'ed-cart'); cartEl.type = 'button'; cartEl.style.display = 'none';
    cartEl.addEventListener('click', checkout); document.body.appendChild(cartEl);
  }
  function setCart(n) {
    if (!cartEl) return;
    cartEl.textContent = '🧺 ' + n + (n === 1 ? ' ändring' : ' ändringar');
    cartEl.style.display = n > 0 ? 'block' : 'none';
  }
  function refreshCart() { j(API + '/edit/cart').then(function (r) { return r.json(); }).then(function (d) { setCart((d.drafts || []).length); }); }

  function checkout() { j(API + '/edit/cart').then(function (r) { return r.json(); }).then(function (d) { checkoutPanel(d.drafts || []); }); }

  function checkoutPanel(drafts) {
    var ov = overlay(), p = el('div', 'ed-panel'); ov.appendChild(p);
    p.appendChild(el('h3', null, 'Dina ändringar (' + drafts.length + ')'));
    var ul = el('ul', 'ed-cart-list'); p.appendChild(ul);
    drafts.forEach(function (dr) {
      var li = el('li', null, '<span>' + esc(label(dr.kind, dr.ref, dr.anchor)) + '</span>');
      var x = el('button', 'ed-rm', 'Ta bort');
      x.addEventListener('click', function () {
        j(API + '/edit/discard', { method: 'POST', body: { key: dr.key } })
          .then(function (r) { return r.json(); })
          .then(function (res) { setCart(res.cart); ov.remove(); if (res.cart > 0) checkout(); });
      });
      li.appendChild(x); ul.appendChild(li);
    });
    if (!drafts.length) { p.appendChild(el('p', null, 'Korgen är tom.')); }
    var ta = el('textarea', 'ed-msg'); ta.placeholder = 'Beskriv ändringen (blir commit-meddelandet)';
    p.appendChild(ta);
    var row = el('div', 'ed-row', '<button class="ed-cancel">Stäng</button><button class="ed-commit">Spara allt</button>');
    p.appendChild(row);
    var err = el('div', 'ed-err'); p.appendChild(err);
    row.querySelector('.ed-cancel').addEventListener('click', function () { ov.remove(); });
    var commitBtn = row.querySelector('.ed-commit');
    if (!drafts.length) commitBtn.disabled = true;
    commitBtn.addEventListener('click', function () {
      var msg = ta.value.trim(); if (!msg) { ta.focus(); return; }
      commitBtn.disabled = true; commitBtn.textContent = 'Sparar…';
      j(API + '/edit/commit', { method: 'POST', body: { message: msg } }).then(function (r) {
        if (r.ok) return r.json().then(function () { setCart(0); location.reload(); });
        return r.json().then(function (d) {
          var detail = d.detail;
          if (r.status === 409 && detail && detail.conflicts) {
            err.textContent = 'Någon annan hann ändra: ' + detail.conflicts.join(', ') + '. Ladda om och försök igen.';
          } else { err.textContent = 'Fel: ' + (typeof detail === 'string' ? detail : r.status); }
          commitBtn.disabled = false; commitBtn.textContent = 'Spara allt';
        });
      });
    });
  }

  // ---- login ------------------------------------------------------------
  function loginPanel() {
    var ov = overlay(), p = el('div', 'ed-panel'); ov.appendChild(p);
    p.appendChild(el('h3', null, 'Logga in'));
    var u = el('input'); u.placeholder = 'Användarnamn';
    var pw = el('input'); pw.type = 'password'; pw.placeholder = 'Lösenord';
    p.appendChild(u); p.appendChild(pw);
    var err = el('div', 'ed-err');
    var row = el('div', 'ed-row', '<button class="ed-cancel">Avbryt</button><button class="ed-do">Logga in</button>');
    p.appendChild(row); p.appendChild(err);
    row.querySelector('.ed-cancel').addEventListener('click', function () { ov.remove(); });
    function submit() {
      j(API + '/auth/login', { method: 'POST', body: { username: u.value, password: pw.value } })
        .then(function (r) { if (r.ok) location.reload(); else err.textContent = 'Fel användarnamn eller lösenord.'; });
    }
    row.querySelector('.ed-do').addEventListener('click', submit);
    pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    u.focus();
  }

  // ---- helpers ----------------------------------------------------------
  function overlay() {
    var ov = el('div', 'ed-overlay');
    ov.addEventListener('click', function (e) { if (e.target === ov) ov.remove(); });
    document.body.appendChild(ov); return ov;
  }
  function label(kind, ref, anchor) {
    if (kind === 'kommentar') return 'Kommentar · ' + ref + (anchor ? ' · ' + anchor : ' · hela dokumentet');
    if (kind === 'begrepp') return 'Begrepp · ' + ref;
    return 'Sida · ' + ref;
  }
  // a deliberately small preview: headings, paragraphs and [text](target) links.
  // Not the full citation-linked render (that is the server's job on publish) --
  // just enough to see structure and links before carting.
  function mdPreview(src) {
    return src.split(/\n{2,}/).map(function (block) {
      block = block.trim(); if (!block) return '';
      var h = block.match(/^(#{1,6})\s+(.*)$/);
      if (h) { var n = Math.min(h[1].length + 2, 6); return '<h' + n + '>' + inline(h[2]) + '</h' + n + '>'; }
      return '<p>' + inline(block) + '</p>';
    }).join('');
  }
  function inline(t) {
    return esc(t).replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, txt, tgt) {
      return '<a href="#" title="' + esc(tgt) + '">' + esc(txt) + '</a>';
    });
  }
})();
