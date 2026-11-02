/* ETF-Timing-Studio -- Anwendungslogik */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));
  const DE = "de-DE";

  const nf0 = new Intl.NumberFormat(DE, { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat(DE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat(DE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const eur = v => (v === null || v === undefined || !isFinite(v)) ? "–" : nf0.format(v) + " €";
  const eur2 = v => (v === null || v === undefined || !isFinite(v)) ? "–" : nf2.format(v) + " €";
  const pct = (v, d) => (v === null || v === undefined || !isFinite(v)) ? "–"
    : (d === 1 ? nf1 : nf2).format(v * 100) + " %";
  const num = (v, d) => (v === null || v === undefined || !isFinite(v)) ? "–"
    : (d === 0 ? nf0 : d === 1 ? nf1 : nf2).format(v);
  const dateDE = iso => {
    if (!iso) return "–";
    const p = iso.slice(0, 10).split("-");
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : iso;
  };
  const signCls = v => (v === null || v === undefined || !isFinite(v)) ? "" : (v >= 0 ? "pos" : "neg");

  const S = {
    boot: null, result: null, grid: null, wf: null, mc: null, mcOrigin: null,
    compare: null, tab: "overview", searchTarget: null, busy: false,
  };

  /* ------------------------------------------------------------------ API */
  async function apiGet(path) {
    const r = await fetch(path, { headers: { "Accept": "application/json" } });
    const j = await r.json().catch(() => ({ ok: false, error: "Antwort unlesbar" }));
    if (!r.ok || j.ok === false) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }
  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({ ok: false, error: "Antwort unlesbar" }));
    if (!r.ok || j.ok === false) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }

  function busy(on, msg) {
    S.busy = on;
    $("#overlay").classList.toggle("open", on);
    if (msg) $("#overlayMsg").textContent = msg;
    if (on) setStatus("busy", msg || "rechne …");
    else setStatus("ok", "bereit");
  }
  function setStatus(kind, text) {
    const p = $("#statusPill");
    p.className = "pill " + (kind || "");
    p.textContent = text || "bereit";
  }
  let toastTimer = null;
  function toast(msg, kind) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show " + (kind || "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast " + (kind || ""); }, 4200);
  }

  /* -------------------------------------------------------------- Formular */
  function setVal(id, v) { const e = $("#" + id); if (e) e.value = v; }
  function getVal(id) { const e = $("#" + id); return e ? e.value : ""; }
  function getNum(id, def) {
    const v = parseFloat(String(getVal(id)).replace(",", "."));
    return isFinite(v) ? v : def;
  }

  function readConfig() {
    const type = getVal("signalType");
    const params = {
      sma_days: getNum("pSma", 130),
      upper: getNum("pUpper", 1.05),
      lower: getNum("pLower", 0.92),
      days_red: getNum("pDaysRed", 19),
      days_green: getNum("pDaysGreen", 16),
      band: getNum("pBand", 0),
      sma_short: getNum("pSmaShort", 50),
      sma_long: getNum("pSmaLong", 200),
      momentum_days: getNum("pMom", 252),
    };
    if (type === "sma") params.sma_days = getNum("pSma2", 200);
    const cash = getVal("defMode") === "cash";
    return {
      risk: getVal("riskSym").trim(),
      signal: getVal("signalSym").trim(),
      defensive_is_cash: cash,
      defensive: cash ? null : getVal("defSym").trim(),
      signal_type: type,
      params,
      capital: getNum("capital", 10000),
      fee_fixed: getNum("feeFixed", 5.9),
      fee_pct: getNum("feePct", 0),
      slippage_bps: getNum("slippage", 5),
      tax_rate: getNum("taxRate", 26.375),
      tax_mode: getVal("taxMode"),
      loss_offset: true,
      cash_rate: getNum("cashRate", 2),
      exec_lag: parseInt(getVal("execLag"), 10) || 0,
      start: getVal("startDate") || null,
      end: getVal("endDate") || null,
      rotation: S.rotation || null,
    };
  }

  function applyConfig(c) {
    setVal("riskSym", c.risk || "");
    setVal("signalSym", c.signal || "");
    setVal("defMode", c.defensive_is_cash === false && c.defensive ? "etf" : "cash");
    setVal("defSym", c.defensive || "");
    $("#defRow").style.display = getVal("defMode") === "etf" ? "flex" : "none";
    setVal("signalType", c.signal_type || "ampel");
    const p = c.params || {};
    setVal("pSma", p.sma_days || 130);
    setVal("pUpper", p.upper || 1.05);
    setVal("pLower", p.lower || 0.92);
    setVal("pDaysRed", p.days_red || 19);
    setVal("pDaysGreen", p.days_green || 16);
    setVal("pSma2", p.sma_days || 200);
    setVal("pBand", p.band || 0);
    setVal("pSmaShort", p.sma_short || 50);
    setVal("pSmaLong", p.sma_long || 200);
    setVal("pMom", p.momentum_days || 252);
    setVal("capital", c.capital || 10000);
    setVal("feeFixed", c.fee_fixed !== undefined ? c.fee_fixed : 5.9);
    setVal("feePct", c.fee_pct !== undefined ? c.fee_pct : 0);
    setVal("slippage", c.slippage_bps !== undefined ? c.slippage_bps : 5);
    setVal("taxRate", c.tax_rate !== undefined ? c.tax_rate : 26.375);
    setVal("taxMode", c.tax_mode || "accrued");
    setVal("cashRate", c.cash_rate !== undefined ? c.cash_rate : 2);
    setVal("execLag", String(c.exec_lag === 0 ? 0 : 1));
    setVal("startDate", (c.start || "").slice(0, 10));
    setVal("endDate", (c.end || "").slice(0, 10));
    S.rotation = c.rotation || null;
    syncParamVisibility();
  }

  function syncParamVisibility() {
    const t = getVal("signalType");
    $("#paramsAmpel").style.display = t === "ampel" ? "block" : "none";
    $("#paramsSma").style.display = t === "sma" ? "block" : "none";
    $("#paramsDual").style.display = t === "dual_sma" ? "block" : "none";
    $("#paramsMom").style.display = t === "momentum" ? "block" : "none";
  }

  /* ------------------------------------------------------------- Bootstrap */
  async function init() {
    try {
      const b = await apiGet("/api/bootstrap");
      S.boot = b;
      $("#bootMsg").textContent = "Bereit";
      $("#boot").classList.add("hide");
      setStatus("ok", "verbunden");

      // Signaltypen
      const st = $("#signalType");
      st.innerHTML = "";
      b.signal_types.forEach(t => {
        if (t.id === "none") return;
        const o = document.createElement("option");
        o.value = t.id; o.textContent = t.name;
        st.appendChild(o);
      });

      // Presets
      const ps = $("#presetSel");
      ps.innerHTML = "";
      b.presets.forEach(p => {
        const o = document.createElement("option");
        o.value = p.id; o.textContent = p.name;
        ps.appendChild(o);
      });
      ps.addEventListener("change", () => {
        const p = b.presets.find(x => x.id === ps.value);
        if (!p) return;
        $("#presetHint").textContent = p.hint || "";
        applyConfig(p.config);
      });

      refreshCachedList();
      fillCompareSelect();
      applyConfig(b.presets[0].config);
      $("#presetHint").textContent = b.presets[0].hint;

      wireEvents();
      showTab((location.hash || "").replace("#", "") || "overview");
      if (b.cached && b.cached.length >= 2) {
        runBacktest();
      } else {
        $("#kpiRow").innerHTML = welcomeCard();
        setStatus("warn", "keine Daten");
      }
    } catch (e) {
      $("#bootMsg").textContent = "Fehler: " + e.message;
      setStatus("err", "Fehler");
      toast("Start fehlgeschlagen: " + e.message, "err");
    }
  }

  function welcomeCard() {
    return `<div class="panel" style="grid-column:1/-1">
      <div class="panel-title">Willkommen</div>
      <p class="muted" style="margin:8px 0">Es sind noch keine Kursdaten im Datenbestand.
      Lade zuerst die Standard-Instrumente (MSCI World + S&amp;P 500) – das dauert nur wenige Sekunden.</p>
      <button class="btn primary" id="btnSeed">Standard-Daten laden</button>
      <div class="hint">Alternativ: eigener CSV-Import oder beliebige Yahoo-Symbole im Reiter „Daten“.</div>
    </div>`;
  }

  function wireEvents() {
    $("#btnRun").addEventListener("click", () => runBacktest());
    $("#btnGrid").addEventListener("click", () => { showTab("params"); runGrid(); });
    $("#btnWf").addEventListener("click", () => { showTab("robust"); runWalkforward(); });
    $("#btnWf2").addEventListener("click", () => runWalkforward());
    $("#btnMc").addEventListener("click", () => runMonteCarlo());
    $("#btnSens").addEventListener("click", () => runSensitivity());
    $("#btnCompare").addEventListener("click", () => runCompare());
    $("#btnExport").addEventListener("click", () => exportCsv());
    $("#btnShutdown").addEventListener("click", shutdown);
    $("#signalType").addEventListener("change", syncParamVisibility);
    $("#defMode").addEventListener("change", () => {
      $("#defRow").style.display = getVal("defMode") === "etf" ? "flex" : "none";
    });
    $("#logScale").addEventListener("change", () => {
      if (S.result) drawEquity(S.result);
    });
    $("#tradeFilter").addEventListener("change", () => { if (S.result) renderTrades(S.result); });
    $("#btnRefreshData").addEventListener("click", () => refreshDataView(true));
    $("#btnDownload").addEventListener("click", () => downloadSymbols());
    $("#impFile").addEventListener("change", (e) => importCsv(e.target.files[0]));
    $$("#tabs .tab").forEach(t => t.addEventListener("click", () => showTab(t.dataset.view)));
    $$("[data-search]").forEach(b => b.addEventListener("click", () => openSearch(b.dataset.search)));
    $("#searchClose").addEventListener("click", closeSearch);
    $("#searchModal").addEventListener("click", e => { if (e.target.id === "searchModal") closeSearch(); });
    let tmr = null;
    $("#searchInput").addEventListener("input", () => {
      clearTimeout(tmr);
      tmr = setTimeout(doSearch, 300);
    });
    $("#searchInput").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeSearch(); });
    document.body.addEventListener("click", e => {
      if (e.target.id === "btnSeed") seedDefaults();
    });
    window.addEventListener("hashchange", () => {
      showTab((location.hash || "").replace("#", "") || "overview");
    });
    window.addEventListener("resize", () => {
      clearTimeout(window.__rz);
      window.__rz = setTimeout(() => { if (S.result) redrawCharts(); }, 220);
    });
  }

  function emptyHint(id, text) {
    const el = $("#" + id);
    if (el && !el.dataset.filled) { el.innerHTML = `<div class="hint" style="padding:14px">${text}</div>`; }
  }

  function showTab(name) {
    if (!name || !document.getElementById('view-' + name)) name = 'overview';
    S.tab = name;
    if (history.replaceState) history.replaceState(null, '', '#' + name);
    $$("#tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
    $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
    if (name === "data") refreshDataView();
    if (name === "params" && !S.grid) emptyHint("chartGrid", "Starte die Parameter-Suche, um die " +
      "Ergebnismatrix (RSL-Tage × Obergrenze) zu sehen.");
    if (name === "robust") {
      if (!S.wf) emptyHint("chartWf", "Walk-Forward starten, um die Out-of-Sample-Kurve zu sehen.");
      if (!S.mc) emptyHint("chartMc", "Monte-Carlo-Projektion starten.");
    }
    if (name === "compare") emptyHint("chartCompare", "Vergleich starten.");
    if (name === "compare") fillCompareSelect();
    if (S.result) {
      if (name === "overview") renderOverview(S.result);
      if (name === "signals") renderSignals(S.result);
      if (name === "periods") renderPeriods(S.result);
      if (name === "trades") renderTrades(S.result);
      if (name === "robust") renderRobust();
    }
    if (name === "params" && S.grid) renderGrid();
  }

  function redrawCharts() {
    if (!S.result) return;
    const tab = S.tab;
    if (tab === "overview") { drawEquity(S.result); drawDD(S.result); }
    if (tab === "signals") { drawSignal(S.result); drawRatio(S.result); }
    if (tab === "periods") drawYears(S.result);
    if (tab === "compare" && S.compare) drawCompare(S.compare);
    if (tab === "params" && S.grid) drawGrid(S.grid);
    if (tab === "robust") { if (S.wf && S.wf.oos) drawWf(S.wf); if (S.mc) drawMc(S.mc, S.mcOrigin); }
  }

  /* --------------------------------------------------------- Datenbestand */
  function fillDatalist() {
    const dl = $("#cachedList");
    if (!dl) return;
    dl.innerHTML = "";
    (S.boot ? S.boot.cached : []).forEach(c => {
      const o = document.createElement("option");
      o.value = c.symbol;
      o.label = c.name || c.symbol;
      dl.appendChild(o);
    });
  }
  function refreshCachedList() {
    fillDatalist();
    fillCompareSelect();
    refreshDataView();
  }
  function fillCompareSelect() {
    const sel = $("#compareSel");
    if (!sel) return;
    const prev = $$("#compareSel option").filter(o => o.selected).map(o => o.value);
    sel.innerHTML = "";
    (S.boot ? S.boot.cached : []).forEach(c => {
      const o = document.createElement("option");
      o.value = c.symbol;
      o.textContent = `${c.symbol} — ${c.name || ""} (${c.first || "?"} … ${c.last || "?"})`;
      if (prev.indexOf(c.symbol) >= 0) o.selected = true;
      sel.appendChild(o);
    });
    if (!prev.length) {
      $$("#compareSel option").slice(0, 4).forEach(o => { o.selected = true; });
    }
  }

  async function refreshDataView(force) {
    try {
      const b = await apiGet("/api/bootstrap");
      S.boot = b;
      fillDatalist();
      const rows = b.cached || [];
      const wrap = $("#cachedTable");
      if (!rows.length) {
        wrap.innerHTML = `<div class="hint" style="padding:10px">Noch keine Daten. Nutze „Kurse von Yahoo laden“ oder den CSV-Import.</div>`;
        return;
      }
      let html = `<table><thead><tr>
        <th>Symbol</th><th>Name</th><th>Quelle</th><th>von</th><th>bis</th>
        <th>Kurse</th><th>aktualisiert</th><th></th></tr></thead><tbody>`;
      rows.forEach(r => {
        html += `<tr>
          <td><b>${r.symbol}</b></td>
          <td class="dim txt">${r.name || ""}</td>
          <td class="dim">${r.source || ""}</td>
          <td class="dim">${dateDE(r.first)}</td>
          <td class="dim">${dateDE(r.last)}</td>
          <td>${r.rows}</td>
          <td class="dim">${(r.updated || "").replace("T", " ").slice(0, 16)}</td>
          <td><button class="btn mini" data-del="${r.symbol}">löschen</button></td>
        </tr>`;
      });
      html += "</tbody></table>";
      wrap.innerHTML = html;
      $$("[data-del]").forEach(b2 => b2.addEventListener("click", async () => {
        if (!confirm(`Daten für ${b2.dataset.del} löschen?`)) return;
        try {
          await apiPost("/api/delete", { symbol: b2.dataset.del });
          toast("Gelöscht: " + b2.dataset.del, "ok");
          refreshDataView();
        } catch (e) { toast("Löschen fehlgeschlagen: " + e.message, "err"); }
      }));
    } catch (e) { /* still */ }
  }

  async function downloadSymbols(symbols) {
    const list = symbols || getVal("dlInput").split(/[,\s;]+/).filter(Boolean);
    if (!list.length) { toast("Bitte Symbol(e) eingeben", "err"); return; }
    busy(true, "lade Kurse …");
    try {
      const j = await apiPost("/api/download", { symbols: list, force: false });
      const ok = j.results.filter(r => r.ok), bad = j.results.filter(r => !r.ok);
      $("#dlOut").innerHTML = ok.map(r => `✓ <b>${r.symbol}</b> — ${r.name} · ${r.rows} Kurse (${dateDE(r.first)} … ${dateDE(r.last)})`).join("<br>") +
        (bad.length ? "<br>" + bad.map(r => `<span class="neg">✗ ${r.symbol}: ${r.error}</span>`).join("<br>") : "");
      S.boot.cached = j.cached;
      fillDatalist(); fillCompareSelect(); refreshDataView();
      toast(`${ok.length} Instrument(e) geladen`, ok.length ? "ok" : "err");
    } catch (e) {
      toast("Download fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); setStatus("ok", "verbunden"); }
  }

  async function seedDefaults() {
    await downloadSymbols(["IWDA.AS", "^GSPC", "EXS1.DE"]);
    applyConfig(S.boot.presets[0].config);
    runBacktest();
  }

  async function importCsv(file) {
    if (!file) return;
    busy(true, "importiere CSV …");
    try {
      const text = await file.text();
      const name = getVal("impName").trim() || file.name.replace(/\.(csv|txt)$/i, "");
      const j = await apiPost("/api/import", { name, symbol: name, csv: text });
      $("#impOut").innerHTML = `✓ <b>${j.symbol}</b> importiert: ${j.rows} Kurse (${dateDE(j.first)} … ${dateDE(j.last)})`;
      S.boot.cached = j.cached;
      fillDatalist(); fillCompareSelect(); refreshDataView();
      setVal("riskSym", j.symbol);
      toast("CSV importiert", "ok");
    } catch (e) {
      $("#impOut").innerHTML = `<span class="neg">✗ ${e.message}</span>`;
      toast("Import fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  /* ------------------------------------------------------------ Backtest */
  async function runBacktest() {
    if (S.busy) return;
    const cfg = readConfig();
    if (!cfg.risk || !cfg.signal) { toast("Bitte Risiko- und Signal-Instrument angeben", "err"); return; }
    busy(true, "berechne Backtest …");
    try {
      const j = await apiPost("/api/backtest", { config: cfg });
      S.result = j.result;
      S.mc = null;
      renderOverview(S.result);
      renderSignals(S.result);
      renderPeriods(S.result);
      renderTrades(S.result);
      $("#metaLine").textContent = `${dateDE(S.result.meta.start)} – ${dateDE(S.result.meta.end)} · ` +
        `${S.result.meta.days} Handelstage · ${S.result.meta.risk_name || cfg.risk}`;
      showTab("overview");
      const m = S.result.metrics;
      toast(`Fertig: ${pct(m.cagr)} p.a. · max. Verlust ${pct(m.max_drawdown)}`, "ok");
      setStatus("ok", "bereit");
    } catch (e) {
      toast("Backtest fehlgeschlagen: " + e.message, "err");
      setStatus("err", "Fehler");
    } finally { busy(false); }
  }

  /* ------------------------------------------------------------ Render: KPI */
  function kpi(label, value, sub, cls, valCls) {
    return `<div class="kpi ${cls || ""}">
      <div class="k-lbl">${label}</div>
      <div class="k-val ${valCls || ""}">${value}</div>
      ${sub ? `<div class="k-sub">${sub}</div>` : ""}
    </div>`;
  }

  function renderOverview(res) {
    const m = res.metrics;
    const rows = [
      kpi("Endkapital", eur(m.end_capital), `Start ${eur(m.start_capital)}`,
        m.end_capital >= m.start_capital ? "good" : "bad", "acc"),
      kpi("Gesamtrendite", pct(m.total_return), `Benchmark ${pct(m.bench_total_return)}`,
        m.total_return >= 0 ? "good" : "bad", signCls(m.total_return)),
      kpi("CAGR p.a.", pct(m.cagr), `Benchmark ${pct(m.bench_cagr)} · Δ ${pct(m.excess_cagr)}`,
        m.cagr >= m.bench_cagr ? "good" : "bad", signCls(m.cagr)),
      kpi("Max. Drawdown", pct(m.max_drawdown), `Benchmark ${pct(m.bench_max_drawdown)}`,
        m.max_drawdown >= m.bench_max_drawdown ? "good" : "bad", "neg"),
      kpi("Sharpe", num(m.sharpe, 2), `Benchmark ${num(m.bench_sharpe, 2)}`, "neutral"),
      kpi("Calmar", num(m.calmar, 2), "CAGR / max. Verlust", "neutral"),
      kpi("Volatilität", pct(m.volatility), `Benchmark ${pct(m.bench_volatility)}`, "neutral"),
      kpi("Zeit im Markt", pct(m.time_in_market), `${m.trades} Trades · ${m.switches} Wechsel`, "neutral"),
      kpi("Kosten & Steuer", eur(m.fees + m.taxes),
        `${eur(m.fees)} Gebühr · ${eur(m.taxes)} Steuer (anteilig)`, "neutral"),
    ];
    $("#kpiRow").innerHTML = rows.join("");
    drawEquity(res);
    drawDD(res);
    renderDdList(res);
    renderMetricsTable(res);
  }

  function drawEquity(res) {
    const eq = res.equity;
    Charts.lineChart($("#chartEquity"), {
      x: eq.dates,
      xFmt: x => x.slice(0, 4),
      yFmt: v => nf0.format(v) + " €",
      log: $("#logScale").checked,
      zeroLine: false,
      series: [
        { label: "Strategie", values: eq.strategy, color: "#22d3ee", width: 2.1, fill: true },
        { label: "Buy & Hold", values: eq.benchmark, color: "#a78bfa", width: 1.4, dash: "5 4" },
      ],
    });
    $("#legendEquity").innerHTML =
      `<span><i style="background:#22d3ee"></i>Strategie</span>` +
      `<span><i style="background:#a78bfa"></i>Buy &amp; Hold</span>`;
    // Signalband
    const st = eq.state, rb = $("#ribbonEquity");
    let html = "", i = 0;
    while (i < st.length) {
      let j = i;
      while (j < st.length && st[j] === st[i]) j++;
      const w = ((j - i) / st.length) * 100;
      html += `<span style="width:${w}%;background:${st[i] ? "rgba(52,211,153,.55)" : "rgba(248,113,113,.45)"}"></span>`;
      i = j;
    }
    rb.innerHTML = html;
  }

  function drawDD(res) {
    Charts.lineChart($("#chartDD"), {
      x: res.equity.dates,
      xFmt: x => x.slice(0, 4),
      yFmt: v => (v * 100).toFixed(0) + " %",
      zeroLine: true,
      series: [
        { label: "Strategie", values: res.equity.drawdown, color: "#f87171", width: 2, fill: true },
        { label: "Buy & Hold", values: res.equity.bench_drawdown, color: "#64748b", width: 1.2, dash: "4 4" },
      ],
    });
  }

  function drawdownEpisodes(res) {
    const dd = res.equity.drawdown, dates = res.equity.dates;
    const out = [];
    let i = 0;
    while (i < dd.length) {
      if (dd[i] >= -1e-9) { i++; continue; }
      const start = i;
      let trough = i;
      while (i < dd.length && dd[i] < -1e-9) {
        if (dd[i] < dd[trough]) trough = i;
        i++;
      }
      const end = Math.min(i, dd.length - 1);
      out.push({ start, trough, end, depth: dd[trough] });
    }
    out.sort((a, b) => a.depth - b.depth);
    return out.slice(0, 5);
  }

  function renderDdList(res) {
    const eps = drawdownEpisodes(res);
    const host = $("#ddList");
    if (!eps.length) { host.innerHTML = `<div class="hint">Keine Verlustphase.</div>`; return; }
    host.innerHTML = eps.map((e, k) => {
      const w = Math.min(100, Math.abs(e.depth) * 260);
      return `<div class="dd-item">
        <div class="dd-pct">${pct(e.depth)}</div>
        <div>
          <div class="dd-bar"><i style="width:${w}%"></i></div>
          <div class="dd-date">${dateDE(res.equity.dates[e.start])} – ${dateDE(res.equity.dates[e.end])}</div>
        </div>
        <div class="dd-date">${e.end - e.start} Tage</div>
      </div>`;
    }).join("");
  }

  function renderMetricsTable(res) {
    const m = res.metrics;
    const rows = [
      ["Gesamtrendite", pct(m.total_return), pct(m.bench_total_return)],
      ["CAGR p.a.", pct(m.cagr), pct(m.bench_cagr)],
      ["Volatilität p.a.", pct(m.volatility), pct(m.bench_volatility)],
      ["Sharpe-Ratio", num(m.sharpe, 2), num(m.bench_sharpe, 2)],
      ["Sortino-Ratio", num(m.sortino, 2), "–"],
      ["Calmar-Ratio", num(m.calmar, 2), "–"],
      ["Max. Drawdown", pct(m.max_drawdown), pct(m.bench_max_drawdown)],
      ["Längste Unterwasserphase", `${m.longest_underwater_days} Tage`, "–"],
      ["Bester Tag", pct(m.best_day), "–"],
      ["Schlechtester Tag", pct(m.worst_day), "–"],
      ["Anteil positiver Tage", pct(m.positive_days, 1), "–"],
      ["Zeit im Markt", pct(m.time_in_market, 1), "100 %"],
      ["Trades", String(m.trades), "1"],
      ["Gebühren", eur2(m.fees), "–"],
      ["Steuern", eur2(m.taxes), "–"],
      ["Zeitraum", `${dateDE(m.start)} – ${dateDE(m.end)}`, `${m.years} Jahre`],
    ];
    let html = `<table><thead><tr><th>Kennzahl</th><th>Strategie</th><th>Buy &amp; Hold</th></tr></thead><tbody>`;
    rows.forEach(r => {
      html += `<tr><td>${r[0]}</td><td>${r[1]}</td><td class="dim">${r[2]}</td></tr>`;
    });
    html += "</tbody></table>";
    $("#metricsTable").innerHTML = html;
  }

  /* --------------------------------------------------------- Render: Signal */
  function segments(state) {
    const out = [];
    let i = 0;
    while (i < state.length) {
      let j = i;
      while (j < state.length && state[j] === state[i]) j++;
      out.push({ from: i, to: j - 1, on: !!state[i] });
      i = j;
    }
    return out;
  }

  function drawSignal(res) {
    const s = res.signal;
    const bands = segments(s.state)
      .filter(b => (b.to - b.from) > 2)
      .map(b => ({ from: b.from, to: b.to, color: b.on ? "rgba(52,211,153,.10)" : "rgba(248,113,113,.09)" }));
    Charts.lineChart($("#chartSignal"), {
      x: s.dates, xFmt: x => x.slice(0, 4),
      yFmt: v => nf1.format(v),
      bands,
      series: [
        { label: "Kurs", values: s.price, color: "#e8eef8", width: 1.6 },
        { label: "Durchschnitt", values: s.sma, color: "#fbbf24", width: 1.6, dash: "4 3" },
      ],
    });
    $("#legendSignal").innerHTML =
      `<span><i style="background:#e8eef8"></i>Signalgeber</span>` +
      `<span><i style="background:#fbbf24"></i>gleitender Durchschnitt</span>` +
      `<span><i style="background:rgba(52,211,153,.5)"></i>Risiko aktiv</span>` +
      `<span><i style="background:rgba(248,113,113,.5)"></i>Defensiv</span>`;
    $("#sigTitle").textContent = "Signalgeber mit gleitendem Durchschnitt – " +
      (res.meta.signal_name || res.meta.signal || "");
  }

  function drawRatio(res) {
    const s = res.signal, p = res.meta.params || {};
    const hl = [];
    if (res.meta.signal_type === "ampel") {
      hl.push({ value: p.upper || 1.05, color: "#34d399", label: "Obergrenze " + num(p.upper || 1.05, 2) });
      hl.push({ value: 1, color: "#475569", label: "1,0", dash: "2 3" });
      hl.push({ value: p.lower || 0.92, color: "#f87171", label: "Untergrenze " + num(p.lower || 0.92, 2) });
    } else {
      hl.push({ value: 1, color: "#475569", label: "1,0" });
    }
    Charts.lineChart($("#chartRatio"), {
      x: s.dates, xFmt: x => x.slice(0, 4),
      yFmt: v => num(v, 3),
      zeroLine: false, hlines: hl,
      series: [{ label: "RSL", values: s.ratio, color: "#22d3ee", width: 1.5, fill: true }],
    });
  }

  function renderSignals(res) {
    drawSignal(res);
    drawRatio(res);
    const s = res.signal;
    const last = s.state.length - 1;
    const segs = segments(s.state);
    const cur = segs[segs.length - 1] || { from: last, to: last, on: 1 };
    const daysInState = cur.to - cur.from + 1;
    const greenShare = s.state.filter(x => x).length / s.state.length;
    const ratio = s.ratio[last], price = s.price[last], sma = s.sma[last];
    $("#signalNow").innerHTML = [
      `<div class="sig-card">
        <div class="s-lbl">Aktueller Zustand</div>
        <div class="s-val ${cur.on ? "pos" : "neg"}">${cur.on ? "RISIKO AKTIV" : "DEFENSIV"}</div>
        <div class="s-sub">seit ${dateDE(s.dates[cur.from])} · ${daysInState} Handelstage</div>
      </div>`,
      `<div class="sig-card">
        <div class="s-lbl">Abstand zum Durchschnitt</div>
        <div class="s-val">${ratio === null ? "–" : num(ratio, 3)}</div>
        <div class="s-sub">Kurs ${num(price, 2)} / Ø ${num(sma, 2)}</div>
      </div>`,
      `<div class="sig-card">
        <div class="s-lbl">Anteil Risiko-Phasen</div>
        <div class="s-val">${pct(greenShare, 1)}</div>
        <div class="s-sub">${segs.length} Phasen im Zeitraum</div>
      </div>`,
      `<div class="sig-card">
        <div class="s-lbl">Letzter Wechsel</div>
        <div class="s-val">${dateDE(s.dates[cur.from])}</div>
        <div class="s-sub">${cur.on ? "auf grün" : "auf rot"}</div>
      </div>`,
    ].join("");
  }

  /* -------------------------------------------------------- Render: Perioden */
  function drawYears(res) {
    const y = res.yearly;
    Charts.barChart($("#chartYears"), {
      categories: y.map(r => String(r.year)),
      yFmt: v => (v * 100).toFixed(0) + " %",
      series: [
        { label: "Strategie", values: y.map(r => r.strategy), color: "#22d3ee" },
        { label: "Buy & Hold", values: y.map(r => r.benchmark), color: "#a78bfa" },
      ],
    });
    const pos = y.filter(r => r.strategy > 0).length;
    $("#legendYear").innerHTML =
      `<span><i style="background:#22d3ee"></i>Strategie (${pos}/${y.length} Jahre positiv)</span>` +
      `<span><i style="background:#a78bfa"></i>Buy &amp; Hold</span>`;
  }

  function renderPeriods(res) {
    drawYears(res);
    Charts.heatmap($("#heatMonths"), res.monthly);
    let html = `<table><thead><tr><th>Jahr</th><th>Strategie</th><th>Buy &amp; Hold</th>
      <th>Differenz</th><th>Endstand</th></tr></thead><tbody>`;
    res.yearly.slice().reverse().forEach(r => {
      html += `<tr><td>${r.year}</td>
        <td class="${signCls(r.strategy)}">${pct(r.strategy)}</td>
        <td class="dim">${pct(r.benchmark)}</td>
        <td class="${signCls(r.strategy - r.benchmark)}">${pct(r.strategy - r.benchmark)}</td>
        <td>${eur(r.end)}</td></tr>`;
    });
    html += "</tbody></table>";
    $("#yearTable").innerHTML = html;
  }

  /* ----------------------------------------------------------- Render: Trades */
  function renderTrades(res) {
    const all = res.trades || [];
    const filter = getVal("tradeFilter") || "all";
    const rows = all.filter(t => filter === "all" || t.action === filter);
    const buys = all.filter(t => t.action === "KAUF");
    const sells = all.filter(t => t.action === "VERKAUF");
    const wins = sells.filter(t => t.gain > 0).length;
    const avgHold = (() => {
      let sum = 0, n = 0;
      for (let i = 0; i < all.length - 1; i++) {
        if (all[i].action === "KAUF" && all[i + 1].action === "VERKAUF") {
          sum += (new Date(all[i + 1].date) - new Date(all[i].date)) / 86400000;
          n++;
        }
      }
      return n ? sum / n : 0;
    })();
    $("#tradeKpi").innerHTML = [
      kpi("Umschichtungen", String(all.length), `${buys.length} Käufe · ${sells.length} Verkäufe`, "neutral"),
      kpi("Gewinnquote", sells.length ? pct(wins / sells.length, 1) : "–", `${wins} von ${sells.length} Verkäufen`, "neutral"),
      kpi("Ø Haltedauer", avgHold ? Math.round(avgHold) + " Tage" : "–", "Kauf bis Verkauf", "neutral"),
      kpi("Gebühren", eur2(all.reduce((a, t) => a + t.fee, 0)), "Summe aller Orders", "neutral"),
      kpi("Steuern", eur2(all.reduce((a, t) => a + t.tax, 0)), "realisiert", "neutral"),
      kpi("Bester Trade", eur2(Math.max.apply(null, sells.map(t => t.gain).concat([0]))), "realisierter Gewinn", "neutral"),
    ].join("");
    let html = `<table><thead><tr><th>Datum</th><th>Aktion</th><th>Instrument</th>
      <th>Kurs</th><th>Stück</th><th>Wert</th><th>Gebühr</th><th>Steuer</th><th>Gewinn</th></tr></thead><tbody>`;
    rows.forEach(t => {
      html += `<tr><td>${dateDE(t.date)}</td>
        <td><span class="tag ${t.action === "KAUF" ? "buy" : "sell"}">${t.action}</span></td>
        <td style="text-align:left">${t.symbol}</td>
        <td>${num(t.price, 4)}</td><td>${num(t.units, 3)}</td>
        <td>${eur2(t.value)}</td><td>${eur2(t.fee)}</td><td>${eur2(t.tax)}</td>
        <td class="${signCls(t.gain)}">${t.gain ? eur2(t.gain) : "–"}</td></tr>`;
    });
    html += "</tbody></table>";
    $("#tradeTable").innerHTML = rows.length ? html : `<div class="hint" style="padding:10px">Keine Trades im Zeitraum.</div>`;
  }

  /* --------------------------------------------------------- Render: Vergleich */
  async function runCompare() {
    const sel = $$("#compareSel option").filter(o => o.selected).map(o => o.value);
    if (!sel.length) { toast("Bitte Instrumente auswählen", "err"); return; }
    busy(true, "vergleiche …");
    try {
      const j = await apiPost("/api/compare", { config: readConfig(), symbols: sel });
      S.compare = j.rows;
      drawCompare(S.compare);
      let html = `<table><thead><tr><th>Instrument</th><th>CAGR</th><th>Benchmark</th>
        <th>Max. DD</th><th>Sharpe</th><th>Calmar</th><th>Volatilität</th>
        <th>Zeit im Markt</th><th>Endkapital</th></tr></thead><tbody>`;
      const good = j.rows.filter(r => r.ok).sort((a, b) => (b.cagr || -9) - (a.cagr || -9));
      good.forEach(r => {
        html += `<tr><td><b>${r.symbol}</b><div class="dim" style="font-size:11px">${r.name || ""}</div></td>
          <td class="${signCls(r.cagr)}">${pct(r.cagr)}</td>
          <td class="dim">${pct(r.bench_cagr)}</td>
          <td class="neg">${pct(r.max_drawdown)}</td>
          <td>${num(r.sharpe, 2)}</td><td>${num(r.calmar, 2)}</td>
          <td>${pct(r.volatility)}</td><td>${pct(r.time_in_market, 1)}</td>
          <td>${eur(r.end_capital)}</td></tr>`;
      });
      j.rows.filter(r => !r.ok).forEach(r => {
        html += `<tr><td>${r.symbol}</td><td colspan="8" class="neg">${r.error}</td></tr>`;
      });
      html += "</tbody></table>";
      $("#compareOut").innerHTML = html;
      toast("Vergleich fertig", "ok");
    } catch (e) {
      toast("Vergleich fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  function drawCompare(rows) {
    $("#chartCompare").dataset.filled = "1";
    const good = rows.filter(r => r.ok).sort((a, b) => (b.cagr || -9) - (a.cagr || -9)).slice(0, 12);
    Charts.barChart($("#chartCompare"), {
      categories: good.map(r => r.symbol),
      yFmt: v => (v * 100).toFixed(1) + " %",
      series: [
        { label: "Strategie CAGR", values: good.map(r => r.cagr), color: "#22d3ee" },
        { label: "Buy & Hold CAGR", values: good.map(r => r.bench_cagr), color: "#a78bfa" },
      ],
    });
  }

  /* ------------------------------------------------------- Render: Parameter */
  async function runGrid() {
    busy(true, "teste Parameterkombinationen …");
    try {
      const j = await apiPost("/api/grid", { config: readConfig() });
      S.grid = j.rows;
      renderGrid();
      toast(`${j.rows.length} Varianten getestet`, "ok");
    } catch (e) {
      toast("Parameter-Suche fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  const PARAM_LABELS = {
    sma_days: "RSL-Tage", upper: "Obergrenze", lower: "Untergrenze",
    days_red: "Wechseltage rot", days_green: "Wechseltage grün",
    band: "Hysterese", sma_short: "kurzer SMA", sma_long: "langer SMA",
    momentum_days: "Momentum-Tage",
  };
  const PARAM_ORDER = {
    ampel: ["sma_days", "upper", "lower", "days_red", "days_green"],
    sma: ["sma_days", "band"],
    dual_sma: ["sma_short", "sma_long"],
    momentum: ["momentum_days"],
    none: [],
  };

  function paramText(p, type) {
    const keys = PARAM_ORDER[type] || Object.keys(p);
    return keys.filter(k => p[k] !== undefined && p[k] !== null)
      .map(k => `${PARAM_LABELS[k] || k} ${typeof p[k] === "number" ? num(p[k], 2) : p[k]}`)
      .join(" · ");
  }

  function renderGrid() {
    const rows = S.grid || [];
    if (!rows.length) { $("#gridSummary").textContent = "Keine Ergebnisse."; return; }
    const best = rows[0];
    const stype = (S.boot && getVal("signalType")) || "ampel";
    $("#gridSummary").innerHTML = `Beste Variante: <b>${paramText(best.params, stype)}</b> – ` +
      `CAGR ${pct(best.cagr)} · max. Verlust ${pct(best.max_drawdown)} · Calmar ${num(best.calmar, 2)} ` +
      `(${rows.length} Kombinationen getestet)`;
    drawGrid(rows);
    let html = `<table><thead><tr><th>#</th><th>Parameter</th><th>CAGR</th><th>Max. DD</th>
      <th>Sharpe</th><th>Calmar</th><th>Vol.</th><th>Zeit im Markt</th><th></th></tr></thead><tbody>`;
    rows.slice(0, 120).forEach((r, i) => {
      const p = paramText(r.params, stype);
      html += `<tr><td>${i + 1}</td><td class="txt" style="max-width:520px">${p}</td>
        <td class="${signCls(r.cagr)}">${pct(r.cagr)}</td>
        <td class="neg">${pct(r.max_drawdown)}</td>
        <td>${num(r.sharpe, 2)}</td><td>${num(r.calmar, 2)}</td>
        <td>${pct(r.volatility)}</td><td>${pct(r.time_in_market, 1)}</td>
        <td><button class="btn mini" data-apply='${JSON.stringify(r.params)}'>übernehmen</button></td></tr>`;
    });
    html += "</tbody></table>";
    $("#gridTable").innerHTML = html;
    $$("[data-apply]").forEach(b => b.addEventListener("click", () => {
      const p = JSON.parse(b.dataset.apply);
      const cfg = readConfig();
      const merged = Object.assign({}, cfg.params, p);
      applyConfig(Object.assign({}, cfg, { params: merged }));
      toast("Parameter übernommen – Analyse starten", "ok");
    }));
  }

  function drawGrid(rows) {
    $("#chartGrid").dataset.filled = "1";
    // Matrix: SMA-Tage (Zeilen) x Obergrenze (Spalten) mit bestem Calmar
    const xs = [], ys = [], map = {};
    rows.forEach(r => {
      const x = r.params.upper, y = r.params.sma_days;
      if (x === undefined || y === undefined) return;
      if (xs.indexOf(x) < 0) xs.push(x);
      if (ys.indexOf(y) < 0) ys.push(y);
      const k = y + "|" + x;
      if (!map[k] || (r.calmar || -9) > (map[k].calmar || -9)) map[k] = r;
    });
    xs.sort((a, b) => a - b); ys.sort((a, b) => a - b);
    if (!xs.length || !ys.length) { $("#chartGrid").innerHTML = `<div class="hint">Keine Matrix darstellbar.</div>`; return; }
    const values = ys.map(y => xs.map(x => (map[y + "|" + x] || {}).calmar));
    Charts.heatmap($("#chartGrid"), {
      years: ys, months: xs.map(x => num(x, 2)), values,
      rowLabel: "RSL-Tage", fmt: "num",
    });
    $("#chartGrid").classList.remove("chart");
    $("#chartGrid").style.height = "auto";
  }

  /* ------------------------------------------------------ Render: Robustheit */
  async function runWalkforward() {
    busy(true, "Walk-Forward läuft …");
    try {
      const j = await apiPost("/api/walkforward", {
        config: readConfig(), objective: getVal("wfObj") || "calmar",
        train_years: 6, test_years: 2,
      });
      S.wf = j.walkforward;
      renderRobust();
      toast(S.wf.oos ? "Walk-Forward fertig" : (S.wf.note || "Kein Ergebnis"), S.wf.oos ? "ok" : "err");
    } catch (e) {
      toast("Walk-Forward fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  async function runMonteCarlo() {
    if (!S.result) { toast("Bitte zuerst eine Analyse starten", "err"); return; }
    busy(true, "Monte-Carlo …");
    try {
      const cfg = readConfig();
      const j = await apiPost("/api/backtest", { config: cfg, montecarlo: true });
      S.mc = j.result.montecarlo;
      S.mcOrigin = j.result.equity.strategy.slice(-1)[0];
      renderRobust();
      toast("Projektion fertig", "ok");
    } catch (e) {
      toast("Monte-Carlo fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  async function runSensitivity() {
    busy(true, "Kosten-Sensitivität …");
    try {
      const base = readConfig();
      const variants = [
        { label: "Basisfall", mod: {} },
        { label: "ohne Gebühren", mod: { fee_fixed: 0, fee_pct: 0 } },
        { label: "ohne Slippage", mod: { slippage_bps: 0 } },
        { label: "ohne Steuer", mod: { tax_rate: 0 } },
        { label: "Gebühr 15 €", mod: { fee_fixed: 15 } },
        { label: "Gebühr 30 €", mod: { fee_fixed: 30 } },
        { label: "Slippage 25 bp", mod: { slippage_bps: 25 } },
        { label: "Slippage 50 bp", mod: { slippage_bps: 50 } },
        { label: "Steuer 30 %", mod: { tax_rate: 30 } },
      ];
      const out = [];
      for (const v of variants) {
        const cfg = Object.assign({}, base, v.mod);
        const j = await apiPost("/api/backtest", { config: cfg });
        out.push({ label: v.label, m: j.result.metrics });
      }
      let html = `<table><thead><tr><th>Szenario</th><th>CAGR</th><th>Endkapital</th>
        <th>Max. DD</th><th>Gebühren</th><th>Steuern</th></tr></thead><tbody>`;
      const baseCagr = out[0].m.cagr;
      out.forEach(r => {
        const d = r.m.cagr - baseCagr;
        html += `<tr><td>${r.label}</td>
          <td class="${signCls(r.m.cagr)}">${pct(r.m.cagr)}${r.label !== "Basisfall" ? ` <span class="dim">(${pct(d)})</span>` : ""}</td>
          <td>${eur(r.m.end_capital)}</td>
          <td class="neg">${pct(r.m.max_drawdown)}</td>
          <td class="dim">${eur2(r.m.fees)}</td>
          <td class="dim">${eur2(r.m.taxes)}</td></tr>`;
      });
      html += "</tbody></table>";
      $("#sensOut").innerHTML = html;
    } catch (e) {
      toast("Sensitivität fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  function renderRobust() {
    // Walk-Forward
    if (S.wf) {
      if (!S.wf.oos) {
        $("#wfOut").innerHTML = `<div class="hint">${S.wf.note || "Kein Walk-Forward-Ergebnis."}</div>`;
        $("#chartWf").innerHTML = ""; $("#wfTable").innerHTML = "";
      } else {
        const oos = S.wf.oos;
        const first = oos.strategy[0], last = oos.strategy[oos.strategy.length - 1];
        const yrs = (new Date(oos.dates[oos.dates.length - 1]) - new Date(oos.dates[0])) / 31557600000;
        const cagr = Math.pow(last / first, 1 / Math.max(yrs, .1)) - 1;
        const posWin = S.wf.windows.filter(w => (w.test_return || 0) > 0).length;
        $("#wfOut").innerHTML = `<div class="kpi-row small">
          ${kpi("Out-of-Sample CAGR", pct(cagr), `${S.wf.windows.length} Fenster`, "good", signCls(cagr))}
          ${kpi("Positive Testfenster", `${posWin}/${S.wf.windows.length}`, "nach Optimierung", "neutral")}
          ${kpi("OOS-Endkapital", eur(last), `Start ${eur(first)}`, "neutral", "acc")}
        </div>`;
        drawWf(S.wf);
        let html = `<table><thead><tr><th>Training</th><th>Test</th><th>gewählte Parameter</th>
          <th>Testrendite</th></tr></thead><tbody>`;
        S.wf.windows.forEach(w => {
          html += `<tr><td class="dim">${w.train[0].slice(0, 7)} – ${w.train[1].slice(0, 7)}</td>
            <td class="dim">${w.test[0].slice(0, 7)} – ${w.test[1].slice(0, 7)}</td>
            <td style="text-align:left">${Object.keys(w.params).map(k => k + "=" + w.params[k]).join(" · ")}</td>
            <td class="${signCls(w.test_return)}">${pct(w.test_return)}</td></tr>`;
        });
        html += "</tbody></table>";
        $("#wfTable").innerHTML = html;
      }
    }
    // Monte Carlo
    if (S.mc) {
      drawMc(S.mc, S.mcOrigin);
      const f = S.mc.final || {};
      $("#mcOut").innerHTML = `Nach ${Math.round(S.mc.horizon_days / 252)} Jahren: ` +
        `5 %-Quantil <b>${eur(f.p5)}</b> · Median <b>${eur(f.p50)}</b> · 95 %-Quantil <b>${eur(f.p95)}</b> ` +
        `(Start ${eur(S.mcOrigin)}, ${S.mc.n_paths} Pfade).`;
    }
  }

  function drawWf(wf) {
    $("#chartWf").dataset.filled = "1";
    const o = wf.oos;
    Charts.lineChart($("#chartWf"), {
      x: o.dates, xFmt: x => x.slice(0, 4), yFmt: v => nf0.format(v) + " €",
      series: [{ label: "Out-of-Sample", values: o.strategy, color: "#34d399", width: 2, fill: true }],
    });
  }

  function drawMc(mc, origin) {
    $("#chartMc").dataset.filled = "1";
    const n = mc.p50.length;
    const labels = Array.from({ length: n }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() + i * mc.step_days);
      return d.toISOString().slice(0, 10);
    });
    Charts.lineChart($("#chartMc"), {
      x: labels, xFmt: x => x.slice(0, 7), yFmt: v => nf0.format(v) + " €",
      series: [
        { label: "95 %-Quantil", values: mc.p95, color: "#34d399", width: 1.1, dash: "4 4" },
        { label: "Median", values: mc.p50, color: "#22d3ee", width: 2.2 },
        { label: "5 %-Quantil", values: mc.p5, color: "#f87171", width: 1.1, dash: "4 4" },
        { label: "Start", values: labels.map(() => origin), color: "#64748b", width: 1, dash: "2 3" },
      ],
    });
  }

  /* ------------------------------------------------------------- Export/Ende */
  async function exportCsv() {
    if (!S.result) { toast("Bitte zuerst eine Analyse starten", "err"); return; }
    busy(true, "erstelle CSV …");
    try {
      const j = await apiPost("/api/export", { config: readConfig() });
      const bin = atob(j.content);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const blob = new Blob([bytes], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = j.filename;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
      toast("CSV gespeichert: " + j.filename, "ok");
    } catch (e) {
      toast("Export fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  async function shutdown() {
    if (!confirm("Server beenden? Das Tool kann danach über die Startdatei neu gestartet werden.")) return;
    try { await apiGet("/api/shutdown"); } catch (e) { /* egal */ }
    setStatus("warn", "beendet");
    document.body.innerHTML = `<div style="display:flex;height:100vh;align-items:center;
      justify-content:center;flex-direction:column;gap:10px;color:#8ba0bd;font-family:Segoe UI,sans-serif">
      <div style="font-size:22px;color:#e8eef8">Server beendet</div>
      <div>Fenster schließen oder Startdatei erneut ausführen.</div></div>`;
  }

  /* ------------------------------------------------------------------ Suche */
  function openSearch(target) {
    S.searchTarget = target;
    $("#searchModal").classList.add("open");
    $("#searchInput").value = "";
    $("#searchResults").innerHTML = "";
    setTimeout(() => $("#searchInput").focus(), 40);
  }
  function closeSearch() { $("#searchModal").classList.remove("open"); }

  async function doSearch() {
    const q = $("#searchInput").value.trim();
    if (q.length < 2) return;
    $("#searchResults").innerHTML = `<div class="hint">suche …</div>`;
    try {
      const j = await apiGet("/api/search?q=" + encodeURIComponent(q));
      if (!j.results.length) { $("#searchResults").innerHTML = `<div class="hint">nichts gefunden</div>`; return; }
      $("#searchResults").innerHTML = j.results.map(r => `
        <div class="sr-item" data-sym="${r.symbol}">
          <div style="min-width:0">
            <div class="sr-sym">${r.symbol}</div>
            <div class="sr-name">${r.name}</div>
          </div>
          <div class="sr-meta">${r.exchange || ""} · ${r.type || ""}</div>
        </div>`).join("");
      $$(".sr-item").forEach(el => el.addEventListener("click", async () => {
        const sym = el.dataset.sym;
        if (S.searchTarget) setVal(S.searchTarget, sym);
        closeSearch();
        await downloadSymbols([sym]);
      }));
    } catch (e) {
      $("#searchResults").innerHTML = `<div class="hint neg">Fehler: ${e.message}</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
