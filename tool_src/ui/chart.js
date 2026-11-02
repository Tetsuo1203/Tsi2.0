/* ETF-Timing-Studio -- schlanke SVG-Diagrammbibliothek (ohne externe Abhängigkeiten) */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(tip));

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(tag, cls, parent, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* ---------------------------------------------------------------- Linien */
  function lineChart(host, cfg) {
    host._cfg = cfg;
    host.innerHTML = "";
    const W = Math.max(320, host.clientWidth || 600);
    const H = Math.max(140, host.clientHeight || 300);
    const m = { l: cfg.left || 64, r: 16, t: 14, b: 28 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }, host);
    const defs = el("defs", null, svg);

    const series = (cfg.series || []).filter(s => s && s.values && s.values.length);
    if (!series.length) { h("div", "hint", host, "keine Daten"); return; }
    const n = series[0].values.length;
    const xOf = i => m.l + (n <= 1 ? 0 : (i / (n - 1)) * iw);

    // Wertebereich
    let vals = [];
    for (const s of series) for (const v of s.values) if (v !== null && v !== undefined && isFinite(v)) vals.push(v);
    if (cfg.hlines) for (const hl of cfg.hlines) if (isFinite(hl.value)) vals.push(hl.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (cfg.log) { lo = Math.max(1e-6, lo); hi = Math.max(hi, lo * 1.001); }
    else { const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad; }
    if (cfg.yMin !== undefined) lo = cfg.yMin;
    if (cfg.yMax !== undefined) hi = cfg.yMax;
    if (lo === hi) { lo -= 1; hi += 1; }
    const log = !!cfg.log;
    const yOf = v => {
      if (log) {
        const a = Math.log(lo), b = Math.log(hi);
        return m.t + ih - ((Math.log(Math.max(v, lo)) - a) / (b - a)) * ih;
      }
      return m.t + ih - ((v - lo) / (hi - lo)) * ih;
    };

    // Hintergrundbänder (z. B. Signalphasen)
    if (cfg.bands) {
      for (const b of cfg.bands) {
        const x0 = xOf(clamp(b.from, 0, n - 1)), x1 = xOf(clamp(b.to, 0, n - 1));
        el("rect", { x: x0, y: m.t, width: Math.max(1, x1 - x0), height: ih, fill: b.color }, svg);
      }
    }

    // Gitter + Y-Beschriftung
    const ticks = 5;
    for (let k = 0; k <= ticks; k++) {
      const v = log
        ? Math.exp(Math.log(lo) + (k / ticks) * (Math.log(hi) - Math.log(lo)))
        : lo + (k / ticks) * (hi - lo);
      const y = yOf(v);
      el("line", { x1: m.l, y1: y, x2: W - m.r, y2: y, stroke: "#1a2a41", "stroke-width": 1 }, svg);
      const t = el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", fill: "#6b81a0",
        "font-size": 10.5, "font-family": "Segoe UI, system-ui" }, svg);
      t.textContent = cfg.yFmt ? cfg.yFmt(v) : String(Math.round(v));
    }

    // Nulllinie
    if (cfg.zeroLine && lo < 0 && hi > 0) {
      el("line", { x1: m.l, y1: yOf(0), x2: W - m.r, y2: yOf(0), stroke: "#39536f",
        "stroke-width": 1, "stroke-dasharray": "3 3" }, svg);
    }
    // Horizontale Schwellen
    if (cfg.hlines) for (const hl of cfg.hlines) {
      if (!isFinite(hl.value) || hl.value < lo || hl.value > hi) continue;
      const y = yOf(hl.value);
      el("line", { x1: m.l, y1: y, x2: W - m.r, y2: y, stroke: hl.color || "#64748b",
        "stroke-width": 1, "stroke-dasharray": hl.dash || "5 4", opacity: .85 }, svg);
      if (hl.label) {
        const t = el("text", { x: W - m.r - 2, y: y - 4, "text-anchor": "end",
          fill: hl.color || "#64748b", "font-size": 10 }, svg);
        t.textContent = hl.label;
      }
    }

    // Serien
    series.forEach((s, si) => {
      const color = s.color || "#22d3ee";
      let d = "", pen = false;
      for (let i = 0; i < s.values.length; i++) {
        const v = s.values[i];
        if (v === null || v === undefined || !isFinite(v)) { pen = false; continue; }
        d += (pen ? "L" : "M") + xOf(i).toFixed(1) + "," + yOf(v).toFixed(1) + " ";
        pen = true;
      }
      if (s.fill) {
        const gid = "grad" + si + "_" + Math.random().toString(36).slice(2, 7);
        const g = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
        el("stop", { offset: "0%", "stop-color": color, "stop-opacity": s.fillOpacity || .28 }, g);
        el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }, g);
        let area = d + `L${xOf(s.values.length - 1).toFixed(1)},${(m.t + ih).toFixed(1)} ` +
          `L${xOf(0).toFixed(1)},${(m.t + ih).toFixed(1)} Z`;
        el("path", { d: area, fill: `url(#${gid})`, stroke: "none" }, svg);
      }
      el("path", { d, fill: "none", stroke: color, "stroke-width": s.width || 1.8,
        "stroke-dasharray": s.dash || null, "stroke-linejoin": "round", "stroke-linecap": "round",
        opacity: s.opacity || 1 }, svg);
    });

    // X-Beschriftung
    const xs = cfg.x || [];
    const xTickCount = Math.min(8, Math.max(2, Math.floor(iw / 90)));
    for (let k = 0; k <= xTickCount; k++) {
      const i = Math.round((k / xTickCount) * (n - 1));
      const lab = xs[i] || "";
      const t = el("text", { x: xOf(i), y: H - 8, "text-anchor": k === 0 ? "start" : (k === xTickCount ? "end" : "middle"),
        fill: "#6b81a0", "font-size": 10.5 }, svg);
      t.textContent = cfg.xFmt ? cfg.xFmt(lab) : lab;
    }

    // Interaktion
    const focus = el("g", { opacity: 0 }, svg);
    const vline = el("line", { y1: m.t, y2: m.t + ih, stroke: "#5b7799", "stroke-width": 1,
      "stroke-dasharray": "3 3" }, focus);
    const dots = series.map(s => el("circle", { r: 3.4, fill: s.color || "#22d3ee",
      stroke: "#0b1524", "stroke-width": 1.5 }, focus));
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);

    function showAt(evt) {
      const r = svg.getBoundingClientRect();
      const px = (evt.clientX - r.left) * (W / r.width);
      let i = Math.round(((px - m.l) / iw) * (n - 1));
      i = clamp(i, 0, n - 1);
      focus.setAttribute("opacity", 1);
      vline.setAttribute("x1", xOf(i)); vline.setAttribute("x2", xOf(i));
      let rows = "";
      series.forEach((s, si) => {
        const v = s.values[i];
        if (v === null || v === undefined || !isFinite(v)) { dots[si].setAttribute("opacity", 0); return; }
        dots[si].setAttribute("opacity", 1);
        dots[si].setAttribute("cx", xOf(i)); dots[si].setAttribute("cy", yOf(v));
        rows += `<div class="tip-row"><span class="tip-k"><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color || "#22d3ee"};margin-right:6px"></i>${s.label || ""}</span><b>${cfg.yFmt ? cfg.yFmt(v) : v}</b></div>`;
      });
      tip.innerHTML = `<div style="color:#8ba0bd;margin-bottom:4px">${cfg.xFmt ? cfg.xFmt(xs[i] || "") : (xs[i] || "")}</div>` + rows;
      tip.style.opacity = 1;
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = clamp(evt.clientX + 14, 8, window.innerWidth - tw - 8) + "px";
      tip.style.top = clamp(evt.clientY - th - 12, 8, window.innerHeight - th - 8) + "px";
    }
    hit.addEventListener("mousemove", showAt);
    hit.addEventListener("mouseleave", () => { focus.setAttribute("opacity", 0); tip.style.opacity = 0; });
    host._cleanup = () => { tip.style.opacity = 0; };
  }

  /* ---------------------------------------------------------------- Balken */
  function barChart(host, cfg) {
    host._cfg = cfg;
    host.innerHTML = "";
    const W = Math.max(320, host.clientWidth || 600);
    const H = Math.max(140, host.clientHeight || 300);
    const m = { l: 60, r: 14, t: 14, b: 34 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }, host);
    const cats = cfg.categories || [];
    const series = cfg.series || [];
    if (!cats.length) { h("div", "hint", host, "keine Daten"); return; }
    let vals = [0];
    for (const s of series) for (const v of s.values) if (v !== null && isFinite(v)) vals.push(v);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * .12 || .1;
    lo -= hi > 0 ? pad : 0; hi += pad;
    const yOf = v => m.t + ih - ((v - lo) / (hi - lo)) * ih;
    const bandW = iw / cats.length;
    const bw = Math.min(22, (bandW * .78) / Math.max(1, series.length));

    for (let k = 0; k <= 4; k++) {
      const v = lo + (k / 4) * (hi - lo);
      const y = yOf(v);
      el("line", { x1: m.l, y1: y, x2: W - m.r, y2: y, stroke: "#1a2a41" }, svg);
      const t = el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", fill: "#6b81a0",
        "font-size": 10.5 }, svg);
      t.textContent = cfg.yFmt ? cfg.yFmt(v) : Math.round(v);
    }
    el("line", { x1: m.l, y1: yOf(0), x2: W - m.r, y2: yOf(0), stroke: "#39536f" }, svg);

    cats.forEach((c, i) => {
      const cx = m.l + bandW * i + bandW / 2;
      series.forEach((s, si) => {
        const v = s.values[i];
        if (v === null || !isFinite(v)) return;
        const x = cx - (series.length * bw) / 2 + si * bw;
        const y0 = yOf(0), y1 = yOf(v);
        el("rect", { x: x + 1, y: Math.min(y0, y1), width: Math.max(1, bw - 2),
          height: Math.max(1, Math.abs(y1 - y0)), fill: s.color, rx: 2, opacity: s.opacity || .95 }, svg);
      });
      const t = el("text", { x: cx, y: H - 10, "text-anchor": "middle", fill: "#6b81a0",
        "font-size": 10 }, svg);
      t.textContent = c;
    });

    // einfacher Tooltip je Balkengruppe
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", evt => {
      const r = svg.getBoundingClientRect();
      const px = (evt.clientX - r.left) * (W / r.width);
      const i = clamp(Math.floor((px - m.l) / bandW), 0, cats.length - 1);
      let rows = "";
      series.forEach(s => {
        const v = s.values[i];
        rows += `<div class="tip-row"><span class="tip-k">${s.label}</span><b>${v === null || !isFinite(v) ? "–" : (cfg.yFmt ? cfg.yFmt(v) : v)}</b></div>`;
      });
      tip.innerHTML = `<div style="color:#8ba0bd;margin-bottom:4px">${cats[i]}</div>` + rows;
      tip.style.opacity = 1;
      tip.style.left = clamp(evt.clientX + 14, 8, innerWidth - tip.offsetWidth - 8) + "px";
      tip.style.top = clamp(evt.clientY - tip.offsetHeight - 12, 8, innerHeight - tip.offsetHeight - 8) + "px";
    });
    hit.addEventListener("mouseleave", () => tip.style.opacity = 0);
  }

  /* --------------------------------------------------------------- Heatmap */
  function colorFor(v, maxAbs) {
    if (v === null || v === undefined || !isFinite(v)) return "#122033";
    const a = clamp(Math.abs(v) / (maxAbs || .12), 0, 1);
    const alpha = 0.12 + a * 0.68;
    return v >= 0 ? `rgba(52,211,153,${alpha})` : `rgba(248,113,113,${alpha})`;
  }

  function heatmap(host, cfg) {
    host.innerHTML = "";
    const years = cfg.years || [], months = cfg.months || [], values = cfg.values || [];
    if (!years.length) { h("div", "hint", host, "keine Daten"); return; }
    let maxAbs = 0;
    values.forEach(r => r.forEach(v => { if (v !== null && isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v)); }));
    const table = h("table", null, host);
    const thead = h("thead", null, table);
    const hr = h("tr", null, thead);
    const rowLabel = cfg.rowLabel || "Jahr";
    const numeric = cfg.fmt === "num";
    h("th", null, hr, rowLabel);
    months.forEach(mo => h("th", null, hr, mo));
    if (!numeric) h("th", null, hr, rowLabel);
    const tbody = h("tbody", null, table);
    const scale = cfg.scaleAbs || (numeric ? (maxAbs || 1) : 0.12);
    years.forEach((y, ri) => {
      const tr = h("tr", null, tbody);
      h("td", "yr", tr, String(y));
      let sum = 1;
      values[ri].forEach((v, ci) => {
        const txt = (v === null || !isFinite(v)) ? "" : (numeric ? v.toFixed(2) : (v * 100).toFixed(1));
        const td = h("td", null, tr, txt);
        if (v !== null && isFinite(v)) {
          td.style.background = colorFor(v, scale);
          td.title = numeric
            ? `${rowLabel} ${y} · ${months[ci]}: ${v.toFixed(3)}`
            : `${months[ci]} ${y}: ${(v * 100).toFixed(2)} %`;
          if (!numeric) sum *= (1 + v);
        }
      });
      if (!numeric) {
        const td = h("td", null, tr, ((sum - 1) * 100).toFixed(1));
        td.style.background = colorFor(sum - 1, Math.max(maxAbs, .2));
        td.style.fontWeight = "650";
      }
    });
  }

  window.Charts = { lineChart, barChart, heatmap, colorFor };
})();
