/* The inline editor. Loaded on every page but inert until a logged-in session
   is confirmed (GET /auth/me -- only attempted when the login-time hint cookie
   is present, so an anonymous reader costs no API roundtrip at all); the
   static HTML stays identical and cacheable
   for anonymous readers, the edit affordances are grafted on client-side --
   the same approach scrollspy.js uses to inject the rail dots. It reads the
   page's identity from the <meta name="lagen-doc"> injected by
   render_document / the site renderer, attaches an ✎ button to each editable
   node (a §/article for commentary, or the whole body for a concept/
   editorial page), and drives the cart + checkout against the same-origin
   /internal-api/v1/edit/* routes.

   Two bases, because the editor spans both halves of the service: INTERNAL is
   the site's own surface (login, the editors), PUBLIC is the read API it uses
   for the link-target search box. */
(function () {
  var meta = document.querySelector('meta[name="lagen-doc"]');
  var INTERNAL = '/internal-api/v1';
  var PUBLIC = '/api/v1';
  var KIND = meta && meta.dataset.kind;
  var REF = meta && meta.dataset.ref;
  var SOURCE = meta && meta.dataset.source;      // patch identity (if patchable)
  var BASEFILE = meta && meta.dataset.basefile;
  var me = null, badgeEl = null;

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

  // the login check that decides whether any edit UI appears. The session
  // cookie is HttpOnly, so the readable lagen_editor_hint cookie (set/cleared
  // by /auth/login and /auth/logout) stands in for "a session may exist": no
  // hint means an anonymous reader, and the page loads without touching the
  // API. With the hint, a 401/403 (expired session or editing disabled)
  // leaves the page exactly as a reader sees it -- and drops the stale hint.
  function boot(u) {
    me = u;
    account();
    if (me) { refreshCart(); enableEditing(); }
  }
  if (/(?:^|;\s*)lagen_editor_hint=/.test(document.cookie)) {
    j(INTERNAL + '/auth/me').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (u) {
        if (!u) document.cookie = 'lagen_editor_hint=; Max-Age=0; path=/';
        boot(u);
      });
  } else {
    boot(null);
  }

  // A logged-in editor keeps a masthead control of their own: the same 2rem
  // circle the collection and theme controls use, carrying their initials and
  // -- once something is in the cart -- the count of uncommitted changes. It
  // opens the account panel (the cart, the commit form, logout). An anonymous
  // reader gets no sign-in affordance in the masthead -- login lives on its own
  // /admin/ page (adminPage below), so the reader chrome stays clean.
  function account() {
    var tools = document.querySelector('.masthead .mast-tools');
    if (tools && me) {
      var box = el('button', 'ed-account');
      box.type = 'button';
      box.innerHTML = '<span class="ed-initials">' + esc(initials(me.name)) +
                      '</span><span class="mast-badge"></span>';
      tools.appendChild(box);
      badgeEl = box.querySelector('.mast-badge');
      box.addEventListener('click', checkout);
      setCart(0);            // the label, until GET /edit/cart says otherwise
    }
    adminPage();
  }

  // "Staffan Malmgren" -> "SM"; a single-word name gives one letter
  function initials(name) {
    return (name || '?').trim().split(/\s+/).slice(0, 2)
      .map(function (w) { return w.charAt(0).toUpperCase(); }).join('');
  }
  // who the editor is, plus what they have not committed yet -- the one place
  // that sentence is written, used by the control's label and the panel head
  function accountLabel(n) {
    return 'Inloggad som ' + me.name +
      (n > 0 ? ' · ' + n + (n === 1 ? ' osparad ändring' : ' osparade ändringar') : '');
  }

  function logout(e) {
    if (e) e.preventDefault();
    j(INTERNAL + '/auth/logout', { method: 'POST' }).then(function () { location.reload(); });
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
      window.open(INTERNAL + '/patch/edit?source=' + encodeURIComponent(SOURCE) +
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
    var q = INTERNAL + '/edit/region?kind=' + encodeURIComponent(KIND) + '&ref=' + encodeURIComponent(REF) +
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
      j(INTERNAL + '/edit/region', { method: 'POST', body: { kind: KIND, ref: REF, anchor: anchor, new_text: ta.value } })
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
        j(PUBLIC + '/search?q=' + encodeURIComponent(q) + '&source=' + src + '&limit=6')
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
  // The cart has no widget of its own; its size rides as a badge on the
  // account control, the way the collection count does on its own icon.
  function setCart(n) {
    if (!badgeEl) return;
    badgeEl.textContent = n > 0 ? n : '';
    badgeEl.parentNode.title = accountLabel(n);
    badgeEl.parentNode.setAttribute('aria-label', badgeEl.parentNode.title);
  }
  function refreshCart() { j(INTERNAL + '/edit/cart').then(function (r) { return r.json(); }).then(function (d) { setCart((d.drafts || []).length); }); }

  function checkout() { j(INTERNAL + '/edit/cart').then(function (r) { return r.json(); }).then(function (d) { checkoutPanel(d.drafts || []); }); }

  function checkoutPanel(drafts) {
    var ov = overlay(), p = el('div', 'ed-panel'); ov.appendChild(p);
    p.appendChild(el('h3', null, esc(accountLabel(0))));
    p.appendChild(el('h4', null, 'Dina ändringar (' + drafts.length + ')'));
    var ul = el('ul', 'ed-cart-list'); p.appendChild(ul);
    drafts.forEach(function (dr) {
      var li = el('li', null, '<span>' + esc(label(dr.kind, dr.ref, dr.anchor)) + '</span>');
      var x = el('button', 'ed-rm', 'Ta bort');
      x.addEventListener('click', function () {
        j(INTERNAL + '/edit/discard', { method: 'POST', body: { key: dr.key } })
          .then(function (r) { return r.json(); })
          .then(function (res) { setCart(res.cart); ov.remove(); if (res.cart > 0) checkout(); });
      });
      li.appendChild(x); ul.appendChild(li);
    });
    if (!drafts.length) { p.appendChild(el('p', null, 'Korgen är tom.')); }
    var ta = el('textarea', 'ed-msg'); ta.placeholder = 'Beskriv ändringen (blir commit-meddelandet)';
    p.appendChild(ta);
    var row = el('div', 'ed-row', '<button class="ed-logout">Logga ut</button>' +
      '<button class="ed-cancel">Stäng</button><button class="ed-commit">Spara allt</button>');
    p.appendChild(row);
    var err = el('div', 'ed-err'); p.appendChild(err);
    row.querySelector('.ed-cancel').addEventListener('click', function () { ov.remove(); });
    row.querySelector('.ed-logout').addEventListener('click', logout);
    var commitBtn = row.querySelector('.ed-commit');
    if (!drafts.length) commitBtn.disabled = true;
    commitBtn.addEventListener('click', function () {
      var msg = ta.value.trim(); if (!msg) { ta.focus(); return; }
      commitBtn.disabled = true; commitBtn.textContent = 'Sparar…';
      j(INTERNAL + '/edit/commit', { method: 'POST', body: { message: msg } }).then(function (r) {
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

  // ---- login (the /admin/ page) -----------------------------------------
  // The login form is mounted into the [data-admin-login] host the static
  // /admin/ page ships. Anonymous -> the credential form; already logged in ->
  // who you are + logout. No-op on every other page (the host is absent).
  function adminPage() {
    var host = document.querySelector('[data-admin-login]');
    if (!host) return;
    host.innerHTML = '';
    if (me) {
      host.appendChild(el('p', null, 'Inloggad som <strong>' + esc(me.name) + '</strong>.'));
      var out = el('button', 'ed-do', 'Logga ut');
      host.appendChild(out);
      out.addEventListener('click', logout);
      return;
    }
    var u = el('input'); u.placeholder = 'Användarnamn'; u.autofocus = true;
    var pw = el('input'); pw.type = 'password'; pw.placeholder = 'Lösenord';
    var err = el('div', 'ed-err');
    var go = el('button', 'ed-do', 'Logga in');
    host.appendChild(u); host.appendChild(pw); host.appendChild(go); host.appendChild(err);
    // Why the login failed, in the reader's language. Every failure used to
    // read "Fel användarnamn eller lösenord", which is a lie for three of the
    // four: the rate limiter's 429 says the credentials were never checked,
    // and 403 says editing is off site-wide. An editor locked out by the
    // backoff retyped a correct password and was told it was wrong.
    //
    // 401 stays deliberately vague: /auth/login answers a wrong username and a
    // wrong password identically, so that neither the reply nor its timing can
    // enumerate editors, and this message must not undo that.
    function loginError(r) {
      if (r.status === 401) return 'Fel användarnamn eller lösenord.';
      if (r.status === 403) return 'Redigering är avstängd på den här servern.';
      if (r.status === 429) {
        // the limiter sends Retry-After in whole seconds; nginx may drop it,
        // so the wait is an addition, not the sentence
        var wait = parseInt(r.headers.get('Retry-After'), 10);
        if (!(wait > 0)) return 'För många inloggningsförsök. Försök igen senare.';
        return 'För många inloggningsförsök. Försök igen om '
          + wait + (wait === 1 ? ' sekund.' : ' sekunder.');
      }
      return 'Inloggningen misslyckades (fel ' + r.status + ').';
    }
    function submit() {
      j(INTERNAL + '/auth/login', { method: 'POST', body: { username: u.value, password: pw.value } })
        // '/ops', not '/ops/': the dashboard is registered at the exact path, and
        // the static site mounted at '/' matches the trailing-slash form before
        // Starlette's redirect_slashes can fire (see api/app.py), so logging in
        // landed on a 404
        .then(function (r) { if (r.ok) location.assign('/ops'); else err.textContent = loginError(r); });
    }
    go.addEventListener('click', submit);
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
