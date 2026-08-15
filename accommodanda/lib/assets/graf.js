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
  const CENTER_LIMIT = 22;         // hop-1 nodes per side
  const EXPAND_SIDE = 7;           // frontier nodes expanded per side per level
  const EXPAND_LIMIT = [0, 0, 6, 4];   // neighbors added per expanded node, by depth
  const MAX_UNITS = 280;           // internal § nodes drawn (panel says the rest)
  // the group *list* is the page's (facets.FLOW_GROUP_NAMES via the template),
  // so the legend and filter always carry every group the API knows; only the
  // hues are client-side, and a group without one falls back to grey rather
  // than falling out of the filter
  const GROUPS = JSON.parse(mount.dataset.flowGroups);
  const GROUP_COLOR = {            // site-family hues per flow group
    "Författningar": "#8f3524",
    "Förarbeten": "#544a80",
    "Rättsfall": "#9a5a2a",
    "Föreskrifter": "#7d6a2e",
    "Myndighetsavgöranden": "#2f6b46",
    "Ställningstaganden": "#3f7a70",
    "Lagkommentarer": "#933659",
    "Begrepp": "#6b7f3f",
    "EU-rättsakter": "#2d5f8a",
    "EU-domar": "#5677a8",
    "EU-fördrag": "#1f4d68",
    "EU-vägledning": "#4d7291",
    "Konventioner": "#4f4a68",
    "Folkrättslig praxis": "#87423e",
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
  }
  addEventListener("resize", () => { resize(); reheat(.3); });
  const toWorld = (sx, sy) => [(sx - tx) / scale, (sy - ty) / scale];
  function reheat(v) { alpha = Math.max(alpha, v); }

  function addNode(id, props) {
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
    select(uri);
    for (let level = 2; level <= depth; level++) {
      await expandFrontier(gen, level);
      if (gen !== generation) return;
    }
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

    ctx.textAlign = "center";
    for (const n of nodes.values()) {
      const linked = focus && (edges.has(focus + "→" + n.id) ||
                               edges.has(n.id + "→" + focus));
      const show = n.hop === 0 || n.id === selected || n.id === hovered ||
        linked || (n.type === "unit" ? scale > 1.5 : scale * n.r > 8.5);
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
  function frame(now) { tick(); draw(now); requestAnimationFrame(frame); }

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
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (active.has(name) ? "" : " off");
      chip.innerHTML = '<span class="dot" style="background:' + colorOf(name) +
                       '"></span>' + esc(name);
      chip.addEventListener("click", () => {
        if (active.has(name)) active.delete(name);
        else active.add(name);
        if (!active.size) active = new Set(GROUPS);
        rebuildFiltered();
      });
      legend.appendChild(chip);
    }
  }

  /* ---------------- panel ---------------- */
  const panel = mount.querySelector(".graf-panel"),
        panelBody = panel.querySelector(".graf-panel-body");
  panel.querySelector(".graf-close").addEventListener("click", closePanel);
  function closePanel() { panel.hidden = true; selected = null; }
  const pagePath = uri => uri.replace("https://lagen.nu", "") || "/";
  function rowHtml(nb) {
    return '<li data-uri="' + esc(nb.uri) + '"><span class="dot" style="background:'
      + colorOf(nb.group) + '"></span><span class="lb">'
      + esc(nb.descriptive || nb.label || nb.uri) + '</span><span class="ti">'
      + esc(nb.title || "") + '</span><span class="n">' + fmt(nb.n) + "</span></li>";
  }
  function sideHtml(heading, side) {
    if (!side) return "";
    const more = side.total_docs - side.top.length;
    return "<h3>" + heading + "</h3><ul>" + side.top.map(rowHtml).join("") +
      "</ul>" + (more > 0 ? '<div class="g-more">+ ' + fmt(more) +
                 " dokument till</div>" : "") +
      (side.unresolved ? '<div class="g-more">' + fmt(side.unresolved) +
       " hänvisningar pekar utanför korpuset</div>" : "");
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
  function panelFrame(d, headline, sub) {
    return '<div class="eyebrow"><span class="dot" style="background:'
      + colorOf(d.group) + '"></span>' + esc(d.group) + "</div><h2>"
      + esc(headline) + '</h2><div class="g-title">' + esc(sub || "") + "</div>";
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
    panel.hidden = false;
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
    const headline = d.pinpoint
      ? d.pinpoint + " · " + (d.label || "") : (d.label || d.uri);
    let internalNote = "";
    if (d.internal && d.internal.edges.length) {
      const shown = Math.min(MAX_UNITS, d.internal.nodes.length);
      internalNote = '<div class="g-more">' + fmt(d.internal.edges.length) +
        " interna hänvisningar mellan " + fmt(d.internal.nodes.length) +
        " enheter" + (shown < d.internal.nodes.length
          ? " (de " + fmt(shown) + " mest sammanlänkade ritas)" : "") + "</div>";
    }
    panelBody.innerHTML = panelFrame(d, headline, d.title) + numsHtml(d) +
      internalNote +
      sideHtml("Hänvisar hit", direction === "out" ? null : d.inbound) +
      sideHtml("Hänvisar vidare till", direction === "in" ? null : d.outbound) +
      actionsHtml(d, true);
    wire();
  }
  function openDocPanel(d) {
    panelBody.innerHTML = panelFrame(d, d.label || d.uri, d.title) + numsHtml(d) +
      sideHtml("Hänvisar hit", d.inbound) +
      sideHtml("Hänvisar vidare till", d.outbound) + actionsHtml(d, false);
    wire();
  }
  function openUnitPanel(n) {
    fetchGraph(center.root + "#" + n.anchor, "both", 12).then(d => {
      if (selected !== n.id) return;
      panelBody.innerHTML =
        panelFrame(d, (d.pinpoint || n.label) + " · " + (d.label || ""), d.title)
        + numsHtml(d) + sideHtml("Hänvisar hit", d.inbound)
        + sideHtml("Hänvisar vidare till", d.outbound) + actionsHtml(d, false);
      wire();
    });
  }

  /* ---------------- search (recenter) ---------------- */
  const input = mount.querySelector(".graf-search input"),
        hits = mount.querySelector(".graf-hits");
  let hitRows = [], hitIndex = -1, timer = null;
  function hitTarget(res) {          // a citation-resolved hit centers on its provision
    const frag = res.fragments && res.fragments[0];
    return frag && frag.uri && frag.uri.includes("#") ? frag.uri : res.uri;
  }
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = input.value.trim();
      if (text.length < 2) { hits.hidden = true; return; }
      const r = await fetch("/api/v1/search?q=" + encodeURIComponent(text) +
                            "&limit=8");
      if (!r.ok) return;
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
