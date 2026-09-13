/* paraTEXT -- a standalone reader over lagen.nu's open API.
 *
 * Three screens, one page: search (GET /api/v1/search, with its facets),
 * browse (GET /api/v1/browse, one leaf bucket at a time) and the document
 * itself (GET /api/v1/document, the parsed artifact drawn as a reading
 * text). Beside the document stand its table of contents and the context of
 * the unit the reader is looking at -- who cites this paragraf, from
 * GET /api/v1/document/inbound -- and a hover over any citation asks
 * GET /api/v1/card for the target's own words.
 *
 * The app is an API showcase, like its sibling paraGRAF: every request it
 * makes is logged in the traffic card, and every row links to the raw JSON
 * answer. The whole view lives in the hash, so any screen is a link. */
(function () {
  "use strict";
  const mount = document.querySelector(".pt");
  if (!mount) return;

  /* ---------------- constants ---------------- */
  const API = mount.dataset.api;       // origin of the API and the doc pages
  const GRAF = mount.dataset.graph;    // paraGRAF, for "utforska grafen"
  const HOST = "https://lagen.nu";     // the uri namespace every id lives in
  const NARROW = 1000;                 // the stylesheet's stacking breakpoint
  const TRAFFIC_MAX = 40;
  const SEARCH_PAGE = 20;
  const BROWSE_PAGE = 500;
  const INBOUND_PAGE = 500;            // rows the document-level context takes
  const CTX_CAP = 12;                  // rows shown per context section before "+ n till"

  // what each source is called to a reader (lib/facets.SOURCE_LABELS)
  const SOURCE_LABEL = {
    sfs: "Författningar", dv: "Rättsfall", forarbete: "Förarbeten",
    foreskrift: "Myndighetsföreskrifter", avg: "Myndighetsavgöranden",
    rs: "Rättsliga ställningstaganden", eurlex: "EU-rätt",
    guidance: "EU-vägledning", hudoc: "Europadomstolens praxis",
    coe: "Europarådets fördrag", icrc: "Internationell humanitär rätt",
    untc: "FN-fördrag", icc: "Internationella brottmålsdomstolen",
    icj: "Internationella domstolen", kommentar: "Lagkommentarer",
    begrepp: "Begrepp", remisser: "Remissvar", lawreview: "Tidskriftsartiklar",
  };
  // the sources /browse answers for, in the site's own order, and the label
  // the masthead wears for the ones it names
  const BROWSABLE = ["sfs", "dv", "forarbete", "foreskrift", "avg", "rs",
                     "eurlex", "guidance", "hudoc", "coe", "icrc", "untc",
                     "icc", "icj", "begrepp"];
  const MAST_NAV = [["sfs", "Lagar"], ["dv", "Rättsfall"],
                    ["forarbete", "Förarbeten"], ["foreskrift", "Föreskrifter"],
                    ["eurlex", "EU-rätt"], ["hudoc", "Folkrätt"],
                    ["begrepp", "Begrepp"]];
  // one flat hue per flow group, the palette paraGRAF paints its nodes with
  const GROUP_COLOR = {
    "Författningar": "#e63946", "Förarbeten": "#7b5cd6", "Rättsfall": "#f4a018",
    "Föreskrifter": "#a3b414", "Myndighetsavgöranden": "#06a561",
    "Ställningstaganden": "#0fa3a3", "Lagkommentarer": "#d6367e",
    "Tidskriftsartiklar": "#ff7b3a", "Begrepp": "#f06fa8",
    "EU-rättsakter": "#1d71d1", "EU-domar": "#5aa8ec", "EU-fördrag": "#123f77",
    "EU-vägledning": "#7ba2c9", "Konventioner": "#8b3fa8",
    "Folkrättslig praxis": "#c07ad1",
  };
  const FLOW_GROUP = {
    sfs: "Författningar", forarbete: "Förarbeten", dv: "Rättsfall",
    foreskrift: "Föreskrifter", avg: "Myndighetsavgöranden",
    rs: "Ställningstaganden", kommentar: "Lagkommentarer", begrepp: "Begrepp",
    guidance: "EU-vägledning", lawreview: "Tidskriftsartiklar",
    coe: "Konventioner", icrc: "Konventioner", untc: "Konventioner",
    hudoc: "Folkrättslig praxis", icj: "Folkrättslig praxis",
    icc: "Folkrättslig praxis",
  };
  const EU_CASELAW = new Set(["judgment", "opinion", "order"]);
  function groupOf(source, kind) {
    if (source === "eurlex")
      return EU_CASELAW.has(kind) ? "EU-domar"
        : kind === "treaty" ? "EU-fördrag" : "EU-rättsakter";
    return FLOW_GROUP[source] || "";
  }
  const colorOf = group => GROUP_COLOR[group] || "#8b887a";
  // which context a reader wants first when a unit carries several kinds of
  // citer (lib/page.RAIL_SECTION_ORDER, reduced to the sources a citation row
  // can come from)
  const RAIL_ORDER = ["kommentar", "dv", "avg", "rs", "hudoc", "icc", "icj",
                      "sfs", "forarbete", "foreskrift", "eurlex", "guidance",
                      "lawreview", "coe", "icrc", "untc", "begrepp"];
  const RAIL_LABEL = {
    kommentar: "Kommentar", dv: "Rättsfall", avg: "Myndighetsavgöranden",
    rs: "Ställningstaganden", hudoc: "Europadomstolen",
    icc: "Internationella brottmålsdomstolen", icj: "Internationella domstolen",
    sfs: "Författningar", forarbete: "Förarbeten", foreskrift: "Föreskrifter",
    eurlex: "EU-rätt", guidance: "EU-vägledning", lawreview: "Tidskriftsartiklar",
    coe: "Europarådets fördrag", icrc: "Humanitär rätt", untc: "FN-fördrag",
    begrepp: "Begrepp",
  };
  const fmt = n => Number(n || 0).toLocaleString("sv-SE");
  const esc = s => String(s == null ? "" : s).replace(/&/g, "&amp;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  // an ECHR judgment names itself "CASE OF VLASOV v. RUSSIA" -- de-shout it.
  // Lower-casing a Turkish "İ" leaves an i plus a combining dot (U+0307), so
  // "BİÇER" came back "Bi̇çer"; the dot goes with the case fold.
  function caseName(s) {
    if (!s || !/^CASE OF /.test(s)) return s;
    const out = s.slice(8).toLowerCase().replace(/̇/g, "")
      .replace(/\p{L}+/gu, w => w.charAt(0).toUpperCase() + w.slice(1))
      .replace(/\bV\.\s/g, "v. ")
      .replace(/\b(And|Of|The)\b/g, m => m.toLowerCase());
    return out.charAt(0).toUpperCase() + out.slice(1);
  }
  // A förarbete's official title continues its identifier ("Prop. 1983/84:138
  // om ändring i räntelagen"), so standing alone it opens lowercase and reads
  // cut off. Raise its first letter where it stands as a heading of its own.
  const upFirst = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  /* A search highlight is a fragment of HTML: the matched terms in <em>, and
   * every other character entity-escaped by the search backend ("Prop.
   * 1983&#x2F;84:138"). Escaping it again printed those entities literally, so
   * it is parsed instead, and rebuilt from its text and <em> runs alone --
   * no other element or attribute survives. */
  const HL_PARSER = new DOMParser();
  function hl(s) {
    const doc = HL_PARSER.parseFromString("<body>" + String(s || ""), "text/html");
    let out = "";
    for (const node of doc.body.childNodes) {
      if (node.nodeType === 3) out += esc(node.nodeValue);
      else if (node.nodeName === "EM") out += "<em>" + esc(node.textContent) + "</em>";
      else out += esc(node.textContent);
    }
    return out;
  }
  const pathOf = uri => uri.replace(HOST, "") || "/";   // uri -> page path
  // a page path -> the uri it addresses. The path comes out of the hash, where
  // the browser keeps it percent-encoded ("/begrepp/R%C3%A4ntelagen"), and the
  // uri the API is keyed on carries the characters themselves.
  function uriOf(path) {
    try { return HOST + decodeURI(path); } catch (err) { return HOST + path; }
  }
  const splitFrag = uri => { const i = uri.indexOf("#");
    return i < 0 ? [uri, ""] : [uri.slice(0, i), uri.slice(i + 1)]; };

  /* ---------------- the API, logged ---------------- */
  const trafficList = mount.querySelector(".pt-traffic"),
        trafficSum = mount.querySelector(".pt-traffic-sum");
  const traffic = [];
  let reqCount = 0, reqBytes = 0;
  const fmtSize = b => b >= 1024
    ? (b / 1024).toFixed(1).replace(".", ",") + " kB" : b + " B";
  function shortPath(path) {
    let p = path.replace("/api/v1/", "");
    try { p = decodeURIComponent(p); } catch (err) { /* show it raw */ }
    return p.replace(/https?:\/\/lagen\.nu\//g, "");
  }
  function renderTraffic() {
    trafficList.innerHTML = traffic.map(row =>
      '<li><a href="' + esc(row.url) + '" target="_blank" rel="noopener">'
      + '<span class="m">GET</span><span class="p">' + esc(row.short)
      + '</span><span class="s' + (row.ok ? "" : " err") + '">' + row.status
      + '</span><span class="kb">' + (row.bytes == null ? "" : fmtSize(row.bytes))
      + '</span><span class="ms">'
      + (row.ms == null ? "" : Math.round(row.ms) + " ms") + "</span></a></li>"
    ).join("");
    trafficSum.textContent = reqCount
      ? "· " + fmt(reqCount) + " anrop, " + fmtSize(reqBytes) : "";
  }
  /* rel -> Promise(payload). An artifact runs to megabytes (jordabalken is
   * 4.9 MB), so the cache is bounded: it exists to make stepping back to a
   * document instant, not to hold a session's whole reading. */
  const cache = new Map();
  const CACHE_MAX = 24;
  function api(path, params, raw) {
    const rel = path + (params ? "?" + params : "");
    if (cache.has(rel)) return cache.get(rel);
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
    const row = { url: API + rel, short: shortPath(rel),
                  status: "…", ok: true, bytes: null, ms: null };
    traffic.unshift(row);
    if (traffic.length > TRAFFIC_MAX) traffic.length = TRAFFIC_MAX;
    reqCount += 1;
    renderTraffic();
    const t0 = performance.now();
    const p = fetch(row.url).then(async r => {
      const text = await r.text();
      row.status = r.status; row.ok = r.ok;
      row.bytes = text.length; row.ms = performance.now() - t0;
      reqBytes += text.length;
      renderTraffic();
      if (!r.ok) {
        let detail = "";
        try { detail = JSON.parse(text).detail; } catch (err) { /* not json */ }
        const httpErr = new Error(typeof detail === "string" && detail
                                  ? detail : path + " " + r.status);
        httpErr.status = r.status;
        throw httpErr;
      }
      return raw ? text : JSON.parse(text);
    }).catch(err => {
      if (row.status === "…") { row.status = "fel"; row.ok = false; }
      row.ms = performance.now() - t0;
      renderTraffic();
      cache.delete(rel);            // a failure must not poison the cache
      throw err;
    });
    cache.set(rel, p);
    return p;
  }
  // the truth per status, for a screen that could not be fetched
  function errorHtml(err, what) {
    const s = err.status;
    const why = s === 404 ? "finns inte i samlingen"
      : s === 503 ? "servern laddar — försök igen om en stund"
      : s ? "servern svarade " + s
      : "servern kunde inte nås";
    return '<div class="pt-error">' + esc(what) + " " + why
      + (err.message && s !== 404 ? ' <code>' + esc(err.message) + "</code>" : "")
      + "</div>";
  }

  /* ---------------- the cards ---------------- */
  const page = mount.querySelector(".pt-page"),
        cardL = mount.querySelector(".pt-card-l"),
        cardR = mount.querySelector(".pt-card-r"),
        cardT = mount.querySelector(".pt-card-t"),
        bodyL = cardL.querySelector(".pt-cardbody"),
        bodyR = cardR.querySelector(".pt-cardbody"),
        titleL = cardL.querySelector(".pt-cardtitle"),
        titleR = cardR.querySelector(".pt-cardtitle");
  function fold(card, folded) {
    card.classList.toggle("folded", folded);
    card.querySelector(".pt-fold")
        .setAttribute("aria-expanded", folded ? "false" : "true");
  }
  const cards = [cardL, cardR, cardT];
  cards.forEach(card => {
    card.querySelector(".pt-cardhead").addEventListener("click", ev => {
      if (ev.target.closest("a")) return;
      const nowFolded = !card.classList.contains("folded");
      fold(card, nowFolded);
      // on a phone the bars are an accordion: one open at a time
      if (!nowFolded && innerWidth <= NARROW)
        for (const other of cards) if (other !== card) fold(other, true);
    });
  });
  function narrowFold() {
    if (innerWidth <= NARROW) cards.forEach(c => fold(c, true));
    else cards.forEach(c => fold(c, false));
  }
  let wasNarrow = innerWidth <= NARROW;
  addEventListener("resize", () => {
    const narrow = innerWidth <= NARROW;
    if (narrow !== wasNarrow) { wasNarrow = narrow; narrowFold(); }
  });
  function setCard(card, title, html) {
    card.querySelector(".pt-cardtitle").textContent = title;
    card.querySelector(".pt-cardbody").innerHTML = html;
    card.querySelector(".pt-cardbody").scrollTop = 0;
  }
  const LOADING = '<div class="pt-loading"><span class="sq"></span>hämtar …</div>';

  /* ---------------- routing: the hash carries the whole view ----------
   * "#/"                      the start page
   * "#/sok?q=…&source=…"      search
   * "#/bladdra/dv/nja/2024"   browse a leaf bucket
   * "#/1975:635#P6"           a document, optionally a unit in it; the
   *                           path is the document's own page path */
  let generation = 0;
  function parseHash() {
    let raw = location.hash.slice(1);
    if (!raw.startsWith("/")) raw = "/" + raw;
    const [path, anchor] = splitFrag(raw);
    const q = path.indexOf("?");
    const route = q < 0 ? path : path.slice(0, q);
    const params = new URLSearchParams(q < 0 ? "" : path.slice(q + 1));
    return { route: route, params: params, anchor: anchor };
  }
  function go(hash, replace) {
    if (location.hash === hash) { render(); return; }
    if (replace) history.replaceState(null, "", hash);
    else history.pushState(null, "", hash);
    render();
  }
  addEventListener("popstate", render);
  function docHash(uri) {
    const [root, frag] = splitFrag(uri);
    return "#" + pathOf(root) + (frag ? "#" + frag : "");
  }
  function searchHash(q, filters) {
    const p = new URLSearchParams({ q: q });
    for (const k of ["source", "kind", "year", "sort", "cursor"])
      if (filters && filters[k]) p.set(k, filters[k]);
    return "#/sok?" + p.toString();
  }
  const browseHash = (source, slugs, offset) =>
    "#/bladdra/" + source + (slugs && slugs.length ? "/" + slugs.join("/") : "")
    + (offset ? "?offset=" + offset : "");
  // every in-app link is a hash link; a click on one that names another
  // document also closes the popover
  mount.addEventListener("click", ev => {
    const a = ev.target.closest("a[data-uri]");
    if (!a) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
    ev.preventDefault();
    hidePop();
    go(docHash(a.dataset.uri));
  });
  function render() {
    const gen = ++generation;
    hidePop();
    const r = parseHash();
    syncMastNav(r);
    if (r.route === "/" || r.route === "") return renderStart(gen);
    if (r.route === "/sok") return renderSearch(gen, r.params);
    if (r.route.startsWith("/bladdra/")) return renderBrowse(gen, r.route.slice(9), r.params);
    return renderDoc(gen, uriOf(r.route), r.anchor, r.params);
  }
  function syncMastNav(r) {
    const src = r.route.startsWith("/bladdra/") ? r.route.slice(9).split("/")[0]
      : currentDoc && r.route !== "/sok" && r.route !== "/" ? currentDoc.source : "";
    mount.querySelectorAll(".pt-nav a").forEach(a =>
      a.classList.toggle("on", a.dataset.source === src));
  }
  mount.querySelector(".pt-nav").innerHTML = MAST_NAV.map(([s, label]) =>
    '<a href="' + browseHash(s) + '" data-source="' + s + '">' + label + "</a>").join("");

  /* ---------------- search boxes (masthead and start page) ------------- */
  const hitName = res => upFirst(caseName((res.pin && res.abbr) || res.display
                                          || res.title || res.identifier || res.uri));
  const hitWhere = res => (res.pin && res.pin.label)
    || (res.identifier && res.identifier !== hitName(res) ? res.identifier : "")
    || res.kind_label || SOURCE_LABEL[res.source] || "";
  const hitTarget = res => res.pin && res.pin.uri ? res.pin.uri : res.uri;
  function searchBox(root) {
    const input = root.querySelector("input"),
          hits = root.querySelector(".pt-hits");
    let hitRows = [], hitIndex = -1, timer = null;
    function pickHit(i) {
      if (i < 0 || !hitRows[i]) { submit(); return; }
      hits.hidden = true;
      go(docHash(hitTarget(hitRows[i])));
    }
    function submit() {
      const text = input.value.trim();
      hits.hidden = true;
      if (text) go(searchHash(text));
    }
    root.addEventListener("submit", ev => { ev.preventDefault(); submit(); });
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const text = input.value.trim();
        if (text.length < 2) { hits.hidden = true; return; }
        let data;
        try {
          data = await api("/api/v1/search",
                           "q=" + encodeURIComponent(text) + "&limit=8");
        } catch (err) {
          hitRows = []; hitIndex = -1;
          hits.innerHTML = '<div class="hit">sökningen kunde inte nås</div>';
          hits.hidden = false;
          return;
        }
        if (input.value.trim() !== text) return;
        hitRows = data.results;
        hitIndex = -1;
        hits.innerHTML = hitRows.map((res, i) =>
          '<div class="hit" data-i="' + i + '"><span class="lb"><span class="dot" '
          + 'style="--c:' + colorOf(groupOf(res.source, res.kind)) + '"></span>'
          + esc(hitName(res)) + '</span><span class="ti">'
          + esc(hitWhere(res)) + "</span></div>").join("")
          + '<div class="hit all" data-all="1">alla ' + fmt(data.total)
          + " träffar →</div>";
        hits.hidden = false;
        hits.querySelectorAll(".hit[data-i]").forEach(el =>
          el.addEventListener("click", () => pickHit(Number(el.dataset.i))));
        hits.querySelector("[data-all]").addEventListener("click", submit);
      }, 180);
    });
    input.addEventListener("keydown", ev => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        hitIndex = Math.max(-1, Math.min(hitRows.length - 1,
          hitIndex + (ev.key === "ArrowDown" ? 1 : -1)));
        hits.querySelectorAll(".hit[data-i]").forEach((el, i) =>
          el.classList.toggle("on", i === hitIndex));
      } else if (ev.key === "Enter") { ev.preventDefault(); pickHit(hitIndex); }
      else if (ev.key === "Escape") { hits.hidden = true; input.blur(); }
    });
    addEventListener("pointerdown", ev => {
      if (!hits.contains(ev.target) && ev.target !== input) hits.hidden = true;
    });
    return input;
  }
  const mastInput = searchBox(mount.querySelector(".pt-search-mast"));
  addEventListener("keydown", ev => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault(); mastInput.focus(); mastInput.select();
    }
  });

  /* ---------------- the start page ---------------- */
  async function renderStart(gen) {
    currentDoc = null;
    document.title = "paraTEXT — svensk rätt att läsa";
    page.innerHTML = '<div class="hero"><div class="eyebrow">Sveriges lagar, '
      + 'med kontext</div><h1>Sök, bläddra och läs svensk rätt</h1>'
      + '<p class="tagline">Lagar, förarbeten, rättsfall, EU-rätt och folkrätt, '
      + 'lästa live ur <a href="https://lagen.nu/docs" target="_blank" '
      + 'rel="noopener">lagen.nu:s öppna API</a> — med hänvisningarna till '
      + 'varje bestämmelse bredvid texten.</p>'
      + '<form class="pt-search pt-search-hero" role="search" autocomplete="off">'
      + '<input type="search" spellcheck="false" autocomplete="off" '
      + 'placeholder="Sök lag, paragraf, rättsfall — avtalslagen 36 §, BrB 3:1, GDPR art 32 …" '
      + 'aria-label="Sök"><div class="pt-hits" hidden></div></form>'
      + '<div class="examples">t.ex. '
      + [["#/1915:218#P36", "avtalslagen 36 §"], ["#/1962:700", "brottsbalken"],
         ["#/dom/nja/2013s502", "NJA 2013 s. 502"],
         ["#/celex/32016R0679", "GDPR"], ["#/sok?q=uppsägning+av+personliga+skäl",
                                          "”uppsägning av personliga skäl”"]]
        .map(([h, t]) => '<a href="' + h + '">' + esc(t) + "</a>").join("")
      + "</div></div>"
      + '<h2 class="dom-h">Källor</h2><ul class="sources">' + LOADING + "</ul>";
    searchBox(page.querySelector(".pt-search-hero"));
    setCard(cardL, "Innehåll", '<div class="pt-empty">Välj en källa att '
      + "bläddra i, eller sök.</div>");
    setCard(cardR, "Kontext", '<div class="pt-empty">Kontexten — vilka '
      + "rättsfall, förarbeten och andra dokument som hänvisar till det du "
      + "läser — fylls i när ett dokument är öppet.</div>");
    let sources;
    try {
      sources = await api("/api/v1/sources");
    } catch (err) {
      if (gen !== generation) return;
      page.querySelector(".sources").outerHTML = errorHtml(err, "Källförteckningen");
      return;
    }
    if (gen !== generation) return;
    const counts = new Map(sources.map(s => [s.source, s.documents]));
    const order = BROWSABLE.concat(sources.map(s => s.source)
                                   .filter(s => !BROWSABLE.includes(s)));
    page.querySelector(".sources").innerHTML = order
      .filter(s => counts.get(s))
      .map(s => {
        // a source with no facet scheme has no /browse tree, so its card opens
        // the search screen for it instead. Not a wildcard search: /search runs
        // `simple_query_string` with default_operator=and, and `q=*` matches
        // nothing at all (measured against the live API).
        const browse = BROWSABLE.includes(s);
        const href = browse ? browseHash(s) : "#/sok?source=" + encodeURIComponent(s);
        return '<li><a href="' + href + '"><span class="dot" style="--c:'
          + colorOf(groupOf(s, "")) + '"></span><span class="nm">'
          + esc(SOURCE_LABEL[s] || s) + '<span class="how">'
          + (browse ? "bläddra" : "sök") + '</span></span><span class="cnt">'
          + fmt(counts.get(s)) + "</span></a></li>";
      }).join("");
  }

  /* ---------------- search ---------------- */
  async function renderSearch(gen, params) {
    currentDoc = null;
    const q = (params.get("q") || "").trim();
    const filters = { source: params.get("source") || "",
                      kind: params.get("kind") || "", year: params.get("year") || "",
                      sort: params.get("sort") || "", cursor: params.get("cursor") || "" };
    document.title = q + " — sök — paraTEXT";
    mastInput.value = q;
    if (!q) {
      // the screen a source with no browse tree lands on: say why there is no
      // listing, and hand the reader the search box rather than an empty page
      if (!filters.source) { go("#/", true); return; }
      const label = SOURCE_LABEL[filters.source] || filters.source;
      document.title = label + " — sök — paraTEXT";
      page.innerHTML = '<div class="results-h"><div class="eyebrow"><span class="dot" style="--c:'
        + colorOf(groupOf(filters.source, "")) + '"></span>' + esc(label) + "</div></div>"
        + "<h1>" + esc(label) + "</h1>"
        + '<p class="pt-empty">Den här källan har ingen bläddringsvy i API:et — '
        + "den går att nå genom sökning. Skriv en fråga i sökrutan ovan.</p>";
      setCard(cardL, "Filter", '<div class="pt-empty">Filtren visas när en sökning är gjord.</div>');
      setCard(cardR, "Sökning", "");
      mastInput.focus();
      return;
    }
    page.innerHTML = '<div class="results-h"><h1>”' + esc(q) + "”</h1></div>" + LOADING;
    setCard(cardL, "Filter", '<div class="pt-empty">…</div>');
    setCard(cardR, "Sökning", "");
    const qp = new URLSearchParams({ q: q, limit: String(SEARCH_PAGE) });
    for (const k of ["source", "kind", "year", "sort", "cursor"])
      if (filters[k]) qp.set(k, filters[k]);
    let data;
    try {
      data = await api("/api/v1/search", qp.toString());
    } catch (err) {
      if (gen !== generation) return;
      page.innerHTML = '<div class="results-h"><h1>”' + esc(q) + "”</h1></div>"
        + errorHtml(err, "Sökningen");
      return;
    }
    if (gen !== generation) return;
    const sortBtn = (v, label) => '<button type="button" data-sort="' + v + '"'
      + ((filters.sort || "relevance") === v ? ' class="on"' : "") + ">" + label + "</button>";
    const chips = [];
    const facetLabel = (facet, value) => {
      const b = (data.facets[facet] || []).find(x => x.value === value);
      return b && b.label ? b.label : value;
    };
    for (const k of ["source", "kind", "year"])
      if (filters[k])
        chips.push('<a class="chip" href="' + searchHash(q, { ...filters, cursor: "", [k]: "" })
          + '">' + esc(facetLabel(k, filters[k])) + '<span class="x">×</span></a>');
    page.innerHTML = '<div class="results-h"><h1>”' + esc(q) + '”</h1>'
      + '<span class="n">' + fmt(data.total) + " träffar</span>"
      + '<span class="sort" role="group" aria-label="Sortering">'
      + sortBtn("relevance", "Relevans") + sortBtn("citations", "Mest citerade")
      + "</span></div>"
      + (chips.length ? '<div class="chips">' + chips.join("") + "</div>" : "")
      + (data.results.length ? '<ol class="hitlist">' + data.results.map(hitHtml).join("") + "</ol>"
         : '<p class="pt-empty">Inga träffar.</p>')
      + '<div class="pager">'
      + (filters.cursor ? '<a class="btn" href="' + searchHash(q, { ...filters, cursor: "" }) + '">« Första sidan</a>' : "")
      + (data.next_cursor ? '<a class="btn primary" href="' + searchHash(q, { ...filters, cursor: data.next_cursor }) + '">Nästa sida »</a>' : "")
      + '<span class="n">' + fmt(data.results.length) + " av " + fmt(data.total) + "</span></div>";
    page.querySelectorAll("[data-sort]").forEach(b => b.addEventListener("click", () =>
      go(searchHash(q, { ...filters, cursor: "", sort: b.dataset.sort === "relevance" ? "" : b.dataset.sort }))));
    // the facets: each bucket's count is computed against the *other* filters,
    // so the numbers stay usable for widening
    const facetList = (facet, heading) => {
      const buckets = data.facets[facet] || [];
      if (!buckets.length) return "";
      return "<h3>" + heading + "</h3><ul>" + buckets.map(b =>
        '<li><a href="' + searchHash(q, { ...filters, cursor: "", [facet]: filters[facet] === b.value ? "" : b.value })
        + '"' + (filters[facet] === b.value ? ' class="on"' : "") + '><span class="nm">'
        + esc(b.label || b.value) + '</span><span class="cnt">' + fmt(b.count)
        + "</span></a></li>").join("") + "</ul>";
    };
    setCard(cardL, "Filter", '<div class="facet">' + facetList("source", "Källa")
      + facetList("kind", "Dokumenttyp") + facetList("year", "År") + "</div>");
    setCard(cardR, "Sökning", '<div class="ctx"><div class="g-nums"><div><b>'
      + fmt(data.total) + "</b><span>träffar</span></div>"
      + (data.facets.source ? "<div><b>" + fmt(data.facets.source.length) + "</b><span>källor</span></div>" : "")
      + "</div><p class=\"g-key\">En träff som läses som en hänvisning — ”avtalslagen 36 §”, "
      + "”BrB 3:1”, ”GDPR art 32” — löses ut mot samlingen och läggs först, markerad.</p>"
      + '<div class="g-actions"><a href="' + esc(API + "/api/v1/search?" + qp.toString())
      + '" target="_blank" rel="noopener">Svaret som JSON ↗</a></div></div>');
  }
  function hitHtml(res) {
    const pinned = res.score == null && res.pin;
    const target = hitTarget(res);
    const group = groupOf(res.source, res.kind);
    return '<li class="hit' + (pinned ? " pinned" : "") + '"><div class="hit-h">'
      + '<span class="dot" style="--c:' + colorOf(group) + '" title="' + esc(group) + '"></span>'
      + '<a class="t" href="' + docHash(target) + '" data-uri="' + esc(target) + '">'
      + esc(hitName(res)) + "</a>"
      + (res.identifier && res.identifier !== hitName(res) ? '<span class="id">' + esc(res.identifier) + "</span>" : "")
      + '<span class="id">' + esc(res.kind_label || SOURCE_LABEL[res.source] || res.source || "") + "</span>"
      + (res.inbound_count ? '<span class="cited" title="hänvisningar till dokumentet">↞ ' + fmt(res.inbound_count) + "</span>" : "")
      + "</div>"
      + (res.pin ? '<p class="pin"><a href="' + docHash(res.pin.uri) + '" data-uri="' + esc(res.pin.uri) + '">'
         + esc(res.pin.label || res.pin.pinpoint) + "</a>"
         + (res.pin.highlight && res.pin.highlight.length ? ' <span class="q">' + hl(res.pin.highlight[0]) + "</span>" : "")
         + "</p>" : "")
      + (res.highlight && res.highlight.length ? '<p class="snip">' + res.highlight.slice(0, 2).map(hl).join(" … ") + "</p>" : "")
      + (res.fragments && res.fragments.length ? '<ul class="frags">' + res.fragments.slice(0, 4).map(f =>
         '<li><a href="' + docHash(f.uri) + '" data-uri="' + esc(f.uri) + '">' + esc(f.label || f.pinpoint || "") + "</a>"
         + (f.highlight && f.highlight.length ? ' <span class="q">' + hl(f.highlight[0]) + "</span>" : "") + "</li>").join("") + "</ul>" : "")
      + "</li>";
  }

  /* ---------------- browse ---------------- */
  async function renderBrowse(gen, rest, params) {
    currentDoc = null;
    const segs = rest.split("/").filter(Boolean);
    const source = segs[0], slugs = segs.slice(1);
    const offset = Math.max(0, parseInt(params.get("offset"), 10) || 0);
    const label = SOURCE_LABEL[source] || source;
    document.title = label + " — bläddra — paraTEXT";
    page.innerHTML = '<div class="browse-h"><div class="eyebrow">Bläddra</div><h1>'
      + esc(label) + "</h1></div>" + LOADING;
    setCard(cardL, label, LOADING);
    setCard(cardR, "Bläddra", "");
    // the navigator first: it says which leaf is the landing bucket
    let nav;
    try {
      nav = await api("/api/v1/browse", "source=" + encodeURIComponent(source));
    } catch (err) {
      if (gen !== generation) return;
      page.innerHTML = '<div class="browse-h"><h1>' + esc(label) + "</h1></div>"
        + errorHtml(err, "Bläddringen av " + label);
      setCard(cardL, label, "");
      return;
    }
    if (gen !== generation) return;
    // `default` is a key path; the bucket parameter wants slugs
    let path = slugs.slice();
    if (!path.length) {
      let nodes = nav.buckets;
      for (const key of nav.default) {
        const n = nodes.find(b => b.key === key);
        if (!n) break;
        path.push(n.slug);
        nodes = n.children || [];
      }
    }
    // walk the tree along the slug path; a top-level bucket with children and
    // no second slug lands on its first child
    const chain = [];
    let nodes = nav.buckets;
    for (const slug of path) {
      const n = nodes.find(b => b.slug === slug);
      if (!n) break;
      chain.push(n);
      nodes = n.children || [];
    }
    while (chain.length && chain[chain.length - 1].children && chain[chain.length - 1].children.length) {
      const child = chain[chain.length - 1].children[0];
      chain.push(child);
    }
    const leaf = chain[chain.length - 1];
    setCard(cardL, label, navigatorHtml(nav, chain, source));
    if (!leaf) {
      page.innerHTML = '<div class="browse-h"><h1>' + esc(label) + "</h1></div>"
        + '<p class="pt-empty">Ingen sådan avdelning.</p>';
      return;
    }
    const bucket = chain.map(n => n.slug).join("/");
    const heading = chain.map(n => n.label).join(" · ");
    let data;
    try {
      data = await api("/api/v1/browse", "source=" + encodeURIComponent(source)
        + "&bucket=" + encodeURIComponent(bucket) + "&offset=" + offset
        + "&limit=" + BROWSE_PAGE);
    } catch (err) {
      if (gen !== generation) return;
      page.innerHTML = '<div class="browse-h"><h1>' + esc(heading) + "</h1></div>"
        + errorHtml(err, "Listan");
      return;
    }
    if (gen !== generation) return;
    // the leaf with documents is the one on the requested path
    let node = null, list = data.buckets;
    for (const slug of chain.map(n => n.slug)) {
      node = list.find(b => b.slug === slug);
      if (!node) break;
      list = node.children || [];
    }
    const docs = (node && node.documents) || [];
    const total = data.total || 0;
    const pager = '<div class="pager">'
      + (offset > 0 ? '<a class="btn" href="' + browseHash(source, chain.map(n => n.slug), Math.max(0, offset - BROWSE_PAGE)) + '">« Föregående</a>' : "")
      + (offset + docs.length < total ? '<a class="btn primary" href="' + browseHash(source, chain.map(n => n.slug), offset + BROWSE_PAGE) + '">Nästa »</a>' : "")
      + '<span class="n">' + fmt(offset + 1) + "–" + fmt(offset + docs.length) + " av " + fmt(total) + "</span></div>";
    page.innerHTML = '<div class="browse-h"><div class="eyebrow"><span class="dot" style="--c:'
      + colorOf(groupOf(source, "")) + '"></span>' + esc(label) + "</div><h1>" + esc(heading)
      + "</h1></div>" + listingHtml(source, docs) + (total > BROWSE_PAGE ? pager : "");
    setCard(cardR, "Bläddra", '<div class="ctx"><div class="g-nums"><div><b>' + fmt(total)
      + "</b><span>dokument i<br>" + esc(leaf.label) + "</span></div><div><b>"
      + fmt(nav.buckets.reduce((s, b) => s + b.count, 0)) + "</b><span>i hela<br>källan</span></div></div>"
      + '<div class="g-actions"><a href="' + esc(API + "/api/v1/browse?source=" + source + "&bucket=" + bucket)
      + '" target="_blank" rel="noopener">Svaret som JSON ↗</a></div></div>');
  }
  function navigatorHtml(nav, chain, source) {
    const top = chain[0], second = chain[1];
    let html = '<div class="nav"><h3>' + esc(nav.levels[0] || "Avdelning") + "</h3><ul>"
      + nav.buckets.map(b =>
        '<li><a href="' + browseHash(source, [b.slug]) + '"' + (top && top.slug === b.slug ? ' class="on"' : "")
        + '><span class="nm">' + esc(b.label) + '</span><span class="cnt">' + fmt(b.count) + "</span></a></li>").join("")
      + "</ul>";
    if (top && top.children && top.children.length)
      html += "<h3>" + esc(nav.levels[1] || "") + '</h3><ul class="years">'
        + top.children.map(c =>
          '<li><a href="' + browseHash(source, [top.slug, c.slug]) + '"' + (second && second.slug === c.slug ? ' class="on"' : "")
          + '><span class="nm">' + esc(c.label) + "</span></a></li>").join("") + "</ul>";
    return html + "</div>";
  }
  const VARIANT_LABEL = { dom: "Domar", referat: "Referat", notis: "Notiser",
    ep: "Europaparlamentet och rådet", council: "Rådet", commission: "Kommissionen",
    other: "Övriga", untitled: "Utan titel", cj: "Domstolen", gc: "Tribunalen",
    cst: "Personaldomstolen", current: "Gällande lydelse", amending: "Ändringsfördrag",
    accession: "Anslutningsfördrag", withdrawal: "Utträdesavtal" };
  function listingHtml(source, docs) {
    if (!docs.length) return '<p class="pt-empty">Inga dokument.</p>';
    if (source === "begrepp")
      return '<div class="listing"><div class="terms">' + docs.map(d =>
        '<a href="' + docHash(d.uri) + '" data-uri="' + esc(d.uri) + '"'
        + (d.short_title ? ' class="described"' : "") + ">" + esc(d.short_id || d.display) + "</a>").join("") + "</div></div>";
    const row = d => {
      const split = d.key != null;
      const cls = "row" + (split ? " split" : "") + (d.subdued ? " subdued" : "");
      const name = d.short_title, desc = d.description;
      const text = name && desc ? name + ": " + desc : (desc || name || "");
      let html = '<div class="' + cls + '">';
      if (split)
        html += '<a class="t" href="' + docHash(d.uri) + '" data-uri="' + esc(d.uri) + '">'
          + (d.pre ? '<span class="pre">' + esc(d.pre) + "</span> " : "") + esc(d.key || d.display) + "</a>";
      else
        html += '<a class="id" href="' + docHash(d.uri) + '" data-uri="' + esc(d.uri) + '">'
          + esc(caseName(d.short_id || d.display)) + '</a><span class="d">' + esc(caseName(text))
          + (d.date ? ' <span class="where">' + esc(d.date) + "</span>" : "") + "</span>";
      if (d.consolidated) html += '<span class="sub">konsoliderad version</span>';
      if (d.amendments && d.amendments.length)
        html += '<span class="sub">ändringar: ' + d.amendments.map(a =>
          '<a href="' + docHash(a.uri) + '" data-uri="' + esc(a.uri) + '">' + esc(a.short_id || a.display) + "</a>").join("") + "</span>";
      return html + "</div>";
    };
    // a listing with several variants groups them under headings
    const variants = [...new Set(docs.map(d => d.variant).filter(Boolean))];
    if (variants.length > 1)
      return '<div class="listing">' + variants.map(v =>
        '<div class="group">' + esc(VARIANT_LABEL[v] || v) + "</div>"
        + docs.filter(d => d.variant === v).map(row).join("")).join("")
        + docs.filter(d => !d.variant).map(row).join("") + "</div>";
    return '<div class="listing">' + docs.map(row).join("") + "</div>";
  }

  /* ---------------- the document ---------------- */
  let currentDoc = null;         // the /document payload on screen
  let docInbound = null;         // the document-level inbound rows, by unit
  let selectedUnit = null;       // the unit anchor the context card stands for
  let followScroll = true;
  async function renderDoc(gen, uri, anchor, params) {
    const same = currentDoc && currentDoc.uri === uri;
    if (!same) {
      currentDoc = null; docInbound = null; selectedUnit = null; reading = false;
      page.innerHTML = LOADING;
      setCard(cardL, "Innehåll", "");
      setCard(cardR, "Kontext", "");
      let data;
      try {
        data = await api("/api/v1/document", "uri=" + encodeURIComponent(uri));
      } catch (err) {
        if (gen !== generation) return;
        page.innerHTML = '<div class="eyebrow">Dokument</div><h1>' + esc(pathOf(uri)) + "</h1>"
          + errorHtml(err, "Dokumentet")
          + (err.status === 404 ? '<p><a href="' + searchHash(pathOf(uri).replace(/^\//, "")) + '">Sök efter det i stället →</a></p>' : "");
        return;
      }
      if (gen !== generation) return;
      currentDoc = data;
      drawDoc(data);
      syncMastNav(parseHash());
      loadVersions(gen, data);
      loadDocInbound(gen, data);
    }
    const diff = params.get("diff");
    if (diff) showDiff(gen, uri, diff);
    else { const old = page.querySelector(".diff"); if (old) old.remove(); }
    if (anchor) {
      const el = document.getElementById(anchor);
      if (el) {
        // the unit the anchor lives in is what the context card answers for
        const unit = el.closest("[data-unit]");
        el.scrollIntoView({ block: "start" });
        programmaticScroll();
        selectUnit(unit ? unit.dataset.unit : null, true);
      } else selectUnit(null, true);
    } else if (!same) {
      scrollTo(0, 0);
      programmaticScroll();
      selectUnit(null, true);
    }
  }

  /* -- inline runs: a text value is a list of strings and link dicts -- */
  function runs(text) {
    if (text == null) return "";
    if (typeof text === "string") return esc(text);
    return text.map(r => {
      if (typeof r === "string") return esc(r);
      const label = esc(r.text || "");
      if (!r.uri) return label;
      const cls = r.kind === "term" || r.predicate === "dcterms:subject" ? ' class="term"' : "";
      return '<a href="' + docHash(r.uri) + '" data-uri="' + esc(r.uri) + '"' + cls + ">" + label + "</a>";
    }).join("");
  }
  const plain = text => typeof text === "string" ? text
    : (text || []).map(r => typeof r === "string" ? r : (r.text || "")).join("");

  /* -- the walk: pure functions over the artifact tree, dispatching on node
   * `type` only, never on which source it came from (the same contract as
   * lib/mdtext.py). A type the walk does not know renders through the
   * generic rule: its runs as a paragraph, then its children. -- */
  const toc = [];                      // [{anchor, label, level}]
  const unitToc = [];                  // the units, for a document with no headings
  let lastPage = null;                 // förarbete page markers
  const idAttr = n => n.id ? ' id="' + esc(n.id) + '"' : "";
  function pageMarker(n) {
    if (n.page == null || n.page === lastPage) return "";
    lastPage = n.page;
    return '<span class="sid" id="sid' + esc(n.page) + '">Sida ' + esc(n.page) + "</span>";
  }
  function walk(nodes, depth) {
    return (nodes || []).map(n => n && typeof n === "object" ? block(n, depth) : "").join("");
  }
  function heading(level, n, extra) {
    const lvl = Math.max(2, Math.min(6, level));
    const text = plain(n.text).trim();
    if (n.id && text) toc.push({ anchor: n.id, label: text, level: lvl - 1 });
    return "<h" + lvl + idAttr(n) + ' class="rubrik' + (extra ? " " + extra : "") + '">' + runs(n.text) + "</h" + lvl + ">";
  }
  // a unit is what the context card answers for: a §, an article, a numbered
  // paragraph of a judgment. It hangs its numeral in the gutter.
  function unit(n, kind, numeral, titleHtml, inner) {
    const id = n.id || "";
    if (id && numeral && kind !== "punkt-nr") unitToc.push({ anchor: id, label: numeral });
    return '<section class="unit ' + kind + '"' + idAttr(n) + (id ? ' data-unit="' + esc(id) + '"' : "") + ">"
      + '<div class="gutter"><span class="n" title="Visa kontext">'
      + (kind === "artikel" ? '<span class="k">Art.</span>' : "") + esc(numeral) + "</span>"
      + (id ? '<a class="pilcrow" href="' + docHash(currentDoc.uri + "#" + id) + '" aria-label="Permalänk">¶</a>' : "")
      + '</div><div class="body">' + (titleHtml ? '<div class="unit-title">' + titleHtml + "</div>" : "")
      + inner + "</div></section>";
  }
  function temporal(n) {
    let out = "";
    if (n.ikrafttrader) out += '<p class="temporal">/Träder i kraft ' + esc(n.ikrafttrader) + "/</p>";
    if (n.upphor) out += '<p class="temporal">/Upphör att gälla ' + esc(n.upphor) + "/</p>";
    return out;
  }
  function table(n) {
    const rows = (n.children || []).filter(r => r && r.cells);
    if (!rows.length) return "";
    return '<div class="tabell-wrap"><table>' + (plain(n.text).trim() ? "<caption>" + runs(n.text) + "</caption>" : "")
      + rows.map(r => {
        const tag = r.th ? "th" : "td";
        return "<tr" + idAttr(r) + ">" + r.cells.map((c, i) => {
          const rs = r.rowspan && r.rowspan[i] > 1 ? ' rowspan="' + r.rowspan[i] + '"' : "";
          const cs = r.colspan && r.colspan[i] > 1 ? ' colspan="' + r.colspan[i] + '"' : "";
          return "<" + tag + rs + cs + ">" + runs(c) + "</" + tag + ">";
        }).join("") + "</tr>";
      }).join("") + "</table></div>";
  }
  const DV_LABEL = { domskal: "Domskäl", domslut: "Domslut", betankande: "Betänkande",
    skiljaktig: "Skiljaktig mening", tillagg: "Tillägg", yrkanden: "Yrkanden",
    bakgrund: "Bakgrund", dom: "", delmal: "" };
  function block(n, depth) {
    const t = n.type;
    const body = runs(n.text);
    const kids = n.children;
    const pg = pageMarker(n);
    if (t === "rubrik") return pg + (plain(n.text).trim() ? heading((n.level || 1) + 1, n) : "");
    if (t === "avsnitt" || t === "sektion") {
      const level = n.level || 1;
      const head = plain(n.text).trim() ? heading(level + 1, n, n.num ? "numrerad" : "") : "";
      return pg + '<section class="' + t + '"' + (head ? "" : idAttr(n)) + ">" + head + walk(kids, level + 2) + "</section>";
    }
    if (t === "heading") {                // eurlex division: label + title
      const level = n.level || 1;
      const text = plain(n.text).trim();
      const label = decap(n.label);
      if (n.id && (label || text)) toc.push({ anchor: n.id, label: [label, text].filter(Boolean).join(" – "), level: level });
      return "<h" + Math.min(6, level + 1) + idAttr(n) + ' class="rubrik">'
        + (label ? '<span class="rubrik-nr">' + esc(label) + "</span>" : "") + body
        + "</h" + Math.min(6, level + 1) + ">" + walk(kids, depth + 1);
    }
    if (t === "article") {                // eurlex: label "Artikel 5", text = title
      const num = String(n.num || n.id || "");
      const title = plain(n.text).trim();
      if (n.id) toc.push({ anchor: n.id, label: "Artikel " + num + (title ? " – " + title : ""), level: 3 });
      return unit(n, "artikel", num, title ? body : "", walk(kids, depth + 1));
    }
    if (t === "artikel") {                // coe: text is the full printed heading
      const title = plain(n.text).trim() || "Artikel " + (n.ordinal || "");
      if (n.id) toc.push({ anchor: n.id, label: title, level: 3 });
      return unit(n, "artikel", String(n.ordinal || ""), body, walk(kids, depth + 1));
    }
    if (t === "kapitel" || t === "avdelning") {
      // an SFS container carries no text (its rubrik child is the heading); a
      // förarbete lagtext block carries its printed marker as text
      if (!kids || !kids.length) return pg + (body ? '<p class="marker"><b>' + body + "</b></p>" : "");
      let inner = temporal(n);
      const first = kids[0];
      let rest = kids;
      if (first && first.type === "rubrik") {
        const text = plain(first.text).trim();
        const anchor = n.id || first.id;
        if (anchor && text) toc.push({ anchor: anchor, label: text, level: t === "avdelning" ? 1 : 1 });
        inner += "<h2" + (anchor ? ' id="' + esc(anchor) + '"' : "") + ' class="kaprubrik">' + runs(first.text) + "</h2>";
        rest = kids.slice(1);
        return pg + '<section class="' + t + '">' + inner + walk(rest, depth) + "</section>";
      }
      return pg + '<section class="' + t + '"' + idAttr(n) + ">" + inner + (body ? "<p><b>" + body + "</b></p>" : "") + walk(rest, depth) + "</section>";
    }
    if (t === "paragraf") {
      if (!kids || !kids.length) return pg + (body ? "<p" + idAttr(n) + "><b>" + body + "</b></p>" : "");
      // the numeral hangs in the gutter; the first stycke's beteckning is it
      const first = kids[0];
      const numeral = (first && first.beteckning) || (n.ordinal ? n.ordinal + " §" : "");
      const inner = temporal(n) + (body ? "<p><b>" + body + "</b></p>" : "")
        + kids.map((k, i) => i === 0 && k.beteckning ? block({ ...k, beteckning: null }, depth) : block(k, depth)).join("");
      return pg + unit(n, "paragraf", numeral, "", inner);
    }
    if (t === "stycke") {
      let out = "";
      if (n.beteckning) out = '<span class="num">' + esc(n.beteckning) + "</span> ";
      else if (n.ordinal != null && n.ordinal !== "") {   // dv/hudoc numbered paragraphs
        const id = n.id || "P" + n.ordinal;
        return pg + unit({ ...n, id: id }, "punkt-nr", String(n.ordinal), "", "<p>" + body + "</p>" + walk(kids, depth));
      } else if (n.num != null) out = '<span class="num">' + esc(n.num) + "</span> ";
      return pg + (body || out ? "<p" + idAttr(n) + (n.redaktionell ? ' class="redaktionell"' : "") + ">" + out + body + "</p>" : "")
        + walk(kids, depth);
    }
    if (t === "lista") {
      const items = (kids || []).filter(k => k && k.type === "punkt");
      const numbered = items.length && items.every(k => String(k.ordinal || "").match(/^\d+$/));
      const others = (kids || []).filter(k => k && k.type !== "punkt");
      return pg + (numbered ? "<ol" + idAttr(n) + ">" : '<ul class="punkter"' + idAttr(n) + ">")
        + items.map(k => block(k, depth)).join("") + (numbered ? "</ol>" : "</ul>") + walk(others, depth);
    }
    if (t === "punkt") {
      const ord = String(n.ordinal || "");
      const marker = ord.match(/^\d+$/) ? "" : ord ? '<span class="num">' + esc(ord) + ")</span> " : "";
      return "<li" + idAttr(n) + (ord.match(/^\d+$/) ? ' value="' + ord + '"' : "") + ">" + marker + body + walk(kids, depth) + "</li>";
    }
    if (t === "tabell" || t === "table") return pg + table(n);
    if (t === "rad") return "";
    if (t === "recital") {
      const id = n.id || (n.num != null ? "recital-" + n.num : null);
      return '<p class="recital"' + (id ? ' id="' + esc(id) + '"' : "") + ">"
        + (n.num != null ? '<span class="num">(' + esc(n.num) + ")</span> " : "") + body + "</p>";
    }
    if (t === "paragraph") {              // eurlex numbered article paragraph
      return (body ? "<p" + idAttr(n) + ">" + (n.num != null ? '<span class="num">' + esc(n.num) + ".</span> " : "") + body + "</p>" : "")
        + walk(kids, depth);
    }
    if (t === "point") {
      const num = String(n.num || "");
      const indent = Math.max(0, (n.depth || 1) - 1);
      return '<p class="point"' + idAttr(n) + ' style="margin-left:' + (1.2 + indent * 1.2) + 'rem">'
        + (num ? '<span class="num">' + esc(num) + ")</span> " : "") + body + "</p>" + walk(kids, depth);
    }
    if (t === "citat") {
      const num = String(n.num || "");
      const marker = n.quoted === "recital" ? "(" + num + ") " : n.quoted === "point" ? num + ") " : num ? num + ". " : "";
      return '<blockquote class="citat"' + idAttr(n) + "><p>" + (marker ? '<span class="num">' + esc(marker) + "</span>" : "") + body + "</p>" + walk(kids, depth) + "</blockquote>";
    }
    if (t === "note") return '<p class="fotnot"' + idAttr(n) + ">" + (n.num != null ? "(" + esc(n.num) + ") " : "") + body + "</p>";
    if (t === "ruling") return "<p" + idAttr(n) + ">" + (n.num != null ? '<span class="num">' + esc(n.num) + ".</span> " : "") + body + "</p>";
    if (t === "fotnot") return pg + (body ? '<p class="fotnot"' + idAttr(n) + ">" + body + "</p>" : "");
    if (t === "ruta") return pg + (body ? '<p class="ruta"' + idAttr(n) + ">" + body + "</p>" : "") + walk(kids, depth);
    if (t === "upphavd") return '<p class="upphavd"' + idAttr(n) + ">" + (body || "Har upphävts.") + "</p>";
    if (t === "overgangsbestammelse") return '<section class="ob"' + idAttr(n) + ">" + (body ? "<p>" + body + "</p>" : "") + walk(kids, depth) + "</section>";
    if (t === "keyword") return '<p class="sokord"' + idAttr(n) + ">" + body + "</p>";
    if (t === "preamble") return '<section class="preamble"' + idAttr(n) + ">" + (body ? "<p>" + body + "</p>" : "") + walk(kids, depth) + "</section>";
    if (t === "instans") {
      const court = n.court || plain(n.text).trim();
      const id = n.id || (court ? "instans-" + court.toLowerCase().replace(/[^a-zåäö0-9]+/g, "-") : null);
      if (court && id) toc.push({ anchor: id, label: court, level: 1 });
      return '<section class="instans"' + (id ? ' id="' + esc(id) + '"' : "") + ">"
        + (court ? "<h2>" + esc(court) + "</h2>" : "") + walk(kids, depth) + "</section>";
    }
    if (t in DV_LABEL) {
      const label = DV_LABEL[t] || (n.ordinal ? "Delmål " + n.ordinal : "");
      return '<section class="' + t + '"' + idAttr(n) + ">" + (label ? '<div class="dom-h">' + esc(label) + "</div>" : "")
        + (body ? "<p>" + body + "</p>" : "") + walk(kids, depth) + "</section>";
    }
    if (t === "bild") return "";
    // everything else: its runs as a paragraph, then its children
    const num = n.num != null ? n.num : n.ordinal;
    return pg + (body ? "<p" + idAttr(n) + (t ? ' class="' + esc(t) + '"' : "") + ">"
      + (num != null && num !== "" ? '<span class="num">(' + esc(num) + ")</span> " : "") + body + "</p>" : "")
      + walk(kids, depth);
  }
  function decap(label) {
    if (!label || label !== label.toUpperCase()) return label || "";
    const [word, ...rest] = label.split(" ");
    return [word.charAt(0) + word.slice(1).toLowerCase()].concat(rest).join(" ");
  }
  // the consolidation an artifact presents as its reading text: the latest
  // parsed one, where the source stores several (föreskrift)
  function presentedStructure(art) {
    const cons = (art.consolidations || []).filter(c => c && c.structure);
    if (cons.length)
      return cons.sort((a, b) => String(b.konsolideradTom || "").localeCompare(String(a.konsolideradTom || "")))[0].structure;
    return art.structure || art.body || [];
  }
  /* What the document is called: its own full title, and the printed
   * identifier under it -- but only when the identifier says something the
   * title does not. A court decision's title IS its citing name ("Juniavgörandet
   * (NJA 2013 s. 502)"), and printing it twice spent the head's prime line on a
   * duplicate. */
  function docTitle(data) {
    const art = data.artifact || {};
    const props = (art.metadata && art.metadata.properties) || {};
    const meta = art.metadata || {};
    const title = props["dcterms:title"] || art.title || meta.title || data.title;
    const ident = art.identifier || art.label || data.label;
    if (title && ident && !title.toLowerCase().includes(ident.toLowerCase()))
      return { title: title, ident: ident };
    return { title: title || ident || decodeURI(pathOf(data.uri)), ident: "" };
  }
  function summaryOf(art) {
    const meta = art.metadata || {};
    return art.sammanfattning || art.summary || (meta && meta.sammanfattning) || null;
  }
  // what the head says about the document, per what its artifact carries
  function metaRows(data) {
    const art = data.artifact || {};
    const props = (art.metadata && art.metadata.properties) || {};
    const secondary = (art.metadata && art.metadata.secondary) || {};
    const meta = art.metadata || {};
    const rows = [];
    const add = (k, v) => { if (v != null && v !== "" && !(Array.isArray(v) && !v.length)) rows.push([k, v]); };
    const org = u => (secondary[u] && secondary[u]["rdfs:label"]) || (u ? String(u).split("/").pop() : "");
    if (data.source === "sfs" || data.source === "foreskrift") {
      add("Beteckning", props["dcterms:identifier"] || art.identifier);
      add("Förkortning", props["dcterms:alternate"]);
      add("Utfärdad", props["rpubl:utfardandedatum"] || meta.beslutsdatum);
      add("I kraft", props["rpubl:ikrafttradandedatum"] || meta.ikrafttradandedatum);
      add("Upphävd", props["rpubl:upphavandedatum"]);
      add("Departement", org(props["dcterms:creator"]));
      if (meta.bemyndigande && meta.bemyndigande.length)
        add("Bemyndigande", meta.bemyndigande.map(u => ({ uri: u, text: pathOf(u).replace(/^\//, "").replace("#", " ") })));
    } else if (data.source === "dv") {
      add("Domstol", art.court_namn || art.court);
      add("Målnummer", Array.isArray(art.malnummer) ? art.malnummer.join(", ") : art.malnummer);
      add("Avgörandedatum", art.avgorandedatum);
      add("Referat", art.referat);
      add("Rättsområde", meta.rattsomrade);
      if (meta.lagrum && meta.lagrum.length)
        add("Lagrum", meta.lagrum.map(l => typeof l === "string" ? l : (l.referens || l.sfsnummer || "")).filter(Boolean).join("; "));
      if (meta.forarbeten && meta.forarbeten.length) add("Förarbeten", meta.forarbeten.join("; "));
      if (meta.nyckelord && meta.nyckelord.length) add("Sökord", meta.nyckelord.join(", "));
    } else if (data.source === "eurlex") {
      add("CELEX", art.celex);
      add("Typ", art.doctype);
      add("Datum", art.date);
      add("ECLI", art.ecli);
      add("EUT", art.oj && (typeof art.oj === "string" ? art.oj : JSON.stringify(art.oj)));
      add("Kortnamn", art.shortname || art.abbr);
    } else if (data.source === "forarbete") {
      add("Beteckning", art.identifier);
      add("Typ", data.kind);
      add("Datum", art.date);
    } else if (data.source === "avg") {
      add("Diarienummer", meta.diarienummer);
      add("Beslutsdatum", meta.beslutsdatum);
      add("Avgjort av", meta.avgjordAv);
      add("Utgivare", meta.publisher);
      if (meta.nyckelord && meta.nyckelord.length) add("Sökord", meta.nyckelord.join(", "));
    } else {
      add("Beteckning", art.identifier || data.label);
      add("Typ", art.doctype || data.kind);
      add("Datum", art.date);
      add("ECLI", art.ecli);
    }
    return rows;
  }
  function metaHtml(rows) {
    if (!rows.length) return "";
    return '<dl class="meta">' + rows.map(([k, v]) =>
      "<dt>" + esc(k) + "</dt><dd>" + (Array.isArray(v) ? runs(v) : esc(v)) + "</dd>").join("") + "</dl>";
  }
  function amendmentsHtml(art) {
    const list = art.amendments || [];
    if (!list.length) return "";
    return '<section class="andringar" id="andringar"><h2>Ändringar och övergångsbestämmelser</h2>'
      + list.map(a => {
        const props = a.properties || {};
        const ident = props["dcterms:identifier"] || a.identifier || a.uri || "";
        const anchor = "L" + ident.replace(/^SFS\s+/, "");
        const facts = [["Omfattning", props["rpubl:andrar"]], ["Ikraftträder", props["rpubl:ikrafttradandedatum"]],
                       ["Beslutad", a.beslutsdatum], ["Förarbeten", (a.forarbeten || []).join(", ")],
                       ["CELEX", props["rpubl:celexNummer"]]].filter(f => f[1]);
        return '<div class="andring" id="' + esc(anchor) + '"><h3>'
          + (a.uri ? '<a href="' + docHash(a.uri) + '" data-uri="' + esc(a.uri) + '">' + esc(ident) + "</a>" : esc(ident)) + "</h3>"
          + (facts.length ? '<dl class="facts">' + facts.map(f => "<dt>" + f[0] + "</dt><dd>" + esc(f[1]) + "</dd>").join("") + "</dl>" : "")
          + (a.content ? '<div class="ob">' + walk(a.content, 4) + "</div>" : "") + "</div>";
      }).join("") + "</section>";
  }
  function drawDoc(data) {
    const art = data.artifact || {};
    const group = groupOf(data.source, data.kind);
    const { title, ident } = docTitle(data);
    const shown = upFirst(caseName(title));
    document.title = shown + " — paraTEXT";
    toc.length = 0; unitToc.length = 0; lastPage = null;
    const props = (art.metadata && art.metadata.properties) || {};
    let banners = "";
    if (props["rpubl:upphavandedatum"]) banners += '<div class="banner">Upphävd ' + esc(props["rpubl:upphavandedatum"]) + "</div>";
    const summary = summaryOf(art);
    const bodyHtml = walk(presentedStructure(art), 2);
    const footnotes = (art.footnotes || []).filter(Boolean);
    const empty = !bodyHtml.trim();
    page.innerHTML = '<article class="doc-shell"><div class="eyebrow"><span class="dot" style="--c:'
      + colorOf(group) + '"></span>' + esc(SOURCE_LABEL[data.source] || data.source)
      + (data.kind && data.kind !== data.source ? " · " + esc(data.kind) : "") + "</div>"
      + "<h1" + (data.source === "begrepp" ? "" : "") + ">" + esc(shown)
      + (ident ? "<small>" + esc(ident) + "</small>" : "") + "</h1>"
      + banners + metaHtml(metaRows(data))
      + (summary ? '<p class="summary">' + runs(summary) + "</p>" : "")
      + '<div class="doc" id="top">' + (empty ? '<p class="pt-empty">Dokumentet har ingen text i samlingen'
        + (data.source_url ? ' — <a href="' + esc(data.source_url) + '" target="_blank" rel="noopener">läs hos utgivaren ↗</a>' : "") + ".</p>" : bodyHtml)
      + (footnotes.length ? '<section class="noter"><h2>Noter</h2><ol>' + footnotes.map(f =>
        "<li" + (f.num != null ? ' value="' + esc(f.num) + '" id="fn' + esc(f.num) + '"' : "") + ">" + runs(f.text) + "</li>").join("") + "</ol></section>" : "")
      + amendmentsHtml(art) + "</div></article>";
    // The table of contents card: the document's headings, or -- for a
    // document that prints none, as a flat statute does (räntelagen: nine
    // paragrafer, no rubriker) -- its units, which is what a reader of a short
    // law navigates by anyway.
    const entries = toc.length ? toc
      : unitToc.map(e => ({ anchor: e.anchor, label: e.label, level: 1 }));
    const tocHtml = '<div class="toc"><ul><li><a class="top" href="' + docHash(data.uri) + '">' + esc(data.label || ident || "↑ Början") + "</a></li>"
      + entries.map(e => '<li class="l' + Math.min(3, e.level) + '"><a href="' + docHash(data.uri + "#" + e.anchor) + '">' + esc(e.label) + "</a></li>").join("")
      + (art.amendments && art.amendments.length ? '<li class="l1"><a href="' + docHash(data.uri + "#andringar") + '">Ändringar (' + fmt(art.amendments.length) + ")</a></li>" : "")
      + "</ul></div>" + '<div class="versions" hidden></div>';
    setCard(cardL, "Innehåll", tocHtml);
    // the gutter numerals ask for the unit's context
    page.querySelectorAll("section.unit > .gutter .n").forEach(el =>
      el.addEventListener("click", () => {
        const u = el.closest("[data-unit]");
        if (u) go(docHash(data.uri + "#" + u.dataset.unit), true);
      }));
    observeUnits();
  }
  // the toc entry of the anchor on screen
  function markToc(anchor) {
    bodyL.querySelectorAll(".toc a").forEach(a => {
      const [, frag] = splitFrag(a.getAttribute("href"));
      a.classList.toggle("on", !!anchor && frag === anchor);
    });
  }

  /* -- versions and diffs (statutes and EU acts) -- */
  async function loadVersions(gen, data) {
    if (data.source !== "sfs" && data.source !== "eurlex") return;
    let v;
    try { v = await api("/api/v1/document/versions", "uri=" + encodeURIComponent(data.uri)); }
    catch (err) { return; }           // no versions is the common case
    if (gen !== generation || !v.versions || !v.versions.length) return;
    const slot = bodyL.querySelector(".versions");
    if (!slot) return;
    slot.hidden = false;
    slot.innerHTML = "<h3>Lydelser</h3><select aria-label=\"Tidigare lydelser\"><option value=\"\">Gällande lydelse</option>"
      + v.versions.slice().reverse().map(x => '<option value="' + esc(x.version) + '">' + esc(x.version)
        + (x.ikraft ? " (i kraft " + esc(x.ikraft) + ")" : "") + "</option>").join("")
      + "</select><div class=\"g-key\">Väljs en äldre lydelse visas skillnaden mot den gällande.</div>";
    slot.querySelector("select").addEventListener("change", ev => {
      const val = ev.target.value;
      go("#" + pathOf(data.uri) + (val ? "?diff=" + encodeURIComponent(val) : ""), true);
    });
  }
  async function showDiff(gen, uri, from) {
    const old = page.querySelector(".diff");
    if (old && old.dataset.from === from) return;
    if (old) old.remove();
    const box = document.createElement("div");
    box.className = "diff"; box.dataset.from = from;
    box.innerHTML = LOADING;
    const doc = page.querySelector(".doc");
    if (!doc) return;
    doc.parentNode.insertBefore(box, doc);
    try {
      const html = await api("/api/v1/document/diff", "uri=" + encodeURIComponent(uri) + "&from=" + encodeURIComponent(from), true);
      if (gen !== generation) return;
      box.innerHTML = '<div class="diff-note">Skillnad: lydelsen ' + esc(from) + " → gällande lydelse</div>" + html;
      doc.hidden = true;
    } catch (err) {
      if (gen !== generation) return;
      box.innerHTML = errorHtml(err, "Jämförelsen");
    }
  }

  /* ---------------- context: who cites this ----------------
   * The document-level inbound answer (scope=tree) carries every row's
   * `target`, so one call gives the whole document a per-unit citation map:
   * the gutter badges, and the context card for the unit in view as the
   * reader scrolls. Selecting a unit (its numeral, a pinned search hit, a
   * #anchor) asks the exact question for it -- ranked, complete -- once. */
  async function loadDocInbound(gen, data) {
    let inb;
    try {
      inb = await api("/api/v1/document/inbound", "uri=" + encodeURIComponent(data.uri) + "&limit=" + INBOUND_PAGE);
    } catch (err) {
      if (gen !== generation) return;
      docInbound = { error: err };
      if (!selectedUnit) showDocContext(data);
      return;
    }
    if (gen !== generation) return;
    // rows by the unit they land in: the rendered element for the target's
    // anchor, walked up to its unit
    const byUnit = new Map();
    for (const row of inb.citations) {
      const [, frag] = splitFrag(row.target);
      let key = "";
      if (frag) {
        const el = document.getElementById(frag);
        const u = el && el.closest("[data-unit]");
        key = u ? u.dataset.unit : frag;
      }
      if (!byUnit.has(key)) byUnit.set(key, []);
      byUnit.get(key).push(row);
    }
    docInbound = { total: inb.total, by_source: inb.by_source, rows: inb.citations,
                   byUnit: byUnit, partial: inb.total > inb.citations.length };
    for (const [key, rows] of byUnit) {
      if (!key) continue;
      const u = page.querySelector('[data-unit="' + CSS.escape(key) + '"] > .gutter');
      if (!u) continue;
      const n = new Set(rows.map(r => r.uri)).size;
      const b = document.createElement("span");
      b.className = "ctx-n"; b.title = "dokument som hänvisar hit";
      b.textContent = "↞" + fmt(n) + (docInbound.partial ? "+" : "");
      b.addEventListener("click", () => go(docHash(data.uri + "#" + key), true));
      u.appendChild(b);
    }
    if (!selectedUnit) showDocContext(data);
    else if (!pinnedUnit) showUnitContext(selectedUnit, false);
  }
  /* Whether the reader has started reading. Until they scroll, the card
   * stands for the whole document -- opening a statute and having the card
   * immediately answer for its 1 § says something narrower than the reader
   * asked. Their first real scroll hands the card over to the text, and also
   * releases a pinned unit (a deep link, a gutter click): from then on they
   * are reading, and the card follows. */
  let reading = false, pinnedUnit = false;
  let ignoreScrollUntil = 0, lastScrollY = 0;
  function programmaticScroll() {    // our own scrolling is not the reader's
    ignoreScrollUntil = performance.now() + 700;
    lastScrollY = scrollY;
  }
  addEventListener("scroll", () => {
    if (performance.now() < ignoreScrollUntil || Math.abs(scrollY - lastScrollY) < 60) {
      lastScrollY = scrollY;
      return;
    }
    lastScrollY = scrollY;
    reading = true;
    pinnedUnit = false;
  }, { passive: true });
  function selectUnit(anchor, pinned) {
    selectedUnit = anchor;
    pinnedUnit = !!anchor && pinned;
    page.querySelectorAll("section.unit.on").forEach(el => el.classList.remove("on"));
    if (anchor) {
      const el = page.querySelector('[data-unit="' + CSS.escape(anchor) + '"]');
      if (el) el.classList.add("on");
    }
    markToc(anchor || null);
    if (!currentDoc) return;
    if (anchor) showUnitContext(anchor, pinned);
    else showDocContext(currentDoc);
  }
  // one row per citing document (a proposition cites the same paragraf from
  // many places), the count of its places kept
  function foldRows(rows) {
    const seen = new Map();
    for (const r of rows) {
      const prev = seen.get(r.uri);
      if (prev) { prev.n += 1; continue; }
      seen.set(r.uri, { ...r, n: 1 });
    }
    return [...seen.values()];
  }
  /* One citing document as a row. What it carries, and what it deliberately
   * does not: the citing spot's page number names a place this row does not
   * take you to, and the full date spent the width the case name needs, so the
   * row states the year. */
  function ctxRow(r) {
    const name = upFirst(caseName(r.label || r.title || pathOf(r.uri)));
    const title = upFirst(caseName(r.title || ""));
    const year = r.date ? String(r.date).slice(0, 4) : "";
    return '<li><a href="' + docHash(r.uri) + '" data-uri="' + esc(r.uri) + '"><span class="lb">' + esc(name) + "</span></a>"
      + (title && title.toLowerCase() !== name.toLowerCase()
         ? '<span class="ti" title="' + esc(title) + '">' + esc(title) + "</span>" : "")
      + (r.n > 1 ? '<span class="where" title="ställen i dokumentet">×' + fmt(r.n) + "</span>" : "")
      + (year ? '<span class="where">' + esc(year) + "</span>" : "")
      + (r.inbound_count > 0 ? '<span class="cited" title="hänvisningar till dokumentet">↞' + fmt(r.inbound_count) + "</span>" : "")
      + "</li>";
  }
  function sectionsHtml(rows, bySource) {
    const groups = new Map();
    for (const r of foldRows(rows)) {
      const s = r.source || "övrigt";
      if (!groups.has(s)) groups.set(s, []);
      groups.get(s).push(r);
    }
    const order = [...groups.keys()].sort((a, b) =>
      (RAIL_ORDER.indexOf(a) + 1 || 99) - (RAIL_ORDER.indexOf(b) + 1 || 99));
    return order.map((s, i) => {
      const list = groups.get(s);
      const total = bySource && bySource[s];
      return "<details" + (i === 0 ? " open" : "") + '><summary><span class="pt-chevron"></span>'
        + '<span class="dot" style="--c:' + colorOf(groupOf(s, list[0].kind)) + '"></span>'
        + esc(RAIL_LABEL[s] || SOURCE_LABEL[s] || s) + '<span class="n">' + fmt(list.length)
        + (total && total > list.length ? " av " + fmt(total) : "") + "</span></summary><ul>"
        + list.slice(0, CTX_CAP).map(ctxRow).join("") + "</ul>"
        + (list.length > CTX_CAP ? '<details class="more"><summary>+ ' + fmt(list.length - CTX_CAP) + " till</summary><ul>"
           + list.slice(CTX_CAP).map(ctxRow).join("") + "</ul></details>" : "")
        + "</details>";
    }).join("");
  }
  function actionsHtml(uri) {
    return '<div class="g-actions"><a class="primary" href="' + esc(GRAF + "/#uri=" + encodeURIComponent(uri))
      + '" target="_blank" rel="noopener">Utforska grafen ↗</a><a href="' + esc(API + pathOf(uri).replace(/#.*$/, "") + (uri.includes("#") ? "#" + splitFrag(uri)[1] : ""))
      + '" target="_blank" rel="noopener">Öppna på lagen.nu ↗</a></div>';
  }
  const followHtml = () => '<label class="follow"><input type="checkbox"' + (followScroll ? " checked" : "")
    + "> kortet följer läsningen</label>";
  function wireFollow() {
    const cb = bodyR.querySelector(".follow input");
    if (cb) cb.addEventListener("change", () => { followScroll = cb.checked; });
  }
  function showDocContext(data) {
    const { title } = docTitle(data);
    titleR.textContent = "Kontext";
    let html = '<div class="ctx"><div class="eyebrow"><span class="dot" style="--c:' + colorOf(groupOf(data.source, data.kind))
      + '"></span>Hela dokumentet</div><div class="g-title">' + esc(caseName(title)) + "</div>";
    html += '<div class="g-nums"><div><b>' + fmt(data.inbound_count) + "</b><span>dokument<br>hänvisar hit</span></div>"
      + (docInbound && docInbound.total != null ? "<div><b>" + fmt(docInbound.total) + "</b><span>hänvisningar<br>till text och delar</span></div>" : "") + "</div>";
    if (!docInbound) html += LOADING;
    else if (docInbound.error) html += errorHtml(docInbound.error, "Hänvisningarna");
    else if (!docInbound.rows.length) html += '<div class="g-empty pt-empty">Inget i samlingen hänvisar hit.</div>';
    else {
      html += '<div class="g-key">↞ hänvisningar till det citerande dokumentet · × ställen i det</div>'
        + sectionsHtml(docInbound.rows, docInbound.by_source)
        + (docInbound.partial ? '<div class="g-more">de ' + fmt(docInbound.rows.length) + " första av " + fmt(docInbound.total) + " raderna — välj en bestämmelse för hela svaret</div>" : "");
    }
    html += '<details class="outbound"><summary><span class="pt-chevron"></span>Hänvisar vidare till</summary><div class="out-body"></div></details>';
    html += actionsHtml(data.uri) + followHtml() + "</div>";
    bodyR.innerHTML = html;
    bodyR.scrollTop = 0;
    wireFollow();
    const out = bodyR.querySelector(".outbound");
    out.addEventListener("toggle", () => { if (out.open) loadOutbound(data.uri, out.querySelector(".out-body")); });
  }
  async function loadOutbound(uri, slot) {
    if (slot.dataset.done) return;
    slot.dataset.done = "1";
    slot.innerHTML = LOADING;
    let rows;
    try { rows = await api("/api/v1/document/outbound", "uri=" + encodeURIComponent(uri)); }
    catch (err) { slot.innerHTML = errorHtml(err, "Utgående hänvisningar"); return; }
    const hosted = rows.filter(r => r.hosted !== false);
    const folded = foldRows(hosted.map(r => ({ uri: splitFrag(r.uri)[0], label: r.label, title: r.title, source: r.source, anchor: null })));
    slot.innerHTML = (folded.length ? "<ul>" + folded.slice(0, 60).map(ctxRow).join("") + "</ul>" : '<div class="pt-empty">Inga hänvisningar.</div>')
      + (folded.length > 60 ? '<div class="g-more">+ ' + fmt(folded.length - 60) + " dokument till</div>" : "")
      + (rows.length > hosted.length ? '<div class="g-more">' + fmt(rows.length - hosted.length) + " hänvisningar pekar utanför samlingen</div>" : "");
  }
  const unitCtx = new Map();       // anchor -> the exact /inbound answer
  async function showUnitContext(anchor, exact) {
    const data = currentDoc;
    const uri = data.uri + "#" + anchor;
    const el = page.querySelector('[data-unit="' + CSS.escape(anchor) + '"]');
    const numeral = el ? el.querySelector(".gutter .n").textContent.replace(/^Art\./, "Artikel ") : anchor;
    const head = numeral + " " + caseName(docTitle(data).title);
    titleR.textContent = numeral;
    const known = docInbound && !docInbound.error ? (docInbound.byUnit.get(anchor) || []) : null;
    let rows = known, total = known ? known.length : null, bySource = null, partial = docInbound && docInbound.partial;
    let html = '<div class="ctx"><div class="eyebrow"><span class="dot" style="--c:' + colorOf(groupOf(data.source, data.kind))
      + '"></span>Bestämmelse</div><div class="g-title" title="' + esc(head) + '">' + esc(head) + "</div>";
    const finish = () => {
      let h = html;
      if (rows == null) h += LOADING;
      else {
        h += '<div class="g-nums"><div><b>' + fmt(total) + "</b><span>hänvisningar</span></div>"
          + "<div><b>" + fmt(new Set(rows.map(r => r.uri)).size) + "</b><span>dokument</span></div></div>";
        h += rows.length ? '<div class="g-key">↞ hänvisningar till det citerande dokumentet · × ställen i det</div>' + sectionsHtml(rows, bySource)
          : '<div class="pt-empty">Inget i samlingen hänvisar till den här bestämmelsen' + (partial ? " bland de första raderna" : "") + ".</div>";
        if (partial && !exact) h += '<div class="g-more">ur dokumentets första ' + fmt(INBOUND_PAGE) + ' rader — <a href="' + docHash(uri) + '">hämta hela svaret</a></div>';
      }
      h += '<div class="g-actions"><a href="' + docHash(data.uri) + '">Hela dokumentet</a></div>' + actionsHtml(uri) + followHtml() + "</div>";
      bodyR.innerHTML = h;
      bodyR.scrollTop = 0;
      wireFollow();
    };
    finish();
    // scrolling never spends a request: it reads the document-level map
    if (!exact && known) return;
    let inb = unitCtx.get(anchor);
    if (!inb) {
      try {
        inb = await api("/api/v1/document/inbound", "uri=" + encodeURIComponent(uri) + "&limit=1000");
      } catch (err) {
        if (selectedUnit !== anchor) return;
        html += errorHtml(err, "Hänvisningarna");
        rows = [];
        finish();
        return;
      }
      unitCtx.set(anchor, inb);
    }
    if (selectedUnit !== anchor) return;
    rows = inb.citations; total = inb.total; bySource = inb.by_source; partial = inb.total > inb.citations.length;
    finish();
  }
  // as the reader scrolls, the context card follows the topmost unit in view
  // -- from the document-level map, so scrolling costs no request
  let observer = null;
  const visible = new Map();
  function observeUnits() {
    if (observer) observer.disconnect();
    visible.clear();
    observer = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (e.isIntersecting) visible.set(e.target, e.boundingClientRect.top);
        else visible.delete(e.target);
      }
      if (!followScroll || pinnedUnit || !reading || !currentDoc) return;
      let best = null, bestTop = Infinity;
      for (const el of visible.keys()) {
        const top = el.getBoundingClientRect().top;
        if (top >= -40 && top < bestTop) { best = el; bestTop = top; }
      }
      const anchor = best ? best.dataset.unit : null;
      if (anchor !== selectedUnit) selectUnit(anchor, false);
    }, { rootMargin: "-64px 0px -55% 0px" });
    page.querySelectorAll("[data-unit]").forEach(el => observer.observe(el));
  }
  // a click in the page body past the unit gutters releases a pinned unit
  page.addEventListener("click", ev => {
    if (ev.target.closest("a, button, .gutter, select, input, details")) return;
    if (pinnedUnit) pinnedUnit = false;
  });

  /* ---------------- link popovers: the target's own words -------------- */
  const pop = document.createElement("div");
  pop.className = "pt-pop"; pop.hidden = true;
  document.body.appendChild(pop);
  let popTimer = null, popFor = null;
  function hidePop() { clearTimeout(popTimer); popTimer = null; pop.hidden = true; popFor = null; }
  async function showPop(a) {
    const uri = a.dataset.uri;
    popFor = uri;
    let c;
    try { c = await api("/api/v1/card", "uri=" + encodeURIComponent(uri)); }
    catch (err) {
      if (popFor !== uri) return;
      pop.innerHTML = '<div class="cit">' + esc(pathOf(uri)) + '</div><div class="ti">' + esc(err.status === 404 ? "finns inte i samlingen" : "kunde inte hämtas") + "</div>";
      placePop(a);
      return;
    }
    if (popFor !== uri) return;
    const title = caseName(c.title || "");
    const cit = caseName(c.citation || c.label || pathOf(uri));
    pop.innerHTML = '<div class="eyebrow"><span class="dot" style="--c:' + colorOf(c.group) + '"></span>' + esc(c.group) + "</div>"
      + '<div class="cit">' + esc(cit) + "</div>"
      + (title && title.toLowerCase() !== cit.toLowerCase() ? '<div class="ti">' + esc(title) + "</div>" : "")
      + (c.snippet ? '<div class="snip">' + esc(c.snippet) + "</div>" : "")
      + (c.inbound_count ? '<div class="cited">↞ ' + fmt(c.inbound_count) + " hänvisningar hit</div>" : "");
    placePop(a);
  }
  function placePop(a) {
    const r = a.getBoundingClientRect();
    pop.hidden = false;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    let left = r.left + scrollX, top = r.bottom + scrollY + 8;
    if (left + w > scrollX + innerWidth - 12) left = Math.max(scrollX + 12, scrollX + innerWidth - 12 - w);
    if (r.bottom + 8 + h > innerHeight && r.top - 8 - h > 0) top = r.top + scrollY - 8 - h;
    pop.style.left = left + "px"; pop.style.top = top + "px";
  }
  mount.addEventListener("pointerover", ev => {
    if (ev.pointerType === "touch") return;
    const a = ev.target.closest("a[data-uri]");
    if (!a || a.closest(".pt-hits")) return;
    clearTimeout(popTimer);
    popTimer = setTimeout(() => showPop(a), 350);
  });
  mount.addEventListener("pointerout", ev => {
    const a = ev.target.closest("a[data-uri]");
    if (!a) return;
    const to = ev.relatedTarget;
    if (to && (a.contains(to) || pop.contains(to))) return;
    clearTimeout(popTimer); popTimer = null;
    popTimer = setTimeout(() => { if (!pop.matches(":hover")) hidePop(); }, 250);
  });
  pop.addEventListener("pointerleave", () => hidePop());
  addEventListener("keydown", ev => { if (ev.key === "Escape") hidePop(); });

  /* ---------------- boot ---------------- */
  narrowFold();
  renderTraffic();
  render();
})();
