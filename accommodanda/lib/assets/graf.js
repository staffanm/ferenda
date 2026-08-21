/* The citation-graph explorer at /hanvisningar/ (templates/hanvisningar.html).
 *
 * One canvas, drawn from /api/v1/graph: the centered document (or provision)
 * with its citers streaming in from the left and its citation targets to the
 * right. The depth control -- or zooming out -- fetches one more degree,
 * direction-preserved: the second inbound ring is "documents citing a
 * document that cites the center", never a mixed walk. A provision center
 * additionally shows its document's internal §-to-§ graph. Colours are the
 * page's own tokens plus one hue per flow group (the legend doubles as the
 * source-type filter); the same vocabulary as the stats flow diagram
 * (lib/facets.flow_group). */
(function () {
  "use strict";
  const mount = document.querySelector(".graf");
  if (!mount) return;

  /* ---------------- constants ---------------- */
  const DEFAULT_URI = mount.dataset.defaultUri;
  const MAX_DEPTH = 3;
  const CENTER_LIMIT = 120;        // hop-1 nodes per side
  const LABEL_BUDGET = 26;         // neighbour labels drawn at scale 1
  const EXPAND_SIDE = 7;           // frontier nodes expanded per side per level
  const EXPAND_LIMIT = [0, 0, 6, 4];   // neighbors added per expanded node, by depth
  const MAX_UNITS = 280;           // internal § nodes drawn (panel says the rest)
  const NARROW = 860;              // the stylesheet's stacking breakpoint
  // the group *list* is the page's (facets.FLOW_GROUP_NAMES via the template),
  // so the legend and filter always carry every group the API knows; only the
  // hues are client-side, and a group without one falls back to grey rather
  // than falling out of the filter
  const GROUPS = JSON.parse(mount.dataset.flowGroups);
  /* One hue per flow group. The four that carry the corpus -- författningar,
   * förarbeten, rättsfall, EU-rättsakter -- sit a quarter-turn apart (red,
   * violet, amber, blue) so a 6px dot tells them apart at a glance; the first
   * three keep the site's own identity colours (--accent, --forarbete,
   * --case), the fourth takes the blue the EU family already reads in. Every
   * hue before this sat between 10° and 60° of every other, and författningar
   * against rättsfall was two browns.
   *
   * The smaller groups fill the wheel between them, and the two families that
   * always appear together stay families: EU is four steps of one blue, the
   * international pair is plum and orchid. */
  const GROUP_COLOR = {
    "Författningar": "#a63a29",        // oxblood -- the site accent
    "Förarbeten": "#5a4fa0",           // blue-violet -- --forarbete
    "Rättsfall": "#c17d17",            // amber -- --case, brightened
    "Föreskrifter": "#7f8a26",         // olive
    "Myndighetsavgöranden": "#2c8455", // green
    "Ställningstaganden": "#17807f",   // teal
    "Lagkommentarer": "#b03a6b",       // magenta -- --kommentar
    "Begrepp": "#cc6f93",              // rose
    "EU-rättsakter": "#1c6fae",        // blue
    "EU-domar": "#4f97cf",             // light blue
    "EU-fördrag": "#10476e",           // navy
    "EU-vägledning": "#8bb0c9",        // steel
    "Konventioner": "#7a3f86",         // plum
    "Folkrättslig praxis": "#b06fae",  // orchid
  };
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fmt = n => n.toLocaleString("sv-SE");
  const esc = s => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
                            .replace(/"/g, "&quot;");

  /* ---------------- theme ---------------- */
  let TOK = {}, DARK = false;
  function refreshTokens() {
    const cs = getComputedStyle(document.documentElement);
    const t = document.documentElement.getAttribute("data-theme");
    DARK = t === "dark" || (t !== "light" &&
      matchMedia("(prefers-color-scheme: dark)").matches);
    TOK = { bg: cs.getPropertyValue("--bg").trim(),
            surf: cs.getPropertyValue("--surf").trim(),
            ink: cs.getPropertyValue("--ink").trim(),
            ink3: cs.getPropertyValue("--ink-3").trim(),
            accent: cs.getPropertyValue("--accent").trim() };
    buildLegend();
  }
  function hexToHsl(hex) {
    const v = parseInt(hex.slice(1), 16),
          r = (v >> 16 & 255) / 255, g = (v >> 8 & 255) / 255, b = (v & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > .5 ? d / (2 - mx - mn) : d / (mx + mn);
    const h = mx === r ? (g - b) / d + (g < b ? 6 : 0)
            : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }
  function colorOf(group) {
    const base = GROUP_COLOR[group] || "#697079";
    if (!DARK) return base;
    const [h, s, l] = hexToHsl(base);     // lighten the way style.css does
    return "hsl(" + h.toFixed(0) + " " + (s * 78).toFixed(0) + "% " +
           (Math.min(.70, l + .26) * 100).toFixed(0) + "%)";
  }
  new MutationObserver(refreshTokens).observe(
    document.documentElement, { attributes: true,
                                attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", refreshTokens);

  /* ---------------- state ---------------- */
  const nodes = new Map(), edges = new Map();
  let center = null;               // the /api/v1/graph payload for the center
  let depth = 1, direction = "both";
  let active = new Set(GROUPS);   // legend filter
  let selected = null, hovered = null, alpha = 0, generation = 0;
  // the legend states how many of each group are on the stage, so it has to be
  // rewritten whenever the node set changes -- including the rings that arrive
  // one fetch at a time. The flag is flushed once per frame rather than at
  // every addNode, which would rewrite 14 rows per node.
  let legendDirty = true;
  let particles = [];
  const cache = new Map();         // uri|dir|groups -> Promise(payload)

  function groupsParam() {
    return active.size === GROUPS.length
      ? "" : [...active].join(",");
  }
  function fetchGraph(uri, dir, limit) {
    const groups = groupsParam();
    const key = uri + "|" + dir + "|" + groups + "|" + limit;
    if (!cache.has(key)) {
      const q = new URLSearchParams({ uri: uri, direction: dir,
                                      limit: String(limit) });
      if (groups) q.set("groups", groups);
      // a failed fetch must not poison the cache key -- evict and rethrow,
      // so a retry after a transient error actually retries
      cache.set(key, fetch("/api/v1/graph?" + q).then(
        r => { if (!r.ok) throw new Error("graph " + r.status);
               return r.json(); })
        .catch(err => { cache.delete(key); throw err; }));
    }
    return cache.get(key);
  }

  /* ---------------- canvas ---------------- */
  const stage = mount.querySelector(".graf-stage"),
        canvas = stage.querySelector("canvas"),
        ctx = canvas.getContext("2d");
  let W = 800, H = 600, dpr = 1, scale = 1, tx = 0, ty = 0;
  function resize() {
    dpr = devicePixelRatio || 1;
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    // the center is pinned to the middle of the stage, so a stage that just
    // changed size has to move it -- otherwise the graph keeps hanging off the
    // side it was laid out against
    for (const n of nodes.values())
      if (n.hop === 0 && n.pin) n.pin = [W / 2, H / 2];
  }
  addEventListener("resize", () => { resize(); reheat(.3); });
  const toWorld = (sx, sy) => [(sx - tx) / scale, (sy - ty) / scale];
  function reheat(v) { alpha = Math.max(alpha, v); }

  function addNode(id, props) {
    legendDirty = true;
    let n = nodes.get(id);
    if (!n) {
      const a = Math.random() * Math.PI * 2,
            d = 90 + Math.random() * 130;
      n = { id: id, x: (props.ox ?? W / 2) + Math.cos(a) * d,
            y: (props.oy ?? H / 2) + Math.sin(a) * d,
            vx: 0, vy: 0, born: performance.now() };
      nodes.set(id, n);
    }
    Object.assign(n, props);
    return n;
  }
  function addEdge(a, b, w, kind) {       // direction: a cites b
    const key = a + "→" + b;
    if (edges.has(key)) { edges.get(key).w = Math.max(edges.get(key).w, w); return; }
    const bow = (hash(key) % 2 ? 1 : -1) * (.09 + (hash(key) % 7) / 45);
    edges.set(key, { a: a, b: b, w: w, bow: bow, kind: kind || "doc" });
  }
  function hash(s) {
    let h = 2166136261;
    for (const c of s) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
    return Math.abs(h);
  }

  /* ---------------- building the graph ---------------- */
  function docRadius(n, hop) {
    return (hop <= 1 ? 6 : 4) + Math.min(13, Math.log2(n + 1) * 1.6)
           / (hop >= 2 ? 1.6 : 1);
  }
  function placeSide(host, side, list, hop) {
    for (const nb of list) {
      if (!nodes.has(nb.uri))
        addNode(nb.uri, { type: "doc", label: nb.descriptive || nb.label || nb.uri,
                          title: nb.title, group: nb.group,
                          r: docRadius(nb.n, hop), hop: hop, side: side,
                          ox: host.x + side * 110, oy: host.y });
      else if (nodes.get(nb.uri).hop > hop) nodes.get(nb.uri).hop = hop;
      if (side < 0) addEdge(nb.uri, host.id, nb.n);
      else addEdge(host.id, nb.uri, nb.n);
    }
  }
  function placeUnits(data, centerNode) {
    const internal = data.internal;
    if (!internal) return;
    const byAnchor = new Map(internal.nodes.map(u => [u.anchor, u]));
    const keep = new Set(internal.nodes
      .slice().sort((a, b) => b.n - a.n).slice(0, MAX_UNITS)
      .map(u => u.anchor));
    keep.add(data.unit);
    for (const anchor of keep) {
      if (anchor === data.unit) continue;      // the focus unit IS the center
      const u = byAnchor.get(anchor);
      if (!u) continue;
      addNode("unit:" + anchor,
              { type: "unit", label: u.label, group: data.group,
                r: 3.2 + Math.min(7, Math.log2(u.n + 1) * 1.1),
                hop: 1, side: 0, anchor: anchor,
                ox: centerNode.x, oy: centerNode.y });
    }
    const id = a => a === data.unit ? center.uri : "unit:" + a;
    for (const [a, b, n] of internal.edges)
      if (keep.has(a) && keep.has(b)) addEdge(id(a), id(b), n, "unit");
  }
  async function expandFrontier(gen, level) {
    for (const side of [-1, 1]) {
      if (direction === "in" && side > 0) continue;
      if (direction === "out" && side < 0) continue;
      const frontier = [...nodes.values()]
        .filter(n => n.type === "doc" && n.hop === level - 1 && n.side === side)
        .slice(0, EXPAND_SIDE);
      for (const host of frontier) {
        const data = await fetchGraph(host.id, side < 0 ? "in" : "out",
                                      EXPAND_LIMIT[level]);
        if (gen !== generation || depth < level) return;
        const list = side < 0 ? data.inbound.top : data.outbound.top;
        placeSide(host, side, list, level);
        seedParticles(); reheat(.5);
      }
    }
  }
  async function build() {
    const gen = ++generation;
    const uri = center.uri;
    nodes.clear(); edges.clear(); particles = [];
    const c = addNode(uri, {
      type: "doc", label: center.label || uri, title: center.title,
      group: center.group, r: center.anchor ? 13 : 16, hop: 0, side: 0,
      pin: [W / 2, H / 2] });
    c.x = W / 2; c.y = H / 2;
    if (direction !== "out" && center.inbound)
      placeSide(c, -1, center.inbound.top, 1);
    if (direction !== "in" && center.outbound)
      placeSide(c, 1, center.outbound.top, 1);
    placeUnits(center, c);
    scale = 1; tx = 0; ty = 0;
    seedParticles(); reheat(1);
    showCapNote();
    select(uri);
    for (let level = 2; level <= depth; level++) {
      await expandFrontier(gen, level);
      if (gen !== generation) return;
    }
  }
  /* What the stage is *not* showing. The center's neighbourhood is cut to
   * CENTER_LIMIT documents a side, and article 6 ECHR has 31,996 citers -- a
   * graph that draws 120 of them and says nothing reads as the whole picture.
   * Both counts come from the payload's own totals, which `_graph_side`
   * computes over the group-filtered set, so the note describes the same
   * population the stage does. */
  const capNote = mount.querySelector(".graf-cap");
  function showCapNote() {
    let docs = 0, links = 0;
    for (const side of [center.inbound, center.outbound]) {
      if (!side) continue;
      docs += side.total_docs - side.top.length;
      links += side.total_links - side.top.reduce((sum, r) => sum + r.n, 0);
    }
    capNote.hidden = docs <= 0;
    capNote.textContent = fmt(links) + " hänvisningar från " + fmt(docs) +
      " dokument visas inte av prestandaskäl";
  }
  const hint = mount.querySelector(".graf-hint");
  const hintText = hint.textContent;
  function flashHint(message) {
    hint.textContent = message;
    setTimeout(() => { hint.textContent = hintText; }, 4000);
  }
  async function recenter(uri, push) {
    let data;
    try {
      data = await fetchGraph(uri, "both", CENTER_LIMIT);
    } catch (err) {              // a 404 hash or a dropped connection: say so,
      flashHint("ingen sådan startpunkt: " + uri);   // keep the current graph
      return;
    }
    center = data;
    if (push !== false)
      history.replaceState(null, "", "#" + encodeURIComponent(uri));
    await build();
  }
  function rebuildFiltered() {     // direction or group filter changed
    cache.clear();
    recenter(center.uri, false);
  }

  /* ---------------- simulation ---------------- */
  function tick() {
    if (alpha < .004) return;
    const list = [...nodes.values()];
    const cx = W / 2, cy = H / 2;
    for (let i = 0; i < list.length; i++) {
      const a = list[i];
      for (let j = i + 1; j < list.length; j++) {
        const b = list[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        if (d2 > 160000) continue;           // beyond 400px repulsion is noise
        const d = Math.sqrt(d2), min = a.r + b.r + 12;
        let f = 1100 * (a.r / 14) * (b.r / 14) / d2;
        if (d < min) f += (min - d) * .06;
        const ux = dx / d * f * alpha, uy = dy / d * f * alpha;
        a.vx += ux; a.vy += uy; b.vx -= ux; b.vy -= uy;
      }
    }
    for (const e of edges.values()) {
      const a = nodes.get(e.a), b = nodes.get(e.b);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y,
            d = Math.sqrt(dx * dx + dy * dy) || 1,
            rest = a.r + b.r + (e.kind === "unit" ? 46 : 85) +
                   ((a.hop || 0) + (b.hop || 0)) * 20,
            f = (d - rest) * .005 * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of list) {
      n.vx += (cx - n.x) * .0008 * alpha;
      n.vy += (cy - n.y) * .0012 * alpha;
      if (n.side)
        n.vx += (cx + n.side * Math.min(W * .3, 320) - n.x) * .004 * alpha;
      n.vx *= .86; n.vy *= .86;
      if (n.pin) { n.x = n.pin[0]; n.y = n.pin[1]; n.vx = n.vy = 0; }
      else { n.x += n.vx; n.y += n.vy; }
    }
    alpha *= REDUCED ? .93 : .985;
  }

  /* ---------------- particles ---------------- */
  function seedParticles() {
    particles = [];
    if (REDUCED) return;
    const strongest = [...edges.values()].sort((a, b) => b.w - a.w).slice(0, 60);
    for (const e of strongest) {
      const count = Math.min(8, 1 + Math.floor(Math.log10(e.w + 1) * 2.2));
      for (let i = 0; i < count; i++)
        particles.push({ e: e, t: Math.random(),
                         v: .0018 + Math.random() * .0022 });
    }
    if (particles.length > 380) particles.length = 380;
  }

  /* ---------------- drawing ---------------- */
  function bez(e) {
    const a = nodes.get(e.a), b = nodes.get(e.b);
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2,
          dx = b.x - a.x, dy = b.y - a.y;
    return [a, b, mx - dy * e.bow, my + dx * e.bow];
  }
  const onCurve = (a, b, cx, cy, t) => [
    (1 - t) * (1 - t) * a.x + 2 * (1 - t) * t * cx + t * t * b.x,
    (1 - t) * (1 - t) * a.y + 2 * (1 - t) * t * cy + t * t * b.y];

  function draw(now) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = TOK.bg;
    ctx.fillRect(0, 0, W, H);
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * tx, dpr * ty);
    const focus = hovered || selected;

    for (const e of edges.values()) {
      const [a, b, cx, cy] = bez(e);
      const hot = focus && (e.a === focus || e.b === focus);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.quadraticCurveTo(cx, cy, b.x, b.y);
      ctx.strokeStyle = colorOf(b.group);
      ctx.globalAlpha = hot ? .55 : e.kind === "unit" ? .28 : .16;
      ctx.lineWidth = Math.max(.7, Math.log2(e.w + 1) * .8) / scale *
                      (hot ? 1.35 : 1);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    if (!REDUCED) {
      for (const p of particles) {
        p.t += p.v * (scale > 1 ? 1 : scale);
        if (p.t > 1) p.t -= 1;
        const [a, b, cx, cy] = bez(p.e);
        const [x, y] = onCurve(a, b, cx, cy, p.t);
        ctx.beginPath();
        ctx.arc(x, y, Math.max(.8, 1.4 / scale), 0, 7);
        ctx.fillStyle = colorOf(b.group);
        ctx.globalAlpha = .8 * Math.sin(p.t * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    for (const n of nodes.values()) {
      const col = colorOf(n.group);
      const grow = REDUCED ? 1 : Math.min(1, (now - n.born) / 400);
      const r = n.r * (.3 + .7 * grow);
      if (n.id === selected || n.id === hovered) {
        ctx.shadowColor = col; ctx.shadowBlur = 20 * scale;
      }
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 7);
      ctx.fillStyle = TOK.surf; ctx.fill();
      ctx.globalAlpha = n.type === "unit" ? .14 : .28;
      ctx.fillStyle = col; ctx.fill();
      ctx.globalAlpha = 1;
      if (n.type === "unit") ctx.setLineDash([3 / scale, 2 / scale]);
      ctx.lineWidth = (n.id === selected ? 2.6 : 1.5) / scale;
      ctx.strokeStyle = col; ctx.stroke();
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
      if (n.id === selected && !REDUCED) {
        const ph = (now % 1600) / 1600;
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 4 + ph * 13, 0, 7);
        ctx.globalAlpha = (1 - ph) * .5;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    /* Labels thin out as the graph grows. With 120 neighbours a side every
     * circle carried its case name and the stage was a mat of overlapping
     * text -- so only the most-cited keep a standing label, and zooming in
     * raises the budget (the same bargain the node radius already makes).
     * The center, the selection, what the pointer is over and anything joined
     * to it are always named: those are the reader's own question. */
    const ranked = [...nodes.values()]
      .filter(n => n.type === "doc" && n.hop !== 0)
      .sort((a, b) => b.r - a.r);
    const budget = Math.round(LABEL_BUDGET * Math.max(1, scale));
    const cutoff = ranked.length > budget ? ranked[budget - 1].r : 0;
    // "joined to the focus" names a small set for a selected neighbour, and
    // the entire graph when the focus is the center -- every hop-1 node is
    // joined to it by construction. Naming all of them is what the budget is
    // there to prevent.
    const spoke = focus && center && focus === center.uri;
    ctx.textAlign = "center";
    for (const n of nodes.values()) {
      const linked = focus && !spoke &&
        (edges.has(focus + "→" + n.id) || edges.has(n.id + "→" + focus));
      const show = n.hop === 0 || n.id === selected || n.id === hovered ||
        linked || (n.type === "unit" ? scale > 1.5
                                     : n.r >= cutoff && scale * n.r > 8.5);
      if (!show) continue;
      const unit = n.type === "unit";
      const size = (unit ? 9.5 : n.hop === 0 ? 13 : 11) / Math.sqrt(scale);
      ctx.font = (unit ? "italic 500 " + size + 'px "Source Serif 4",Georgia,serif'
                       : "550 " + size + "px Inter,sans-serif");
      ctx.lineWidth = 3.4 / scale;
      ctx.strokeStyle = TOK.bg;
      ctx.fillStyle = unit ? TOK.ink3 : TOK.ink;
      const text = n.label.length > 34 ? n.label.slice(0, 32) + "…" : n.label;
      const y = n.y + n.r + size + 3 / scale;
      ctx.strokeText(text, n.x, y);
      ctx.fillText(text, n.x, y);
    }
  }
  function frame(now) {
    if (legendDirty) { legendDirty = false; syncLegendCounts(); }
    tick(); draw(now); requestAnimationFrame(frame);
  }

  /* ---------------- interaction ---------------- */
  let dragNode = null, panning = false, sx = 0, sy = 0, moved = 0;
  let lastZoomStep = 0;
  function pick(px, py) {
    const [wx, wy] = toWorld(px, py);
    let best = null, bd = 1e9;
    for (const n of nodes.values()) {
      const d = Math.hypot(n.x - wx, n.y - wy);
      if (d < n.r + 6 / scale && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const local = ev => { const r = canvas.getBoundingClientRect();
                        return [ev.clientX - r.left, ev.clientY - r.top]; };
  canvas.addEventListener("pointerdown", ev => {
    canvas.setPointerCapture(ev.pointerId);
    sx = ev.clientX; sy = ev.clientY; moved = 0;
    dragNode = pick(...local(ev));
    panning = !dragNode;
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", ev => {
    if (dragNode || panning) {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      moved += Math.abs(dx) + Math.abs(dy);
      sx = ev.clientX; sy = ev.clientY;
      if (dragNode) { dragNode.pin = [...toWorld(...local(ev))]; reheat(.25); }
      else { tx += dx; ty += dy; }
    } else {
      const h = pick(...local(ev));
      hovered = h ? h.id : null;
      canvas.style.cursor = h ? "pointer" : "grab";
    }
  });
  canvas.addEventListener("pointerup", ev => {
    canvas.classList.remove("dragging");
    if (moved < 5) {
      const n = pick(...local(ev));
      if (n) select(n.id); else closePanel();
    }
    if (dragNode && dragNode.hop !== 0) dragNode.pin = null;
    dragNode = null; panning = false;
  });
  canvas.addEventListener("dblclick", ev => {
    const n = pick(...local(ev));
    if (!n) return;
    if (n.type === "unit") recenter(center.root + "#" + n.anchor);
    else if (n.hop !== 0) recenter(n.id);
  });
  canvas.addEventListener("wheel", ev => {
    ev.preventDefault();
    const [mx, my] = local(ev);
    const f = Math.exp(-ev.deltaY * .0013);
    let ns = Math.min(6, Math.max(.3, scale * f));
    // crossing the outer/inner threshold steps the *degree*: zooming out asks
    // for one more ring of connections, zooming back in removes it
    const now = performance.now();
    if (now - lastZoomStep > 700) {
      if (ns < .52 && depth < MAX_DEPTH) { lastZoomStep = now; setDepth(depth + 1);
                                           ns = .95; }
      else if (ns > 2.1 && depth > 1) { lastZoomStep = now; setDepth(depth - 1);
                                        ns = 1.1; }
    }
    tx = mx - (mx - tx) * ns / scale;
    ty = my - (my - ty) * ns / scale;
    scale = ns;
  }, { passive: false });

  /* ---------------- depth / direction / filter controls ---------------- */
  const depthLabel = mount.querySelector(".graf-depth");
  function setDepth(d) {
    d = Math.min(MAX_DEPTH, Math.max(1, d));
    if (d === depth) return;
    if (d < depth) {                       // shed the outer rings in place
      depth = d;
      for (const [id, n] of [...nodes])
        if (n.hop > depth) nodes.delete(id);
      for (const [key, e] of [...edges])
        if (!nodes.has(e.a) || !nodes.has(e.b)) edges.delete(key);
      legendDirty = true;
      seedParticles(); reheat(.4);
    } else {
      depth = d;
      const gen = generation;
      expandFrontier(gen, depth);
    }
    depthLabel.textContent = depth + " led";
  }
  mount.querySelectorAll(".graf-steps button").forEach(b =>
    b.addEventListener("click", () => setDepth(depth + Number(b.dataset.step))));
  mount.querySelectorAll(".graf-seg button").forEach(b =>
    b.addEventListener("click", () => {
      direction = b.dataset.dir;
      mount.querySelectorAll(".graf-seg button").forEach(x =>
        x.classList.toggle("on", x === b));
      rebuildFiltered();
    }));

  /* ---------------- legend = source-type filter ---------------- */
  const legend = mount.querySelector(".graf-legend");
  function buildLegend() {
    legend.innerHTML = "";
    for (const name of GROUPS) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "graf-src";
      row.dataset.group = name;
      row.setAttribute("role", "switch");
      row.setAttribute("aria-checked", active.has(name) ? "true" : "false");
      row.style.setProperty("--c", colorOf(name));
      row.innerHTML = '<span class="sw"></span><span class="dot"></span>' +
        '<span class="nm">' + esc(name) + '</span><span class="cnt"></span>';
      row.addEventListener("click", () => {
        if (active.has(name)) active.delete(name);
        else active.add(name);
        // filtering every group away leaves nothing to draw, so the last one
        // off means "all of them" rather than an empty stage
        if (!active.size) active = new Set(GROUPS);
        buildLegend();
        rebuildFiltered();
      });
      legend.appendChild(row);
    }
    legendDirty = true;
  }
  /* How many documents of one group are drawn, per direction. With "Båda" the
   * two sides are separate statements -- "→42, 5→" is 42 documents citing the
   * center and 5 it cites -- because one number over a two-sided graph says
   * nothing about which side the reader is looking at. The center is the
   * startpunkt, not a neighbour, so it counts on neither side. */
  function countText(inbound, outbound) {
    if (direction === "in") return inbound ? fmt(inbound) : "";
    if (direction === "out") return outbound ? fmt(outbound) : "";
    return [inbound ? "→" + fmt(inbound) : "",
            outbound ? fmt(outbound) + "→" : ""].filter(Boolean).join(", ");
  }
  function syncLegendCounts() {
    const inb = new Map(), out = new Map();
    for (const n of nodes.values()) {
      if (n.type !== "doc" || !n.side) continue;
      const side = n.side < 0 ? inb : out;
      side.set(n.group, (side.get(n.group) || 0) + 1);
    }
    for (const row of legend.children) {
      const name = row.dataset.group;
      row.querySelector(".cnt").textContent = active.has(name)
        ? countText(inb.get(name) || 0, out.get(name) || 0) : "";
    }
  }

  /* ---------------- the rails, rolled up and down ----------------
   * The rails stand over the stage, so folding one changes no layout the
   * canvas can see -- the stage is always the whole page, and what folding
   * buys the reader is sight of the graph the card was covering. */
  function fold(rail, folded) {
    rail.classList.toggle("folded", folded);
    rail.querySelector(".graf-fold")
        .setAttribute("aria-expanded", folded ? "false" : "true");
  }
  mount.querySelectorAll(".graf-rail").forEach(rail => {
    // the whole head takes the click, not just the chevron: a 7px target for
    // a card this size is a target you have to aim at
    rail.querySelector(".graf-railhead").addEventListener("click",
      () => fold(rail, !rail.classList.contains("folded")));
    // on a phone the two cards would cover the stage between them, so both
    // start rolled up and the reader opens the one they want
    if (innerWidth <= NARROW) fold(rail, true);
  });

  /* ---------------- panel (the right rail's body) ---------------- */
  const panelRail = mount.querySelector(".graf-rail-r"),
        railTitle = panelRail.querySelector(".graf-railtitle"),
        panelBody = panelRail.querySelector(".graf-panel-body");
  const EMPTY_PANEL = '<div class="g-empty">Klicka på en nod för att se '
    + "dess hänvisningar.</div>";
  // clicking past every node clears the selection; the rail stays where the
  // reader put it, because folding is now their own affordance, not a
  // side effect of a stray click
  function closePanel() {
    selected = null;
    railTitle.textContent = "Detaljer";
    panelBody.innerHTML = EMPTY_PANEL;
  }
  const pagePath = uri => uri.replace("https://lagen.nu", "") || "/";
  function rowHtml(nb) {
    return '<li data-uri="' + esc(nb.uri) + '"><span class="dot" style="background:'
      + colorOf(nb.group) + '"></span><span class="lb">'
      + esc(nb.descriptive || nb.label || nb.uri) + '</span><span class="ti">'
      + esc(nb.title || "") + '</span><span class="n">' + fmt(nb.n) + "</span></li>";
  }
  /* One direction's neighbours: the heading, how many documents are behind it,
   * and the rows the API's cap left room for. */
  function sideHtml(heading, side) {
    if (!side) return "";
    const more = side.total_docs - side.top.length;
    return "<h3>" + heading + '<span class="n">' + fmt(side.total_docs)
      + "</span></h3><ul>" + side.top.map(rowHtml).join("") + "</ul>"
      + (more > 0 ? '<div class="g-more">+ ' + fmt(more) +
                 " dokument till</div>" : "")
      + (side.unresolved ? '<div class="g-more">' + fmt(side.unresolved) +
       " hänvisningar pekar utanför korpuset</div>" : "");
  }
  function sidesHtml(inb, out) {
    return sideHtml("Hänvisar hit", inb)
      + sideHtml("Hänvisar vidare till", out);
  }
  function select(id) {
    selected = id;
    const n = nodes.get(id);
    if (!n) return;
    if (n.type === "unit") { openUnitPanel(n); return; }
    if (id === center.uri) { openCenterPanel(); return; }
    fetchGraph(id, "both", 12).then(d => {
      if (selected === id) openDocPanel(d); });
  }
  /* The node's name goes in the rail's *head*, not its body: rolled up, the
   * card still says which node it stands for. The body opens on the flow
   * group and the document's own title. */
  function panelFrame(d, headline, sub) {
    railTitle.textContent = headline || "";
    return '<div class="eyebrow"><span class="dot" style="background:'
      + colorOf(d.group) + '"></span>' + esc(d.group)
      + '</div><div class="g-title">' + esc(sub || "") + "</div>";
  }
  function numsHtml(d) {
    const inb = d.inbound, out = d.outbound;
    return '<div class="g-nums">' +
      (inb ? "<div><b>" + fmt(inb.total_links) + "</b><span>hänvisningar in<br>från "
           + fmt(inb.total_docs) + " dok</span></div>" : "") +
      (out ? "<div><b>" + fmt(out.total_links) + "</b><span>hänvisningar ut<br>till "
           + fmt(out.total_docs) + " dok</span></div>" : "") + "</div>";
  }
  function actionsHtml(d, isCenter) {
    const href = pagePath(d.root) + (d.anchor ? "#" + d.anchor : "");
    return '<div class="g-actions">' +
      (isCenter ? "" :
       '<button type="button" class="primary" data-recenter="' + esc(d.uri) +
       '">Utforska härifrån</button>') +
      '<a href="' + esc(href) + '" target="_blank" rel="noopener">' +
      "Öppna dokumentet ↗</a></div>";
  }
  function wire() {
    panelRail.querySelector(".graf-railbody").scrollTop = 0;
    panelBody.querySelectorAll("li[data-uri]").forEach(li =>
      li.addEventListener("click", () => {
        const uri = li.dataset.uri;
        if (nodes.has(uri)) select(uri); else recenter(uri);
      }));
    const rc = panelBody.querySelector("[data-recenter]");
    if (rc) rc.addEventListener("click", () => recenter(rc.dataset.recenter));
  }
  function openCenterPanel() {
    const d = center;
    let internalNote = "";
    if (d.internal && d.internal.edges.length) {
      const shown = Math.min(MAX_UNITS, d.internal.nodes.length);
      internalNote = '<div class="g-more">' + fmt(d.internal.edges.length) +
        " interna hänvisningar mellan " + fmt(d.internal.nodes.length) +
        " enheter" + (shown < d.internal.nodes.length
          ? " (de " + fmt(shown) + " mest sammanlänkade ritas)" : "") + "</div>";
    }
    panelBody.innerHTML = panelFrame(d, d.citation, d.title) + numsHtml(d) +
      internalNote +
      sidesHtml(direction === "out" ? null : d.inbound,
                direction === "in" ? null : d.outbound) +
      actionsHtml(d, true);
    wire();
  }
  function openDocPanel(d) {
    panelBody.innerHTML = panelFrame(d, d.citation, d.title) + numsHtml(d) +
      sidesHtml(d.inbound, d.outbound) + actionsHtml(d, false);
    wire();
  }
  function openUnitPanel(n) {
    fetchGraph(center.root + "#" + n.anchor, "both", 12).then(d => {
      if (selected !== n.id) return;
      panelBody.innerHTML =
        panelFrame(d, d.citation, d.title)
        + numsHtml(d) + sidesHtml(d.inbound, d.outbound)
        + actionsHtml(d, false);
      wire();
    });
  }

  /* ---------------- search (recenter) ---------------- */
  const input = mount.querySelector(".graf-search input"),
        hits = mount.querySelector(".graf-hits");
  let hitRows = [], hitIndex = -1, timer = null;
  function hitTarget(res) {          // a citation-resolved hit centers on its provision
    // `pin`, not `fragments`: the pin is the provision the query resolved to,
    // while a full-text hit's fragments are only where the words stand -- and
    // centring the graph on one of those recentred "dataförordningen" on
    // article 47 of the EU Data Act instead of on the act
    const pin = res.pin;
    return pin && pin.uri && pin.uri.includes("#") ? pin.uri : res.uri;
  }
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = input.value.trim();
      if (text.length < 2) { hits.hidden = true; return; }
      const r = await fetch("/api/v1/search?q=" + encodeURIComponent(text) +
                            "&limit=8");
      if (!r.ok) {                     // search cluster down: say so, don't sit inert
        hitRows = []; hitIndex = -1;
        hits.innerHTML = '<div class="hit">sökningen kunde inte nås</div>';
        hits.hidden = false;
        return;
      }
      hitRows = (await r.json()).results;
      hitIndex = -1;
      hits.innerHTML = hitRows.map((res, i) =>
        '<div class="hit" data-i="' + i + '"><span class="lb">'
        + esc(res.identifier || res.display || res.uri) + '</span><span class="ti">'
        + esc(res.display || res.title || "") + "</span></div>").join("")
        || '<div class="hit">inga träffar</div>';
      hits.hidden = false;
      hits.querySelectorAll(".hit[data-i]").forEach(el =>
        el.addEventListener("click", () => pickHit(Number(el.dataset.i))));
    }, 180);
  });
  function pickHit(i) {
    if (!hitRows[i]) return;
    hits.hidden = true;
    input.value = hitRows[i].identifier || "";
    recenter(hitTarget(hitRows[i]));
  }
  input.addEventListener("keydown", ev => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      hitIndex = Math.max(0, Math.min(hitRows.length - 1,
        hitIndex + (ev.key === "ArrowDown" ? 1 : -1)));
      hits.querySelectorAll(".hit").forEach((el, i) =>
        el.classList.toggle("on", i === hitIndex));
    } else if (ev.key === "Enter" && hitIndex >= 0) pickHit(hitIndex);
    else if (ev.key === "Escape") { hits.hidden = true; input.blur(); }
  });
  addEventListener("pointerdown", ev => {
    if (!hits.contains(ev.target) && ev.target !== input) hits.hidden = true;
  });

  /* ---------------- boot ---------------- */
  refreshTokens();
  resize();
  closePanel();
  // the hash is untrusted reader input: a malformed escape or a uri the
  // catalog lacks falls back to the default center rather than a blank stage
  let startUri = DEFAULT_URI;
  try {
    startUri = decodeURIComponent(location.hash.slice(1)) || DEFAULT_URI;
  } catch (err) { /* keep the default */ }
  recenter(startUri, false).then(() => {
    if (!center && startUri !== DEFAULT_URI) recenter(DEFAULT_URI, false);
  });
  requestAnimationFrame(frame);
})();
