// review UI: one canvas page, one side panel, plain fetch. No dependencies, no build step.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("page");
  const ctx = canvas.getContext("2d");
  const wrap = $("canvas-wrap");
  const labelInput = $("label");
  const statusEl = $("status");

  const HANDLE_PX = 8;     // handle size on screen
  const HIT_PX = 6;        // extra hit slop on screen
  const PAD_PX = 16;       // canvas-wrap padding
  const DRAG_PX = 4;       // screen px a press must travel before it becomes a drag

  const state = {
    session: null,
    page: null,            // current page JSON
    cands: [],             // current page candidates, manifest order
    cur: -1,               // index into cands
    image: null,
    scale: 1,
    drag: null,            // {mode, start:{x,y}, screen:{x,y}, orig:{x,y,w,h}, box:{x,y,w,h}, moved, pointerId}
  };

  // every mutating request (and the coverage refresh after it) runs through this one chain, in order;
  // a failed request shows its error inline and the chain carries on
  let queue = Promise.resolve();
  function enqueue(task) {
    const p = queue.then(task).catch((e) => setStatus(e.message, true));
    queue = p;
    return p;
  }

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const colours = {};
  function readColours() {
    for (const k of ["pending", "accepted", "rejected", "accent", "muted"]) colours[k] = css("--" + k);
  }

  // ---------------------------------------------------------------- api

  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      let msg = data && data.detail !== undefined ? data.detail : res.statusText;
      if (Array.isArray(msg)) msg = msg.map((d) => d.msg || JSON.stringify(d)).join("; ");
      throw new Error(`${res.status}: ${msg}`);
    }
    return data;
  }

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.classList.toggle("error", !!isError);
  }

  // ---------------------------------------------------------------- loading

  async function load() {
    readColours();
    try {
      state.session = await api("GET", "/api/session");
    } catch (e) {
      setStatus(e.message, true);
      return;
    }
    const s = state.session;
    $("sid").textContent = s.session_id;
    let stats = `x-height ${s.stats.x_height.toFixed(2)} em · half-gap ${s.stats.median_half_gap.toFixed(2)} em`;
    if (s.stats.flags.length) stats += ` · ${s.stats.flags.join(", ")}`;
    $("stats").textContent = stats;
    if (s.inconsistencies && s.inconsistencies.length) {
      setStatus(`warning: ${s.inconsistencies.join("; ")} · run \`review replay\``, true);
    }

    const sel = $("page-select");
    sel.innerHTML = "";
    for (const p of s.pages) {
      const o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.id;
      sel.appendChild(o);
    }
    sel.hidden = s.pages.length < 2;
    sel.addEventListener("change", () => { sel.blur(); showPage(sel.value); });
    if (s.pages.length) await showPage(s.pages[0].id);
    refreshCoverage();
  }

  function showPage(pageId) {
    const page = state.session.pages.find((p) => p.id === pageId);
    state.page = page;
    state.cands = page.candidates;
    state.cur = state.cands.length ? 0 : -1;
    cancelDrag();
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => { state.image = img; fit(); renderPanel(); resolve(); };
      img.onerror = () => { state.image = null; setStatus(`cannot load page image ${pageId}`, true); fit(); renderPanel(); resolve(); };
      img.src = `/api/page/${encodeURIComponent(pageId)}/image.png`;
    });
  }

  // ---------------------------------------------------------------- canvas geometry

  function fit() {
    if (!state.page) return;
    const avail = Math.max(200, wrap.clientWidth - 2 * PAD_PX);
    state.scale = avail / state.page.width_px;
    const dpr = window.devicePixelRatio || 1;
    const w = state.page.width_px * state.scale;
    const h = state.page.height_px * state.scale;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    draw();
  }

  const current = () => (state.cur >= 0 ? state.cands[state.cur] : null);

  function boxOf(c) {
    if (state.drag && c === current()) return state.drag.box;
    return c.decision.bbox;
  }

  function toPage(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / state.scale, y: (ev.clientY - r.top) / state.scale };
  }

  // 8 handles: [id, fx, fy] as fractions of the box
  const HANDLES = [
    ["nw", 0, 0], ["n", 0.5, 0], ["ne", 1, 0], ["e", 1, 0.5],
    ["se", 1, 1], ["s", 0.5, 1], ["sw", 0, 1], ["w", 0, 0.5],
  ];
  const CURSORS = {
    nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
    n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", move: "move",
  };

  // handle size on screen: HANDLE_PX, shrunk (to 5 px at least) so small boxes are not buried
  const handlePx = (b) => Math.max(5, Math.min(HANDLE_PX, (Math.min(b.w, b.h) * state.scale) / 3));

  function handleAt(p, b) {
    const r = (handlePx(b) / 2 + HIT_PX / 2) / state.scale;
    for (const [id, fx, fy] of HANDLES) {
      const hx = b.x + fx * b.w, hy = b.y + fy * b.h;
      if (Math.abs(p.x - hx) <= r && Math.abs(p.y - hy) <= r) return id;
    }
    return null;
  }

  const inside = (p, b, slop = 0) =>
    p.x >= b.x - slop && p.x <= b.x + b.w + slop && p.y >= b.y - slop && p.y <= b.y + b.h + slop;

  function candidateAt(p) {
    // the smallest box containing the point wins (so nested/overlapping boxes stay selectable)
    let best = -1, area = Infinity;
    state.cands.forEach((c, i) => {
      const b = boxOf(c);
      if (inside(p, b) && b.w * b.h < area) { best = i; area = b.w * b.h; }
    });
    return best;
  }

  // ---------------------------------------------------------------- drawing

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const s = state.scale;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.page) return;
    ctx.setTransform(dpr * s, 0, 0, dpr * s, 0, 0);
    if (state.image) ctx.drawImage(state.image, 0, 0, state.page.width_px, state.page.height_px);

    const px = (n) => n / s;  // screen px -> page px

    // regions: faint outline and baseline
    ctx.save();
    ctx.strokeStyle = "rgba(47, 111, 219, 0.12)";
    ctx.lineWidth = px(1);
    for (const r of state.page.regions) {
      ctx.strokeRect(r.bbox.x, r.bbox.y, r.bbox.w, r.bbox.h);
      ctx.setLineDash([px(4), px(4)]);
      ctx.beginPath();
      ctx.moveTo(r.bbox.x, r.baseline_y);
      ctx.lineTo(r.bbox.x + r.bbox.w, r.baseline_y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();

    const cur = current();
    ctx.font = `${px(11)}px ${css("--mono") || "monospace"}`;
    ctx.textBaseline = "bottom";
    for (const c of state.cands) {
      if (c === cur) continue;
      drawBox(c, false);
    }
    if (cur) drawBox(cur, true);
  }

  function drawBox(c, isCurrent) {
    const s = state.scale;
    const px = (n) => n / s;
    const b = boxOf(c);
    const colour = colours[c.decision.status] || colours.pending;
    ctx.save();
    ctx.strokeStyle = colour;
    ctx.lineWidth = px(isCurrent ? 2.5 : 1.25);
    ctx.setLineDash(c.status === "needs_review" ? [px(5), px(3)] : []);
    if (isCurrent) {
      ctx.fillStyle = "rgba(47, 111, 219, 0.08)";
      ctx.fillRect(b.x, b.y, b.w, b.h);
    }
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    ctx.setLineDash([]);

    // label above the box
    ctx.fillStyle = colour;
    ctx.globalAlpha = isCurrent ? 1 : 0.8;
    ctx.fillText(c.decision.label, b.x, b.y - px(2));
    ctx.globalAlpha = 1;

    if (isCurrent) {
      const o = c.original_bbox;
      if (o.x !== b.x || o.y !== b.y || o.w !== b.w || o.h !== b.h) {
        ctx.strokeStyle = colours.muted;
        ctx.lineWidth = px(1);
        ctx.setLineDash([px(2), px(2)]);
        ctx.strokeRect(o.x, o.y, o.w, o.h);
        ctx.setLineDash([]);
      }
      const hs = px(handlePx(b));
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = colours.accent;
      ctx.lineWidth = px(1.5);
      for (const [, fx, fy] of HANDLES) {
        const hx = b.x + fx * b.w - hs / 2, hy = b.y + fy * b.h - hs / 2;
        ctx.fillRect(hx, hy, hs, hs);
        ctx.strokeRect(hx, hy, hs, hs);
      }
    }
    ctx.restore();
  }

  // ---------------------------------------------------------------- panel

  function badge(cls, text) {
    const el = document.createElement("span");
    el.className = `badge st-${cls}`;
    el.textContent = text;
    return el;
  }

  function renderProgress() {
    const total = state.cands.length;
    const done = state.cands.filter((x) => x.decision.status !== "pending").length;
    $("progress").textContent = total ? `${state.cur + 1} / ${total} · ${done} reviewed` : "no candidates";
  }

  function renderPanel() {
    const c = current();
    renderProgress();
    const crop = $("crop");
    crop.innerHTML = "";
    if (!c) {
      crop.innerHTML = '<span class="none">—</span>';
      for (const id of ["c-id", "c-conf", "c-ingest", "c-status", "c-ctx"]) $(id).textContent = "—";
      labelInput.value = "";
      $("flags").innerHTML = "";
      draw();
      return;
    }
    const img = document.createElement("img");
    img.alt = `crop of ${c.id}`;
    img.onerror = () => { crop.innerHTML = '<span class="none">no crop</span>'; };
    img.src = `/api/candidate/${encodeURIComponent(c.id)}/crop.png`;
    crop.appendChild(img);

    const d = c.decision;
    $("c-id").textContent = c.id;
    $("c-conf").textContent = c.confidence.toFixed(2);
    $("c-ingest").replaceChildren(badge(c.status, c.status.replace("_", " ")));
    $("c-status").replaceChildren(badge(d.status, d.status));

    const ctxEl = $("c-ctx");
    ctxEl.innerHTML = "";
    const parts = [];
    if (c.context.left_neighbor) parts.push(["nb", c.context.left_neighbor + " ‹ "]);
    parts.push(["word", c.context.source_word]);
    if (c.context.right_neighbor) parts.push(["nb", " › " + c.context.right_neighbor]);
    parts.push(["nb", `  (${c.context.position_in_word})`]);
    for (const [cls, text] of parts) {
      const sp = document.createElement("span");
      sp.className = cls;
      sp.textContent = text;
      ctxEl.appendChild(sp);
    }

    if (document.activeElement !== labelInput) labelInput.value = d.label;
    for (const r of document.querySelectorAll('input[name="quality"]')) r.checked = r.value === d.quality;

    const flags = $("flags");
    flags.innerHTML = "";
    if (!d.flags.length) {
      const li = document.createElement("li");
      li.className = "none";
      li.textContent = "no flags";
      flags.appendChild(li);
    }
    for (const f of d.flags) {
      const li = document.createElement("li");
      li.textContent = f;
      flags.appendChild(li);
    }
    draw();
  }

  function select(i, scroll) {
    if (i < 0 || i >= state.cands.length) return;
    cancelDrag();
    state.cur = i;
    labelInput.value = state.cands[i].decision.label;
    labelInput.blur();
    renderPanel();
    if (scroll) scrollIntoView(boxOf(state.cands[i]));
  }

  function scrollIntoView(b) {
    const s = state.scale, m = 60;
    const top = b.y * s + PAD_PX, bottom = (b.y + b.h) * s + PAD_PX;
    const left = b.x * s + PAD_PX, right = (b.x + b.w) * s + PAD_PX;
    if (top - m < wrap.scrollTop) wrap.scrollTop = top - m;
    else if (bottom + m > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = bottom + m - wrap.clientHeight;
    if (left - m < wrap.scrollLeft) wrap.scrollLeft = left - m;
    else if (right + m > wrap.scrollLeft + wrap.clientWidth) wrap.scrollLeft = right + m - wrap.clientWidth;
  }

  // the next candidate after the current one (wrapping) whose decision is pending; -1 when none
  function nextPending() {
    const n = state.cands.length;
    for (let k = 1; k < n; k++) {
      const i = (state.cur + k) % n;
      if (state.cands[i].decision.status === "pending") return i;
    }
    return -1;
  }

  function advance() {
    const i = nextPending();
    if (i >= 0) select(i, true);
  }

  // ---------------------------------------------------------------- coverage

  async function refreshCoverage() {
    let cov;
    try {
      cov = await api("GET", "/api/coverage");
    } catch (e) {
      setStatus(e.message, true);
      return;
    }
    $("cov-title").textContent = `Coverage · ${cov.met_count}/${cov.total} met`;
    const list = $("cov-list");
    list.innerHTML = "";
    if (!cov.lines.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "no targets";
      list.appendChild(li);
    }
    for (const l of cov.lines) {  // server order: unmet first
      const li = document.createElement("li");
      if (l.met) li.className = "met";
      const left = document.createElement("span");
      left.textContent = `${l.label}: ${l.accepted}/${l.required}`;
      if (l.met) {
        const ok = document.createElement("span");
        ok.className = "ok";
        ok.textContent = " ✓";
        left.appendChild(ok);
      }
      const right = document.createElement("span");
      right.className = "pend";
      right.textContent = l.pending > 0 ? `+${l.pending} pending` : "";
      li.append(left, right);
      list.appendChild(li);
    }
    $("cov-untargeted").textContent = cov.untargeted.length
      ? "untargeted: " + cov.untargeted.map(([lab, n]) => `${lab}×${n}`).join(", ")
      : "";
  }

  // ---------------------------------------------------------------- actions

  function act(action, body, cand) {
    const c = cand || current();
    if (!c) return Promise.resolve();
    const done = enqueue(async () => {
      const d = await api("POST", `/api/candidate/${encodeURIComponent(c.id)}/${action}`, body);
      c.decision = d;
      setStatus(`${action}: ${c.id} ‘${d.label}’ → ${d.status}${d.flags.length ? " (flagged)" : ""}`);
    }).then(() => { if (c === current()) renderPanel(); else { renderProgress(); draw(); } });
    enqueue(refreshCoverage);
    return done;
  }

  // a and r move on to the next pending candidate at once; the request keeps its own candidate
  const accept = () => { if (current()) { act("accept"); advance(); } };
  const rejectCur = () => { if (current()) { act("reject"); advance(); } };
  const resetCur = () => act("reset");
  const setQuality = (q) => act("quality", { quality: q });

  function submitLabel() {
    const c = current();
    if (!c) return;
    const label = labelInput.value;
    labelInput.blur();
    if (label === c.decision.label) return;
    act("relabel", { label });
  }

  // ---------------------------------------------------------------- mouse

  function cancelDrag() {
    const dr = state.drag;
    if (!dr) return;
    state.drag = null;
    try { canvas.releasePointerCapture(dr.pointerId); } catch (_) { /* already released */ }
    draw();
  }

  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || !state.page) return;
    const p = toPage(ev);
    const c = current();
    if (c) {
      const b = c.decision.bbox;
      const h = handleAt(p, b);
      if (h || inside(p, b)) {
        state.drag = {
          mode: h || "move", start: p, screen: { x: ev.clientX, y: ev.clientY },
          orig: { ...b }, box: { ...b }, moved: false, pointerId: ev.pointerId,
        };
        canvas.setPointerCapture(ev.pointerId);
        ev.preventDefault();
        return;
      }
    }
    const i = candidateAt(p);
    if (i >= 0) select(i, false);
  });

  canvas.addEventListener("pointermove", (ev) => {
    if (!state.page) return;
    const p = toPage(ev);
    const dr = state.drag;
    if (!dr) {
      const c = current();
      let cursor = "default";
      if (c) {
        const h = handleAt(p, c.decision.bbox);
        if (h) cursor = CURSORS[h];
        else if (inside(p, c.decision.bbox)) cursor = "move";
        else if (candidateAt(p) >= 0) cursor = "pointer";
      }
      canvas.style.cursor = cursor;
      return;
    }
    if (!dr.moved && Math.hypot(ev.clientX - dr.screen.x, ev.clientY - dr.screen.y) < DRAG_PX) return;
    dr.moved = true;
    const dx = p.x - dr.start.x, dy = p.y - dr.start.y;
    const o = dr.orig;
    let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
    if (dr.mode === "move") { x0 += dx; x1 += dx; y0 += dy; y1 += dy; }
    else {
      if (dr.mode.includes("w")) x0 += dx;
      if (dr.mode.includes("e")) x1 += dx;
      if (dr.mode.includes("n")) y0 += dy;
      if (dr.mode.includes("s")) y1 += dy;
    }
    const min = 1;
    if (x1 - x0 < min) { if (dr.mode.includes("w")) x0 = x1 - min; else x1 = x0 + min; }
    if (y1 - y0 < min) { if (dr.mode.includes("n")) y0 = y1 - min; else y1 = y0 + min; }
    dr.box = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    draw();
  });

  canvas.addEventListener("pointerup", (ev) => {
    const dr = state.drag;
    if (!dr) return;
    const c = current();
    cancelDrag();
    if (!dr.moved || !c) {
      // a press without movement inside the current box: select the smallest box under it (nested boxes)
      const i = candidateAt(toPage(ev));
      if (i >= 0 && i !== state.cur) select(i, false);
      return;
    }
    const r = (n) => Math.round(n * 10) / 10;
    const b = dr.box;
    act("box", { x: r(b.x), y: r(b.y), w: Math.max(0.1, r(b.w)), h: Math.max(0.1, r(b.h)) }, c);
  });

  canvas.addEventListener("pointercancel", cancelDrag);

  // ---------------------------------------------------------------- keyboard and controls

  labelInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); submitLabel(); }
    else if (ev.key === "Escape") {
      ev.preventDefault();
      const c = current();
      if (c) labelInput.value = c.decision.label;
      labelInput.blur();
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && state.drag) { ev.preventDefault(); cancelDrag(); return; }
    if (document.activeElement === labelInput) return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (document.activeElement && document.activeElement.tagName === "SELECT") return;
    const keys = {
      a: accept,
      r: rejectCur,
      x: resetCur,
      j: () => select(state.cur + 1, true),
      k: () => select(state.cur - 1, true),
      n: advance,
      e: () => {
        const c = current();
        if (!c) return;
        labelInput.value = c.decision.label;
        labelInput.focus();
        labelInput.select();
      },
      g: () => setQuality("good"),
      m: () => setQuality("marginal"),
      b: () => setQuality("bad"),
    };
    const fn = keys[ev.key];
    if (!fn) return;
    ev.preventDefault();
    fn();
  });

  for (const r of document.querySelectorAll('input[name="quality"]')) {
    r.addEventListener("change", () => { if (r.checked) setQuality(r.value); r.blur(); });
  }
  $("btn-accept").addEventListener("click", (ev) => { ev.currentTarget.blur(); accept(); });
  $("btn-reject").addEventListener("click", (ev) => { ev.currentTarget.blur(); rejectCur(); });
  $("btn-reset").addEventListener("click", (ev) => { ev.currentTarget.blur(); resetCur(); });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fit, 80);
  });

  load();
})();
