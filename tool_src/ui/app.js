/* ETF-Timing-Studio -- Anwendungslogik */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));
  const DE = "de-DE";

  const S = {
    boot: null, result: null, grid: null, wf: null, mc: null,
    compare: null, tab: "overview", searchTarget: null, busy: false,
    rotation: null, hidden: {}, cur: "€",
  };

  const nf0 = new Intl.NumberFormat(DE, { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat(DE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat(DE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const CUR_SYM = { EUR: "€", USD: "$", GBP: "£", GBp: "GBp", CHF: "CHF", JPY: "¥" };

  const ok = v => v !== null && v !== undefined && isFinite(v);
  const fmtN = (v, d) => (d === 0 ? nf0 : d === 1 ? nf1 : nf2).format(v);
  const eur = v => ok(v) ? nf0.format(v) + " " + S.cur : "–";
  const eur2 = v => ok(v) ? nf2.format(v) + " " + S.cur : "–";
  const pct = (v, d) => ok(v) ? fmtN(v * 100, d === undefined ? 2 : d) + " %" : "–";
  const num = (v, d) => ok(v) ? fmtN(v, d === undefined ? 2 : d) : "–";
  const days = v => ok(v) ? nf0.format(v) + " Tage" : "–";
  const dateDE = iso => {
    if (!iso) return "–";
    const p = iso.slice(0, 10).split("-");
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : iso;
  };
  const signCls = v => ok(v) ? (v >= 0 ? "pos" : "neg") : "";
  const esc = s => String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

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
  const dflt = (v, d) => (v === undefined || v === null || v === "") ? d : v;

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
    // Vorlagenwerte ohne eigenes Eingabefeld (z. B. Zinsbesteuerung, Hebel-Finanzierung) bleiben erhalten
    return Object.assign({}, S.hidden, {
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
      tax_mode: getVal("taxMode") || "accrued",
      tax_rule: getVal("taxRule") || "auto",
      tax_rate: getNum("taxRate", 26.375),
      tax_allowance: getNum("taxAllowance", 1000),
      tax_rate_private: getNum("taxPrivate", 35),
      tax_exemption_private: getNum("taxExemption", 1000),
      cash_rate: getNum("cashRate", 2),
      exec_lag: parseInt(getVal("execLag"), 10) || 0,
      start: getVal("startDate") || null,
      end: getVal("endDate") || null,
      rotation: S.rotation || null,
    });
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
    setVal("feeFixed", dflt(c.fee_fixed, 5.9));
    setVal("feePct", dflt(c.fee_pct, 0));
    setVal("slippage", dflt(c.slippage_bps, 5));
    setVal("taxMode", c.tax_mode || "accrued");
    setVal("taxRule", c.tax_rule || "auto");
    setVal("taxRate", dflt(c.tax_rate, 26.375));
    setVal("taxAllowance", dflt(c.tax_allowance, 1000));
    setVal("taxPrivate", dflt(c.tax_rate_private, 35));
    setVal("taxExemption", dflt(c.tax_exemption_private, 1000));
    setVal("cashRate", dflt(c.cash_rate, 2));
    setVal("execLag", String(c.exec_lag === 0 ? 0 : 1));
    setVal("startDate", (c.start || "").slice(0, 10));
    setVal("endDate", (c.end || "").slice(0, 10));
    S.rotation = c.rotation || null;
    S.hidden = {};
    ["tax_interest", "loss_offset", "synth_rf", "synth_cost"].forEach(k => {
      if (c[k] !== undefined) S.hidden[k] = c[k];
    });
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

      const st = $("#signalType");
      st.innerHTML = "";
      b.signal_types.forEach(t => {
        if (t.id === "none") return;
        const o = document.createElement("option");
        o.value = t.id; o.textContent = t.name;
        st.appendChild(o);
      });
      const tr = $("#taxRule");
      tr.innerHTML = "";
      (b.tax_rules || []).forEach(t => {
        const o = document.createElement("option");
        o.value = t.id; o.textContent = t.name;
        tr.appendChild(o);
      });

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
      fillSuggestions();
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
      Lade zuerst die Standard-Instrumente (MSCI World, S&amp;P 500, DAX-ETF, Bitcoin) – das dauert nur
      wenige Sekunden.</p>
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
    $("#rollH").addEventListener("change", () => { if (S.result) drawRolling(S.result); });
    $("#gridObj").addEventListener("change", () => { if (S.grid) renderGrid(); });
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
    if (!name || !document.getElementById("view-" + name)) name = "overview";
    S.tab = name;
    if (history.replaceState) history.replaceState(null, "", "#" + name);
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
    if (tab === "periods") { drawYears(S.result); drawRolling(S.result); }
    if (tab === "compare" && S.compare) drawCompare(S.compare);
    if (tab === "params" && S.grid) drawGrid(S.grid);
    if (tab === "robust") { if (S.wf && S.wf.oos) drawWf(S.wf); if (S.mc) drawMc(S.mc); }
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
  function fillSuggestions() {
    const host = $("#suggestBox");
    if (!host || !S.boot) return;
    const sug = S.boot.suggestions || [];
    host.innerHTML = `<span class="muted" style="align-self:center">Schnellwahl:</span>` + sug.map(s =>
      `<span class="chip" data-sug="${esc(s.symbol)}" title="${esc(s.name)}"><b>${esc(s.symbol)}</b> ${esc(s.group)}</span>`
    ).join("");
    $$("[data-sug]", host).forEach(c => c.addEventListener("click", () => downloadSymbols([c.dataset.sug])));
  }

  const Q_LABEL = { ok: "✓ ok", info: "ℹ Hinweis", warn: "⚠ prüfen" };

  async function refreshDataView() {
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
        <th>Symbol</th><th>Name</th><th>Quelle</th><th>Währung</th><th>von</th><th>bis</th>
        <th>Kurse</th><th>Qualität</th><th>aktualisiert</th><th></th></tr></thead><tbody>`;
      rows.forEach(r => {
        const q = r.quality;
        const ql = q ? `<span class="q-${q.level}">${Q_LABEL[q.level] || q.level}</span>` : `<span class="dim">–</span>`;
        html += `<tr>
          <td><b>${esc(r.symbol)}</b></td>
          <td class="dim txt">${esc(r.name)}</td>
          <td class="dim">${esc(r.source)}</td>
          <td class="dim">${esc(r.currency)}</td>
          <td class="dim">${dateDE(r.first)}</td>
          <td class="dim">${dateDE(r.last)}</td>
          <td>${r.rows}</td>
          <td>${ql}</td>
          <td class="dim">${(r.updated || "").replace("T", " ").slice(0, 16)}</td>
          <td><button class="btn mini" data-q="${esc(r.symbol)}">prüfen</button>
              <button class="btn mini" data-del="${esc(r.symbol)}">löschen</button></td>
        </tr>`;
      });
      html += "</tbody></table>";
      wrap.innerHTML = html;
      $$("[data-q]", wrap).forEach(b2 => b2.addEventListener("click", () => checkQuality(b2.dataset.q, b2)));
      $$("[data-del]", wrap).forEach(b2 => b2.addEventListener("click", async () => {
        if (!confirm(`Daten für ${b2.dataset.del} löschen?`)) return;
        try {
          await apiPost("/api/delete", { symbol: b2.dataset.del });
          toast("Gelöscht: " + b2.dataset.del, "ok");
          refreshDataView();
        } catch (e) { toast("Löschen fehlgeschlagen: " + e.message, "err"); }
      }));
    } catch (e) { /* still */ }
  }

  function issuesHtml(issues) {
    if (!issues || !issues.length) return `<span class="q-ok">Keine Auffälligkeiten.</span>`;
    return "<ul>" + issues.map(it => `<li class="q-${it.level}">${esc(it.text)}</li>`).join("") + "</ul>";
  }

  async function checkQuality(sym, btn) {
    const tr = btn.closest("tr");
    const next = tr.nextElementSibling;
    if (next && next.classList.contains("qdetail")) { next.remove(); return; }
    try {
      const rep = (await apiGet("/api/quality?symbol=" + encodeURIComponent(sym))).report;
      const row = document.createElement("tr");
      row.className = "qdetail";
      row.innerHTML = `<td colspan="10"><b>${esc(rep.symbol)}</b> · ${esc(rep.calendar || "")} ·
        ${num(rep.periods_per_year, 0)} Kurse je Jahr · Sprung-Schwelle ${pct(rep.jump_threshold, 0)}
        ${issuesHtml(rep.issues)}</td>`;
      tr.parentNode.insertBefore(row, tr.nextSibling);
      const lvl = rep.issues.some(i => i.level === "warn") ? "warn"
        : rep.issues.some(i => i.level === "info" && i.type !== "Aktualität") ? "info" : "ok";
      if (tr.children[7]) tr.children[7].innerHTML = `<span class="q-${lvl}">${Q_LABEL[lvl]}</span>`;
    } catch (e) { toast("Prüfung fehlgeschlagen: " + e.message, "err"); }
  }

  async function downloadSymbols(symbols) {
    const list = symbols || getVal("dlInput").split(/[,\s;]+/).filter(Boolean);
    if (!list.length) { toast("Bitte Symbol(e) eingeben", "err"); return; }
    busy(true, "lade Kurse …");
    try {
      const j = await apiPost("/api/download", { symbols: list, force: false });
      const good = j.results.filter(r => r.ok), bad = j.results.filter(r => !r.ok);
      $("#dlOut").innerHTML = good.map(r => `✓ <b>${esc(r.symbol)}</b> — ${esc(r.name)} · ${r.rows} Kurse (${dateDE(r.first)} … ${dateDE(r.last)})`).join("<br>") +
        (bad.length ? "<br>" + bad.map(r => `<span class="neg">✗ ${esc(r.symbol)}: ${esc(r.error)}</span>`).join("<br>") : "");
      S.boot.cached = j.cached;
      fillDatalist(); fillCompareSelect(); refreshDataView();
      toast(`${good.length} Instrument(e) geladen`, good.length ? "ok" : "err");
    } catch (e) {
      toast("Download fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); setStatus("ok", "verbunden"); }
  }

  async function seedDefaults() {
    await downloadSymbols(["IWDA.AS", "^GSPC", "EXS1.DE", "^GDAXI", "BTC-EUR"]);
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
      const q = j.quality || {};
      $("#impOut").innerHTML = `✓ <b>${esc(j.symbol)}</b> importiert: ${j.rows} Kurse (${dateDE(j.first)} … ${dateDE(j.last)}) ·
        Datumsformat ${esc(j.date_order)} · Dezimalzeichen „${esc(j.decimal)}“ ·
        Qualität <span class="q-${q.level || "ok"}">${Q_LABEL[q.level] || "–"}</span>${q.level && q.level !== "ok" ? issuesHtml(q.issues) : ""}`;
      S.boot.cached = j.cached;
      fillDatalist(); fillCompareSelect(); refreshDataView();
      setVal("riskSym", j.symbol);
      toast("CSV importiert", "ok");
    } catch (e) {
      $("#impOut").innerHTML = `<span class="neg">✗ ${esc(e.message)}</span>`;
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
      const meta = S.result.meta;
      S.cur = CUR_SYM[meta.risk_currency] || meta.risk_currency || "€";
      renderOverview(S.result);
      renderSignals(S.result);
      renderPeriods(S.result);
      renderTrades(S.result);
      $("#metaLine").textContent = `${dateDE(meta.start)} – ${dateDE(meta.end)} · ${meta.days} Kurse ` +
        `(${num(meta.periods_per_year, 0)} je Jahr) · ${meta.risk_name || cfg.risk} · Werte in ${meta.risk_currency || "EUR"}`;
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
  function kpi(label, value, sub, cls, valCls, title) {
    return `<div class="kpi ${cls || ""}"${title ? ` title="${esc(title)}"` : ""}>
      <div class="k-lbl">${label}</div>
      <div class="k-val ${valCls || ""}">${value}</div>
      ${sub ? `<div class="k-sub">${sub}</div>` : ""}
    </div>`;
  }
  const better = (a, b) => ok(a) && ok(b) && a >= b;

  function renderNotices(res) {
    const meta = res.meta || {}, out = [];
    const w = meta.warmup || {};
    if (w.clamped) {
      out.push(`<div class="notice info">Start auf <b>${dateDE(meta.start)}</b> gelegt: Das Signal ist erst ab
        ${dateDE(w.signal_ready)} gültig (Vorlauf des Durchschnitts bzw. Beginn der Signaldaten) – vorher
        wäre die Strategie ungetestet.</div>`);
    }
    if (meta.risk_currency && meta.risk_currency !== "EUR") {
      out.push(`<div class="notice info">Werte in <b>${esc(meta.risk_currency)}</b> (Währung der Risiko-Anlage):
        keine Umrechnung in Euro, Wechselkurseffekte sind nicht enthalten.</div>`);
    }
    (res.quality || []).forEach(q => {
      const warn = q.issues.filter(i => i.level === "warn");
      const info = q.issues.filter(i => i.level === "info" && i.type === "Kursindex");
      if (warn.length) {
        out.push(`<div class="notice warn"><b>Datenqualität ${esc(q.symbol)} (${esc(q.role)}) im Zeitraum:</b>
          ${issuesHtml(warn.slice(0, 6))}</div>`);
      }
      if (info.length) out.push(`<div class="notice info"><b>${esc(q.symbol)}:</b> ${esc(info[0].text)}</div>`);
    });
    $("#noticeBox").innerHTML = out.join("");
  }

  function renderOverview(res) {
    const m = res.metrics;
    renderNotices(res);
    const rows = [
      kpi("Endkapital", eur(m.end_capital), `Start ${eur(m.start_capital)} · B&H ${eur(m.bench_end_capital)}`,
        m.end_capital >= m.bench_end_capital ? "good" : "bad", "acc"),
      kpi("CAGR p.a.", pct(m.cagr), `B&H ${pct(m.bench_cagr)} · Δ ${pct(m.excess_cagr)}`,
        m.cagr >= m.bench_cagr ? "good" : "bad", signCls(m.cagr)),
      kpi("Max. Drawdown", pct(m.max_drawdown), `B&H ${pct(m.bench_max_drawdown)}`,
        m.max_drawdown >= m.bench_max_drawdown ? "good" : "bad", "neg"),
      kpi("Calmar", num(m.calmar), `B&H ${num(m.bench_calmar)} · CAGR / max. Verlust`,
        better(m.calmar, m.bench_calmar) ? "good" : "bad"),
      kpi("Sharpe", num(m.sharpe), `B&H ${num(m.bench_sharpe)} · rf ${pct(m.risk_free, 1)}`,
        better(m.sharpe, m.bench_sharpe) ? "good" : "bad", "",
        "Überrendite über den risikolosen Zins (= Kasse-Zins) je Schwankung"),
      kpi("Ulcer-Index", pct(m.ulcer_index, 1), `B&H ${pct(m.bench_ulcer_index, 1)} · Tiefe × Dauer`,
        better(m.bench_ulcer_index, m.ulcer_index) ? "good" : "bad", "",
        "Wurzel aus dem mittleren quadrierten Drawdown – kleiner ist besser"),
      kpi("Up / Down-Capture", `${pct(m.up_capture, 0)} / ${pct(m.down_capture, 0)}`,
        "Anteil an B&H-Gewinn- / Verlustmonaten",
        ok(m.up_capture) && ok(m.down_capture) && m.up_capture > m.down_capture ? "good" : "neutral", "",
        "Ideal: viel vom Anstieg (Up hoch), wenig vom Rückgang (Down niedrig)"),
      kpi("Volatilität", pct(m.volatility), `B&H ${pct(m.bench_volatility)}`, "neutral"),
      kpi("Zeit im Risiko", pct(m.time_in_market, 1),
        `${m.trades} Orders · ${num(m.switches_per_year, 1)} Wechsel/Jahr`, "neutral"),
      kpi("Kosten & Steuer", eur(m.fees + m.taxes),
        `${eur(m.fees)} Gebühren · ${eur(m.taxes)} Steuer · latent ${eur(m.tax_latent)}`, "neutral"),
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
      yFmt: v => nf0.format(v) + " " + S.cur,
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

  function renderDdList(res) {
    const eps = res.drawdowns || [];
    const host = $("#ddList");
    if (!eps.length) { host.innerHTML = `<div class="hint">Keine Verlustphase.</div>`; return; }
    const deepest = Math.max.apply(null, eps.map(e => Math.abs(e.depth))) || 1;
    host.innerHTML = eps.map(e => {
      const w = Math.min(100, (Math.abs(e.depth) / deepest) * 100);
      const rec = e.recovery ? `erholt ${dateDE(e.recovery)}` : "noch nicht erholt";
      return `<div class="dd-item" title="Hoch ${dateDE(e.peak)} · Tief ${dateDE(e.trough)} · ${rec}">
        <div class="dd-pct">${pct(e.depth, 1)}</div>
        <div>
          <div class="dd-bar"><i style="width:${w}%"></i></div>
          <div class="dd-date">${dateDE(e.peak)} → Tief ${dateDE(e.trough)} · ${rec}</div>
        </div>
        <div class="dd-date">${days(e.days)}</div>
      </div>`;
    }).join("");
  }

  function renderMetricsTable(res) {
    const m = res.metrics, tax = (res.meta || {}).tax || {};
    const recov = (r, d) => r ? `${days(d)} (bis ${dateDE(r)})` : "nicht erholt";
    const G = [
      ["Rendite", [
        ["Endkapital", eur(m.end_capital), eur(m.bench_end_capital), "Endwert nach Kosten; im Modus „anteilig“ nach Steuer auf den Buchgewinn."],
        ["Gesamtrendite", pct(m.total_return), pct(m.bench_total_return), ""],
        ["CAGR p. a.", pct(m.cagr), pct(m.bench_cagr), "Durchschnittliche jährliche Rendite (geometrisch)."],
        ["Anteil positiver Monate", pct(m.positive_months, 1), pct(m.bench_positive_months, 1), ""],
        ["Bester / schlechtester Monat", `${pct(m.best_month, 1)} / ${pct(m.worst_month, 1)}`,
          `${pct(m.bench_best_month, 1)} / ${pct(m.bench_worst_month, 1)}`, ""],
      ]],
      ["Risiko", [
        ["Volatilität p. a.", pct(m.volatility), pct(m.bench_volatility), "Standardabweichung der Tagesrenditen, annualisiert mit der Zahl der Kurse je Jahr."],
        ["Max. Drawdown", pct(m.max_drawdown), pct(m.bench_max_drawdown), "Größter Verlust vom Hoch zum Tief."],
        ["Dauer max. Drawdown", days(m.max_drawdown_days), days(m.bench_max_drawdown_days), "Kalendertage vom Hoch bis zur Erholung (bzw. bis zum Ende)."],
        ["Erholung nach dem Tief", recov(m.max_drawdown_recovery, m.max_drawdown_recovery_days),
          recov(m.bench_max_drawdown_recovery, m.bench_max_drawdown_recovery_days), "Kalendertage vom Tiefpunkt bis zum neuen Hoch."],
        ["Längste Unterwasserphase", days(m.longest_underwater_days), days(m.bench_longest_underwater_days), "Längste Zeit unter einem früheren Höchststand."],
        ["Ulcer-Index", pct(m.ulcer_index, 1), pct(m.bench_ulcer_index, 1), "Wurzel aus dem mittleren quadrierten Drawdown: misst Tiefe UND Dauer der Verluste."],
        ["VaR 95 % (1 Tag)", pct(m.var95), pct(m.bench_var95), "An 5 % der Tage war der Verlust mindestens so groß (historisch)."],
        ["CVaR 95 % (1 Tag)", pct(m.cvar95), pct(m.bench_cvar95), "Durchschnittlicher Verlust an den schlechtesten 5 % der Tage (Expected Shortfall)."],
        ["Schlechtester / bester Tag", `${pct(m.worst_day)} / ${pct(m.best_day)}`, `${pct(m.bench_worst_day)} / ${pct(m.bench_best_day)}`, ""],
        ["Schiefe / Wölbung", `${num(m.skewness)} / ${num(m.kurtosis, 1)}`, `${num(m.bench_skewness)} / ${num(m.bench_kurtosis, 1)}`,
          "Schiefe < 0: große Verluste häufiger als große Gewinne. Wölbung > 0: mehr Extremtage als bei Normalverteilung."],
      ]],
      ["Risikoadjustiert", [
        ["Sharpe-Ratio", num(m.sharpe), num(m.bench_sharpe), `Überrendite über den risikolosen Zins (${pct(m.risk_free, 1)} = Kasse-Zins) je Volatilität.`],
        ["Sortino-Ratio", num(m.sortino), num(m.bench_sortino), "Wie Sharpe, aber nur Schwankung unter dem risikolosen Zins zählt."],
        ["Calmar-Ratio", num(m.calmar), num(m.bench_calmar), "CAGR geteilt durch den Betrag des max. Drawdowns."],
        ["Martin-Ratio", num(m.martin), num(m.bench_martin), "Überrendite je Ulcer-Index (Ulcer Performance Index)."],
      ]],
      ["Bezug zu Buy & Hold", [
        ["Beta", num(m.beta), "1,00", "Wie stark die Strategie im Schnitt mit dem Markt mitgeht (1 = voll)."],
        ["Korrelation", num(m.correlation), "1,00", ""],
        ["Jensen-Alpha p. a.", pct(m.alpha), "–", "Rendite über dem, was das Beta erwarten ließe (jeweils über dem risikolosen Zins)."],
        ["Tracking Error", pct(m.tracking_error), "–", "Schwankung der Renditedifferenz zu Buy & Hold."],
        ["Information Ratio", num(m.information_ratio), "–", "Mehrrendite je Tracking Error."],
        ["Up-Capture", pct(m.up_capture, 0), "100 %", "Anteil der Buy-&-Hold-Gewinne in Monaten mit steigendem Markt."],
        ["Down-Capture", pct(m.down_capture, 0), "100 %", "Anteil der Buy-&-Hold-Verluste in fallenden Monaten – kleiner ist besser."],
        ["Mehrrendite p. a.", pct(m.excess_cagr), "–", "CAGR der Strategie minus CAGR Buy & Hold."],
      ]],
      ["Handel", [
        ["Zeit im Risiko", pct(m.time_in_market, 1), "100 %", "Anteil der Tage mit Signal grün und investiert."],
        ["Zeit investiert", pct(m.time_invested, 1), "100 %", "Inklusive Defensiv-ETF."],
        ["Orders / Wechsel pro Jahr", `${m.trades} / ${num(m.switches_per_year, 1)}`, "1 / –", ""],
        ["Gewinnquote", pct(m.win_rate, 1), "–", "Anteil der Verkäufe mit Gewinn."],
        ["Profit-Faktor", num(m.profit_factor), "–", "Summe der realisierten Gewinne / Summe der Verluste."],
        ["Ø Haltedauer", days(m.avg_holding_days), "–", ""],
        ["Fehlsignale (< 30 Tage)", ok(m.whipsaws) ? String(m.whipsaws) : "–", "–", "Positionen, die nach weniger als 30 Tagen wieder verkauft wurden."],
      ]],
      ["Kosten & Steuer", [
        ["Gebühren", eur2(m.fees), "–", "Ordergebühren inkl. Prozentgebühr (Slippage steckt in den Ausführungskursen)."],
        ["Steuer gezahlt (netto)", eur2(m.taxes), "–", "Bei Verkäufen und Zinsgutschriften gezahlte Steuer abzüglich Erstattungen."],
        ["Latente Steuer am Ende", eur2(m.tax_latent), eur2(m.bench_tax_latent), "Steuer, die bei einem Verkauf am letzten Tag anfiele."],
        ["Zinsertrag Kasse", eur2(m.interest), "–", ""],
        ["Steuerregel (Risiko-Anlage)", esc(tax.risk_rule_label || "–"), esc(tax.risk_rule_label || "–"), ""],
      ]],
      ["Zeitraum", [
        ["Zeitraum", `${dateDE(m.start)} – ${dateDE(m.end)}`, `${num(m.years, 1)} Jahre`, ""],
        ["Kurse je Jahr", num(m.periods_per_year, 0), "–", "≈252 an der Börse, ≈365 bei Krypto; Grundlage aller Annualisierungen."],
      ]],
    ];
    let html = `<table><thead><tr><th>Kennzahl</th><th>Strategie</th><th>Buy &amp; Hold</th></tr></thead><tbody>`;
    G.forEach(([grp, rows]) => {
      html += `<tr class="grp"><td colspan="3">${grp}</td></tr>`;
      rows.forEach(r => {
        html += `<tr><td${r[3] ? ` title="${esc(r[3])}"` : ""}>${r[0]}</td><td>${r[1]}</td><td class="dim">${r[2]}</td></tr>`;
      });
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
        <div class="s-sub">seit ${dateDE(s.dates[cur.from])} · ${daysInState} Kurstage</div>
      </div>`,
      `<div class="sig-card">
        <div class="s-lbl">Abstand zum Durchschnitt</div>
        <div class="s-val">${ok(ratio) ? num(ratio, 3) : "–"}</div>
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
      categories: y.map(r => String(r.year) + (r.partial ? "*" : "")),
      yFmt: v => (v * 100).toFixed(0) + " %",
      series: [
        { label: "Strategie", values: y.map(r => r.strategy), color: "#22d3ee" },
        { label: "Buy & Hold", values: y.map(r => r.benchmark), color: "#a78bfa" },
      ],
    });
    const pos = y.filter(r => r.strategy > 0).length;
    const beat = y.filter(r => r.strategy > r.benchmark).length;
    $("#legendYear").innerHTML =
      `<span><i style="background:#22d3ee"></i>Strategie (${pos}/${y.length} Jahre positiv, ${beat} besser als B&amp;H)</span>` +
      `<span><i style="background:#a78bfa"></i>Buy &amp; Hold</span><span>* Teiljahr</span>`;
  }

  function drawRolling(res) {
    const hs = ((res.rolling || {}).horizons || []);
    const sel = parseInt(getVal("rollH"), 10) || 3;
    const h = hs.find(x => x.years === sel);
    if (!h || !h.n) {
      $("#chartRoll").innerHTML = `<div class="hint" style="padding:14px">Zu kurzer Zeitraum für ${sel} Jahre.</div>`;
      $("#legendRoll").innerHTML = "";
    } else {
      Charts.lineChart($("#chartRoll"), {
        x: h.series.dates, xFmt: x => x.slice(0, 4), yFmt: v => (v * 100).toFixed(0) + " %",
        zeroLine: true,
        series: [
          { label: `Strategie ${sel} J. p. a.`, values: h.series.strategy, color: "#22d3ee", width: 1.8 },
          { label: `Buy & Hold ${sel} J. p. a.`, values: h.series.benchmark, color: "#a78bfa", width: 1.3, dash: "5 4" },
        ],
      });
      $("#legendRoll").innerHTML = `<span><i style="background:#22d3ee"></i>Strategie</span>` +
        `<span><i style="background:#a78bfa"></i>Buy &amp; Hold</span>`;
    }
    let html = `<table><thead><tr><th>Anlagedauer</th><th>Zeiträume</th><th>Strategie besser</th>
      <th>Verlust Strategie</th><th>Verlust B&amp;H</th><th>Schlechtester (S / B&amp;H)</th>
      <th>Median (S / B&amp;H)</th><th>Bester (S / B&amp;H)</th></tr></thead><tbody>`;
    hs.forEach(x => {
      if (!x.n) {
        html += `<tr><td>${x.years} Jahr${x.years > 1 ? "e" : ""}</td><td colspan="7" class="dim">zu kurzer Zeitraum</td></tr>`;
        return;
      }
      const s = x.strategy, b = x.benchmark;
      html += `<tr><td>${x.years} Jahr${x.years > 1 ? "e" : ""}</td><td>${nf0.format(x.n)}</td>
        <td class="${x.beats_share >= 0.5 ? "pos" : "neg"}">${pct(x.beats_share, 0)}</td>
        <td>${pct(s.neg_share, 0)}</td><td class="dim">${pct(b.neg_share, 0)}</td>
        <td>${pct(s.min, 1)} / <span class="dim">${pct(b.min, 1)}</span></td>
        <td>${pct(s.median, 1)} / <span class="dim">${pct(b.median, 1)}</span></td>
        <td>${pct(s.max, 1)} / <span class="dim">${pct(b.max, 1)}</span></td></tr>`;
    });
    html += "</tbody></table>";
    $("#rollTable").innerHTML = html;
  }

  function renderPeriods(res) {
    drawYears(res);
    Charts.heatmap($("#heatMonths"), res.monthly);
    drawRolling(res);
    let html = `<table><thead><tr><th>Jahr</th><th>Strategie</th><th>Buy &amp; Hold</th>
      <th>Differenz</th><th>Endstand</th><th>Endstand B&amp;H</th></tr></thead><tbody>`;
    res.yearly.slice().reverse().forEach(r => {
      html += `<tr><td>${r.year}${r.partial ? ' <span class="dim">(Teiljahr)</span>' : ""}</td>
        <td class="${signCls(r.strategy)}">${pct(r.strategy)}</td>
        <td class="dim">${pct(r.benchmark)}</td>
        <td class="${signCls(r.strategy - r.benchmark)}">${pct(r.strategy - r.benchmark)}</td>
        <td>${eur(r.end)}</td><td class="dim">${eur(r.bench_end)}</td></tr>`;
    });
    html += "</tbody></table>";
    $("#yearTable").innerHTML = html;
  }

  /* ----------------------------------------------------------- Render: Trades */
  function renderTrades(res) {
    const all = res.trades || [], m = res.metrics || {};
    const filter = getVal("tradeFilter") || "all";
    const rows = all.filter(t => filter === "all" || t.action === filter);
    $("#tradeKpi").innerHTML = [
      kpi("Orders", String(all.length), `${m.round_trips || 0} abgeschlossene Positionen`, "neutral"),
      kpi("Gewinnquote", pct(m.win_rate, 1), "Anteil Verkäufe mit Gewinn", "neutral"),
      kpi("Profit-Faktor", num(m.profit_factor), "Gewinne / Verluste (realisiert)",
        ok(m.profit_factor) ? (m.profit_factor >= 1 ? "good" : "bad") : "neutral"),
      kpi("Ø Gewinn / Verlust", `${eur(m.avg_win)} / ${eur(m.avg_loss)}`, "je Verkauf", "neutral"),
      kpi("Ø Haltedauer", days(m.avg_holding_days), `${m.whipsaws || 0} Fehlsignale unter 30 Tagen`, "neutral"),
      kpi("Längste Verlustserie", ok(m.max_loss_streak) ? String(m.max_loss_streak) : "–",
        "Verkäufe mit Verlust in Folge", "neutral"),
      kpi("Gebühren", eur2(m.fees), "Summe aller Orders", "neutral"),
      kpi("Steuern", eur2(m.taxes), `gezahlt · latent ${eur(m.tax_latent)}`, "neutral"),
    ].join("");
    let html = `<table><thead><tr><th>Datum</th><th>Aktion</th><th>Instrument</th>
      <th>Kurs</th><th>Stück</th><th>Wert</th><th>Gebühr</th><th>Steuer</th><th>Gewinn</th>
      <th>Haltedauer</th><th>Anlass</th></tr></thead><tbody>`;
    rows.forEach(t => {
      html += `<tr><td>${dateDE(t.date)}</td>
        <td><span class="tag ${t.action === "KAUF" ? "buy" : "sell"}">${t.action}</span></td>
        <td style="text-align:left">${esc(t.symbol)}</td>
        <td>${num(t.price, 4)}</td><td>${num(t.units, 3)}</td>
        <td>${eur2(t.value)}</td><td>${eur2(t.fee)}</td><td>${eur2(t.tax)}</td>
        <td class="${signCls(t.gain)}">${t.action === "VERKAUF" ? eur2(t.gain) : "–"}</td>
        <td>${ok(t.holding_days) ? days(t.holding_days) : "–"}</td>
        <td class="dim">${esc(t.reason || "")}</td></tr>`;
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
      let html = `<table><thead><tr><th>Instrument</th><th>Zeitraum</th><th>CAGR</th><th>B&amp;H</th>
        <th>Max. DD</th><th>DD B&amp;H</th><th>Sharpe</th><th>Calmar</th><th>Calmar B&amp;H</th>
        <th>Up / Down</th><th>Zeit im Risiko</th><th>Endwert</th><th>Daten</th></tr></thead><tbody>`;
      const good = j.rows.filter(r => r.ok).sort((a, b) => (b.cagr || -9) - (a.cagr || -9));
      good.forEach(r => {
        html += `<tr><td><b>${esc(r.symbol)}</b><div class="dim" style="font-size:11px">${esc(r.name)}</div></td>
          <td class="dim">${(r.start || "").slice(0, 4)}–${(r.end || "").slice(0, 4)}</td>
          <td class="${signCls(r.cagr)}">${pct(r.cagr)}</td>
          <td class="dim">${pct(r.bench_cagr)}</td>
          <td class="neg">${pct(r.max_drawdown)}</td><td class="dim">${pct(r.bench_max_drawdown)}</td>
          <td>${num(r.sharpe)}</td>
          <td class="${better(r.calmar, r.bench_calmar) ? "pos" : ""}">${num(r.calmar)}</td>
          <td class="dim">${num(r.bench_calmar)}</td>
          <td>${pct(r.up_capture, 0)} / ${pct(r.down_capture, 0)}</td>
          <td>${pct(r.time_in_market, 1)}</td>
          <td>${ok(r.end_capital) ? nf0.format(r.end_capital) : "–"}</td>
          <td><span class="q-${r.quality || "ok"}">${Q_LABEL[r.quality] || "–"}</span></td></tr>`;
      });
      j.rows.filter(r => !r.ok).forEach(r => {
        html += `<tr><td>${esc(r.symbol)}</td><td colspan="12" class="neg">${esc(r.error)}</td></tr>`;
      });
      html += "</tbody></table>";
      $("#compareOut").innerHTML = html +
        `<div class="hint">Zeiträume unterscheiden sich je Instrument – nur ähnliche Zeiträume direkt vergleichen.
        Endwerte in der jeweiligen Währung des Instruments.</div>`;
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
      const j = await apiPost("/api/grid", { config: readConfig(), objective: getVal("gridObj") || "calmar" });
      S.grid = j.rows;
      S.gridType = j.signal_type;
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
  const OBJ_LABEL = { calmar: "Calmar", martin: "Martin", sharpe: "Sharpe", cagr: "CAGR" };

  function paramText(p, type) {
    const keys = PARAM_ORDER[type] || Object.keys(p);
    return keys.filter(k => p[k] !== undefined && p[k] !== null)
      .map(k => `${PARAM_LABELS[k] || k} ${typeof p[k] === "number" ? num(p[k], Number.isInteger(p[k]) ? 0 : 2) : p[k]}`)
      .join(" · ");
  }
  const objFmt = (obj, v) => obj === "cagr" ? pct(v) : num(v);

  function renderGrid() {
    const obj = getVal("gridObj") || "calmar";
    const val = r => ok(r[obj]) ? r[obj] : -1e9;
    const rows = (S.grid || []).slice().sort((a, b) => val(b) - val(a));
    if (!rows.length) { $("#gridSummary").textContent = "Keine Ergebnisse."; return; }
    const best = rows[0];
    const stype = S.gridType || getVal("signalType") || "ampel";
    const near = rows.filter(r => best[obj] > 0 && r[obj] >= 0.8 * best[obj]).length;
    $("#gridSummary").innerHTML = `Beste Variante nach ${OBJ_LABEL[obj]}: <b>${paramText(best.params, stype)}</b> – ` +
      `CAGR ${pct(best.cagr)} · max. Verlust ${pct(best.max_drawdown)} · Calmar ${num(best.calmar)} · ` +
      `Martin ${num(best.martin)} (${rows.length} Kombinationen, ${dateDE(best.start)} – ${dateDE(best.end)}).<br>` +
      `Plateau: <b>${near}</b> von ${rows.length} Kombinationen (${pct(near / rows.length, 0)}) erreichen mindestens ` +
      `80 % des Bestwerts – je größer dieser Anteil, desto weniger hängt das Ergebnis an genau einem Parametersatz.`;
    drawGrid(rows, obj);
    let html = `<table><thead><tr><th>#</th><th>Parameter</th><th>CAGR</th><th>Max. DD</th>
      <th>Sharpe</th><th>Calmar</th><th>Martin</th><th>Vol.</th><th>Zeit im Risiko</th><th>Orders</th><th></th></tr></thead><tbody>`;
    rows.slice(0, 150).forEach((r, i) => {
      html += `<tr><td>${i + 1}</td><td class="txt" style="max-width:520px">${paramText(r.params, stype)}</td>
        <td class="${signCls(r.cagr)}">${pct(r.cagr)}</td>
        <td class="neg">${pct(r.max_drawdown)}</td>
        <td>${num(r.sharpe)}</td><td>${num(r.calmar)}</td><td>${num(r.martin)}</td>
        <td>${pct(r.volatility)}</td><td>${pct(r.time_in_market, 1)}</td><td>${r.trades}</td>
        <td><button class="btn mini" data-apply='${esc(JSON.stringify(r.params))}'>übernehmen</button></td></tr>`;
    });
    html += "</tbody></table>";
    $("#gridTable").innerHTML = html;
    $$("[data-apply]").forEach(b => b.addEventListener("click", () => {
      const p = JSON.parse(b.dataset.apply);
      const cfg = readConfig();
      applyConfig(Object.assign({}, cfg, { params: Object.assign({}, cfg.params, p) }));
      toast("Parameter übernommen – Analyse starten", "ok");
    }));
  }

  function drawGrid(rows, obj) {
    obj = obj || getVal("gridObj") || "calmar";
    $("#chartGrid").dataset.filled = "1";
    const xs = [], ys = [], map = {};
    rows.forEach(r => {
      const x = r.params.upper, y = r.params.sma_days;
      if (x === undefined || y === undefined) return;
      if (xs.indexOf(x) < 0) xs.push(x);
      if (ys.indexOf(y) < 0) ys.push(y);
      const k = y + "|" + x;
      if (!map[k] || (ok(r[obj]) ? r[obj] : -9) > (ok(map[k][obj]) ? map[k][obj] : -9)) map[k] = r;
    });
    xs.sort((a, b) => a - b); ys.sort((a, b) => a - b);
    if (S.gridType !== "ampel" || !xs.length || !ys.length) {
      $("#chartGrid").innerHTML = `<div class="hint">Matrix nur für die RSL-Ampel (RSL-Tage × Obergrenze); siehe Rangliste.</div>`;
      return;
    }
    const values = ys.map(y => xs.map(x => (map[y + "|" + x] || {})[obj]));
    Charts.heatmap($("#chartGrid"), {
      years: ys, months: xs.map(x => "Obergr. " + num(x, 2)), values,
      rowLabel: "RSL-Tage", fmt: "num",
    });
    $("#chartGrid").classList.remove("chart");
    $("#chartGrid").style.height = "auto";
  }

  /* ------------------------------------------------------ Render: Robustheit */
  async function runWalkforward() {
    busy(true, "Walk-Forward läuft … (volles Parameterraster, bis ca. 30 s)");
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
      const j = await apiPost("/api/backtest", { config: readConfig(), montecarlo: true });
      S.mc = j.result.montecarlo;
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
        { label: "ohne Steuer", mod: { tax_mode: "none" } },
        { label: "Gebühr fix 15", mod: { fee_fixed: 15 } },
        { label: "Gebühr fix 30", mod: { fee_fixed: 30 } },
        { label: "Slippage 25 bp", mod: { slippage_bps: 25 } },
        { label: "Slippage 50 bp", mod: { slippage_bps: 50 } },
        { label: "Ausführung 1 Tag später", mod: { exec_lag: (base.exec_lag || 0) + 1 } },
        { label: "Kasse ohne Zins", mod: { cash_rate: 0 } },
      ];
      const out = [];
      for (const v of variants) {
        const j = await apiPost("/api/backtest", { config: Object.assign({}, base, v.mod) });
        out.push({ label: v.label, m: j.result.metrics });
      }
      let html = `<table><thead><tr><th>Szenario</th><th>CAGR</th><th>Δ zu B&amp;H</th><th>Endkapital</th>
        <th>Max. DD</th><th>Calmar</th><th>Gebühren</th><th>Steuern</th></tr></thead><tbody>`;
      const baseCagr = out[0].m.cagr;
      out.forEach(r => {
        const d = r.m.cagr - baseCagr, ex = r.m.cagr - r.m.bench_cagr;
        html += `<tr><td>${r.label}</td>
          <td class="${signCls(r.m.cagr)}">${pct(r.m.cagr)}${r.label !== "Basisfall" ? ` <span class="dim">(${pct(d)})</span>` : ""}</td>
          <td class="${signCls(ex)}">${pct(ex)}</td>
          <td>${eur(r.m.end_capital)}</td>
          <td class="neg">${pct(r.m.max_drawdown)}</td>
          <td>${num(r.m.calmar)}</td>
          <td class="dim">${eur2(r.m.fees)}</td>
          <td class="dim">${eur2(r.m.taxes)}</td></tr>`;
      });
      html += "</tbody></table>";
      $("#sensOut").innerHTML = html + `<div class="hint">„Δ zu B&amp;H“ = CAGR der Strategie minus CAGR von Buy &amp; Hold
        unter denselben Annahmen. Bricht der Vorteil schon bei 1 Tag Verzögerung oder höheren Kosten ein,
        ist die Regel nicht belastbar.</div>`;
    } catch (e) {
      toast("Sensitivität fehlgeschlagen: " + e.message, "err");
    } finally { busy(false); }
  }

  function renderRobust() {
    if (S.wf) {
      if (!S.wf.oos) {
        $("#wfOut").innerHTML = `<div class="hint">${esc(S.wf.note || "Kein Walk-Forward-Ergebnis.")}</div>`;
        $("#chartWf").innerHTML = ""; $("#wfTable").innerHTML = "";
      } else {
        const m = S.wf.metrics || {}, ref = S.wf.reference || {}, n = S.wf.windows.length;
        let verdict = "";
        if (ok(ref.cagr) && ok(m.cagr)) {
          verdict = ref.cagr > m.cagr
            ? `<div class="notice warn">Die rollierende Optimierung war out-of-sample <b>schlechter</b> als die festen
               Basis-Parameter (${pct(ref.cagr)} p. a.) – ein typisches Zeichen für Überanpassung. Feste,
               plausible Parameter sind hier die robustere Wahl.</div>`
            : `<div class="notice info">Die rollierende Optimierung war out-of-sample mindestens so gut wie die festen
               Basis-Parameter (${pct(ref.cagr)} p. a.).</div>`;
        }
        $("#wfOut").innerHTML = `<div class="kpi-row small">
          ${kpi("OOS-CAGR", pct(m.cagr), `B&H ${pct(m.bench_cagr)} · ${dateDE(m.start)} – ${dateDE(m.end)}`,
            m.cagr >= m.bench_cagr ? "good" : "bad", signCls(m.cagr))}
          ${kpi("OOS max. Drawdown", pct(m.max_drawdown), `B&H ${pct(m.bench_max_drawdown)}`,
            m.max_drawdown >= m.bench_max_drawdown ? "good" : "bad", "neg")}
          ${kpi("OOS Calmar", num(m.calmar), `B&H ${num(m.bench_calmar)}`, better(m.calmar, m.bench_calmar) ? "good" : "bad")}
          ${kpi("Ohne Optimierung", pct(ref.cagr), `Basis-Parameter · max. DD ${pct(ref.max_drawdown)}`, "neutral")}
          ${kpi("Testfenster", `${S.wf.positive_windows}/${n} positiv`, `${S.wf.beat_windows} besser als B&H · ${S.wf.combos} Kombinationen`, "neutral")}
        </div>${verdict}`;
        drawWf(S.wf);
        let html = `<table><thead><tr><th>Training</th><th>Test</th><th>gewählte Parameter</th>
          <th>Ziel (Training)</th><th>Test Strategie</th><th>Test B&amp;H</th></tr></thead><tbody>`;
        S.wf.windows.forEach(w => {
          html += `<tr><td class="dim">${w.train[0].slice(0, 4)} – ${w.train[1].slice(0, 4)}</td>
            <td class="dim">${w.test[0].slice(0, 7)} – ${w.test[1].slice(0, 7)}</td>
            <td style="text-align:left">${paramText(w.params, getVal("signalType"))}</td>
            <td>${num(w.score, 3)}</td>
            <td class="${signCls(w.test_return)}">${pct(w.test_return)}</td>
            <td class="dim">${pct(w.bench_return)}</td></tr>`;
        });
        html += "</tbody></table>";
        $("#wfTable").innerHTML = html;
      }
    }
    if (S.mc && S.mc.p50) {
      const mc = S.mc, f = mc.final || {}, bf = mc.bench_final || {};
      $("#mcKpi").innerHTML = [
        kpi("Median nach 10 J.", eur(f.p50), `B&H ${eur(bf.bench_p50)} · ${pct(mc.cagr_p50, 1)} p. a.`, "neutral", "acc"),
        kpi("Schlechte 5 %", eur(f.p5), `B&H ${eur(bf.bench_p5)}`, better(f.p5, bf.bench_p5) ? "good" : "neutral"),
        kpi("Gute 5 %", eur(f.p95), `B&H ${eur(bf.bench_p95)}`, "neutral"),
        kpi("Verlust-Wahrscheinlichkeit", pct(mc.prob_loss, 1), `B&H ${pct(mc.bench_prob_loss, 1)} · nach 10 Jahren`,
          better(mc.bench_prob_loss, mc.prob_loss) ? "good" : "bad"),
        kpi("Strategie vor B&H", pct(mc.prob_beat, 0), "Anteil der simulierten Pfade", "neutral"),
        kpi("Typischer max. Drawdown", pct(mc.maxdd_p50, 1),
          `B&H ${pct(mc.bench_maxdd_p50, 1)} · schlechte 5 %: ${pct(mc.maxdd_p5, 0)}`,
          better(mc.maxdd_p50, mc.bench_maxdd_p50) ? "good" : "bad"),
      ].join("");
      drawMc(mc);
      $("#mcOut").innerHTML = `${mc.n_paths} Pfade über ${num(mc.years, 0)} Jahre aus historischen Blöcken à
        ${mc.block_days} Kurstagen (Strategie und Buy &amp; Hold mit denselben Blöcken; Krisenhäufungen bleiben
        erhalten), Start ${eur(mc.start)}. Keine Prognose – nur die Bandbreite, die aus dem bisherigen Verhalten folgt.`;
    }
  }

  function drawWf(wf) {
    $("#chartWf").dataset.filled = "1";
    const o = wf.oos;
    Charts.lineChart($("#chartWf"), {
      x: o.dates, xFmt: x => x.slice(0, 4), yFmt: v => nf0.format(v) + " " + S.cur,
      series: [
        { label: "Out-of-Sample", values: o.strategy, color: "#34d399", width: 2, fill: true },
        { label: "Buy & Hold", values: o.benchmark, color: "#a78bfa", width: 1.3, dash: "5 4" },
      ],
    });
  }

  function drawMc(mc) {
    $("#chartMc").dataset.filled = "1";
    const n = mc.p50.length, ppy = mc.periods_per_year || 252;
    const labels = Array.from({ length: n }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() + Math.round(i * mc.step_days * 365.25 / ppy));
      return d.toISOString().slice(0, 10);
    });
    const spread = mc.p95[n - 1] / Math.max(1, mc.p5[n - 1]);
    Charts.lineChart($("#chartMc"), {
      x: labels, xFmt: x => x.slice(0, 7), yFmt: v => nf0.format(v) + " " + S.cur, log: spread > 30,
      series: [
        { label: "95 %-Quantil", values: mc.p95, color: "#34d399", width: 1.1, dash: "4 4" },
        { label: "Median Strategie", values: mc.p50, color: "#22d3ee", width: 2.2 },
        { label: "5 %-Quantil", values: mc.p5, color: "#f87171", width: 1.1, dash: "4 4" },
        mc.bench_p50 && mc.bench_p50.length ? { label: "Median Buy & Hold", values: mc.bench_p50, color: "#a78bfa", width: 1.5, dash: "6 3" } : null,
        { label: "Start", values: labels.map(() => mc.start), color: "#64748b", width: 1, dash: "2 3" },
      ].filter(Boolean),
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
        <div class="sr-item" data-sym="${esc(r.symbol)}">
          <div style="min-width:0">
            <div class="sr-sym">${esc(r.symbol)}</div>
            <div class="sr-name">${esc(r.name)}</div>
          </div>
          <div class="sr-meta">${esc(r.exchange)} · ${esc(r.type)}</div>
        </div>`).join("");
      $$(".sr-item").forEach(el => el.addEventListener("click", async () => {
        const sym = el.dataset.sym;
        if (S.searchTarget) setVal(S.searchTarget, sym);
        closeSearch();
        await downloadSymbols([sym]);
      }));
    } catch (e) {
      $("#searchResults").innerHTML = `<div class="hint neg">Fehler: ${esc(e.message)}</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
