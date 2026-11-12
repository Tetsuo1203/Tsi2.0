"""
ETF-Timing-Werkzeug -- lokaler Webserver + REST-API.

Startet einen HTTP-Server auf 127.0.0.1, liefert die eingebettete Oberfläche aus
und rechnet Backtests, Parameter-Suchen und Daten-Downloads serverseitig
(deshalb gibt es keine CORS-Probleme mit Yahoo Finance).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

import data as D
import engine as E

VERSION = "1.1"
APP_NAME = "ETF-Timing-Studio"

# --------------------------------------------------------------------------- #
# Strategie-Presets
# --------------------------------------------------------------------------- #

ORIGINAL_ROTATION = {
    "green": ["^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@1", "^GDAXI@2", "^GDAXI@2",
              "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2"],
    "red": ["^GDAXI@-2", "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@2", "^GDAXI@-2", "^GDAXI@-2",
            "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@2", "^GDAXI@2", "^GDAXI@-2"],
}

# Kosten/Steuer für einen deutschen Privatanleger (Wertpapierdepot)
COSTS_DE = {"exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
            "slippage_bps": 5.0, "tax_mode": "accrued", "tax_rate": 26.375,
            "tax_rule": "auto", "tax_allowance": 1000.0, "tax_rate_private": 35.0,
            "tax_exemption_private": 1000.0, "tax_interest": True, "loss_offset": True,
            "cash_rate": 2.0}
# Krypto-Börse/Broker: prozentuale Gebühr, höhere Spreads
COSTS_CRYPTO = dict(COSTS_DE, fee_fixed=0.0, fee_pct=0.25, slippage_bps=15.0)
AMPEL_ORIGINAL = {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
                  "days_green": 16}

PRESETS = [
    {
        "id": "ampel-world",
        "name": "RSL-Ampel · MSCI World (Kasse)",
        "hint": "Original-Logik (130 Tage, 1,05/0,92, 19/16) auf einen weltweiten ETF, "
                "Signal aus dem S&P 500. Defensiv = Kasse.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="^GSPC", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "ampel-dax",
        "name": "RSL-Ampel · DAX-ETF (Kasse)",
        "hint": "Gleiche Ampel-Logik, Signal direkt aus dem DAX, Risiko im DAX-ETF.",
        "config": dict(COSTS_DE, risk="EXS1.DE", signal="^GDAXI", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "sma200-world",
        "name": "200-Tage-SMA · MSCI World (Kasse)",
        "hint": "Der Klassiker: investiert, solange der ETF über seinem 200-Tage-Schnitt "
                "liegt, sonst Kasse.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="IWDA.AS", defensive_is_cash=True,
                       signal_type="sma", params={"sma_days": 200, "band": 0.0}),
    },
    {
        "id": "goldencross-sp500",
        "name": "Golden Cross 50/200 · S&P-500-ETF",
        "hint": "50-Tage-Schnitt über 200-Tage-Schnitt. Start 09/2011: Die Yahoo-Reihe von "
                "SXR8.DE hat am 01.11.2010 einen Datenfehler (−24,6 %), der sonst Signal und "
                "Buy & Hold verfälscht.",
        "config": dict(COSTS_DE, risk="SXR8.DE", signal="SXR8.DE", defensive_is_cash=True,
                       signal_type="dual_sma", params={"sma_short": 50, "sma_long": 200},
                       start="2011-09-01"),
    },
    {
        "id": "momentum-world",
        "name": "Momentum 12 Monate · MSCI World",
        "hint": "Investiert, wenn die 12-Monats-Rendite positiv ist.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="IWDA.AS", defensive_is_cash=True,
                       signal_type="momentum", params={"momentum_days": 252}),
    },
    {
        "id": "btc-sma200",
        "name": "Bitcoin · 200-Tage-SMA (Kasse)",
        "hint": "Bitcoin in Euro, investiert über dem 200-Tage-Schnitt (7 Handelstage je Woche). "
                "Krypto-Kosten 0,25 % + 0,15 % Spread; Steuer §23: steuerfrei nach 1 Jahr "
                "Haltedauer, sonst persönlicher Steuersatz.",
        "config": dict(COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", defensive_is_cash=True,
                       signal_type="sma", params={"sma_days": 200, "band": 0.0}),
    },
    {
        "id": "btc-momentum",
        "name": "Bitcoin · Momentum 365 Tage (Kasse)",
        "hint": "Bitcoin in Euro, investiert bei positiver 1-Jahres-Rendite (365 Kalendertage). "
                "Wenige Wechsel – Gewinne bleiben öfter länger als 1 Jahr liegen (§23).",
        "config": dict(COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", defensive_is_cash=True,
                       signal_type="momentum", params={"momentum_days": 365}),
    },
    {
        "id": "ampel-sp500-long",
        "name": "RSL-Ampel · S&P 500 seit 1970 (Krisen-Test)",
        "hint": "Lange Historie: zeigt, wie die Ampel die großen Bärenmärkte 1973/74, 2000-02 "
                "und 2008 vermeidet – auf Kosten von Rendite in starken Aufwärtsphasen. "
                "^GSPC ist ein Kursindex ohne Dividenden; mit Dividenden: ^SP500TR (ab 1988).",
        "config": dict(COSTS_DE, risk="^GSPC", signal="^GSPC", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "original-comparison",
        "name": "Original-Nachbau (Vergleich): 2x / −2x DAX monatlich",
        "hint": "Die Strategie aus ETF_Simulation.xlsm: LevDAX bei grün, ShortDAX (x2) bei rot, "
                "monatliche Zuordnung. Hebel synthetisch aus dem DAX (Finanzierung 2 % p. a., "
                "Kosten 0,3 % je Hebelfaktor). Steuer wie im Original (ohne Teilfreistellung "
                "und Pauschbetrag).",
        "config": dict(COSTS_DE, risk="^GDAXI@2", signal="^GSPC", defensive_is_cash=False,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL),
                       rotation=ORIGINAL_ROTATION, exec_lag=0, slippage_bps=0.0,
                       cash_rate=0.0, tax_rule="securities", tax_allowance=0.0,
                       tax_interest=False, start="2000-01-01", end="2015-12-23"),
    },
]

SIGNAL_TYPES = [{"id": k, "name": v} for k, v in E.SIGNAL_INFO.items()]

DEFAULTS = dict(COSTS_DE, **{
    "risk": "IWDA.AS", "signal": "^GSPC", "defensive": None, "defensive_is_cash": True,
    "signal_type": "ampel",
    "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
               "days_green": 16, "band": 0.0, "sma_short": 50, "sma_long": 200,
               "momentum_days": 252},
    "start": None, "end": None, "rotation": None, "synth_rf": 2.0, "synth_cost": 0.3,
})

_synth_cache: Dict[str, E.Series] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #

def parse_synth(sym: str):
    """'^GDAXI@2' -> ('^GDAXI', 2.0); None wenn kein synthetisches Symbol."""
    if "@" not in sym:
        return None
    base, _, fac = sym.rpartition("@")
    try:
        return base, float(fac)
    except ValueError:
        return None


def get_series(symbol: str, cfg: Optional[Dict] = None,
               auto_download: bool = True) -> E.Series:
    """Liefert eine Reihe: Cache -> Download -> synthetischer Hebel."""
    syn = parse_synth(symbol)
    if syn:
        base, factor = syn
        rf = float((cfg or {}).get("synth_rf", 2.0) or 0.0)
        cost = float((cfg or {}).get("synth_cost", 0.3) or 0.0)
        key = f"{base}@{factor}@{rf}@{cost}"
        with _lock:
            if key in _synth_cache:
                return _synth_cache[key]
        b = get_series(base, cfg, auto_download)
        s = D.synthetic_leverage(b, factor, rf_pct=rf, spread_pct=cost)
        with _lock:
            _synth_cache[key] = s
        return s
    cached = D.load_series(symbol)
    if cached is not None and len(cached) > 0:
        last = cached.dates[-1]
        if last >= date.today() - timedelta(days=4):
            return cached
        if auto_download:
            try:
                return D.get_or_download(symbol)
            except Exception:                                   # noqa: BLE001
                return cached
        return cached
    if not auto_download:
        raise FileNotFoundError(f"{symbol} ist nicht im Datenbestand.")
    return D.get_or_download(symbol)


def normalize_config(cfg: Dict) -> Dict:
    out = dict(DEFAULTS)
    out.update(cfg or {})
    params = dict(DEFAULTS["params"])
    params.update((cfg or {}).get("params") or {})
    out["params"] = params
    if not out.get("start"):
        out["start"] = None
    if not out.get("end"):
        out["end"] = None
    return out


def load_instruments(cfg: Dict):
    """Risiko-, Signal-, Defensiv-Reihe und alle Instrumente einer Monatsrotation laden."""
    risk = get_series(cfg["risk"], cfg)
    sig = get_series(cfg["signal"], cfg)
    defensive = None
    if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
        defensive = get_series(cfg["defensive"], cfg)
    instruments = {s.symbol: s for s in (risk, sig, defensive) if s is not None}
    if cfg.get("rotation"):
        for row in ("green", "red"):
            for sym in (cfg["rotation"].get(row) or []):
                if sym and sym not in instruments:
                    try:
                        instruments[sym] = get_series(sym, cfg)
                    except Exception:                           # noqa: BLE001
                        pass
    return risk, sig, defensive, instruments


def quality_for(cfg: Dict, risk: E.Series, sig: E.Series, defensive: Optional[E.Series],
                result: Dict) -> List[Dict]:
    """Datenqualität der verwendeten Reihen im Analysezeitraum (Signal inkl. Vorlauf)."""
    start, end = E.pdate(result["meta"]["start"]), E.pdate(result["meta"]["end"])
    p = cfg.get("params") or {}
    look = {"ampel": p.get("sma_days", 130), "sma": p.get("sma_days", 200),
            "dual_sma": p.get("sma_long", 200), "momentum": p.get("momentum_days", 252)}
    lb_days = int(float(look.get(cfg.get("signal_type"), 0) or 0) * 1.5) + 10
    out: List[Dict] = []
    seen = set()

    def add(s: Optional[E.Series], role: str, ref: Optional[E.Series], hint: bool,
            q_start: Optional[date]) -> None:
        if s is None or s.symbol in seen or s.source == "synthetic":
            return
        seen.add(s.symbol)
        rep = E.quality_report(s, start=q_start, end=end, ref=ref, check_age=False,
                               price_index_hint=hint)
        rep["role"] = role
        out.append(rep)

    add(risk, "Risiko-Anlage", sig if sig.symbol != risk.symbol else None, True, start)
    add(sig, "Signalgeber", None, False, start - timedelta(days=lb_days) if start else None)
    add(defensive, "Defensiv-Anlage", None, True, start)
    if sig.dates and end and sig.dates[-1] < end - timedelta(days=5):
        for rep in out:
            if rep["symbol"] == sig.symbol:
                rep["issues"].insert(0, {"level": "warn", "type": "Signalende", "date": sig.dates[-1].isoformat(),
                                         "text": f"Signaldaten enden am {sig.dates[-1].strftime('%d.%m.%Y')} – "
                                                 "danach bleibt das Signal eingefroren."})
                rep["n_warn"] += 1
                rep["level"] = "warn"
    return out


def run_config(cfg: Dict, with_mc: bool = False) -> Dict:
    cfg = normalize_config(cfg)
    risk, sig, defensive, instruments = load_instruments(cfg)
    result = E.run_backtest(risk, sig, cfg, defensive, instruments)
    meta = result["meta"]
    meta["risk_name"] = risk.name
    meta["signal_name"] = sig.name
    meta["risk_currency"] = risk.currency
    meta["signal_currency"] = sig.currency
    meta["risk_first"] = risk.dates[0].isoformat() if risk.dates else None
    meta["risk_last"] = risk.dates[-1].isoformat() if risk.dates else None
    meta["config"] = cfg
    try:
        result["quality"] = quality_for(cfg, risk, sig, defensive, result)
    except Exception:                                           # noqa: BLE001
        traceback.print_exc()
        result["quality"] = []
    if with_mc:
        eq = result["equity"]
        result["montecarlo"] = E.monte_carlo(eq["strategy"], eq["benchmark"],
                                             ppy=meta.get("periods_per_year", 252.0),
                                             years=10.0, start=float(cfg.get("capital") or 10000.0))
    return result


def b64_encode(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _de(v, nd: int = 2) -> str:
    """Zahl im deutschen Format für den CSV-Export (Dezimalkomma)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.{nd}f}".replace(".", ",")
    return str(v)


def results_csv(res: Dict) -> str:
    """Semikolon-CSV mit Dezimalkomma (öffnet sich in Excel/LibreOffice deutsch direkt)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    eq = res["equity"]
    w.writerow(["Datum", "Strategie", "Benchmark", "Drawdown", "Drawdown Benchmark", "Signal",
                "Position"])
    for i, d in enumerate(eq["dates"]):
        w.writerow([d, _de(eq["strategy"][i]), _de(eq["benchmark"][i]), _de(eq["drawdown"][i], 6),
                    _de(eq["bench_drawdown"][i], 6), eq["state"][i], eq["position"][i]])
    w.writerow([])
    w.writerow(["Kennzahl", "Wert"])
    for k, v in res["metrics"].items():
        w.writerow([k, _de(v, 6) if isinstance(v, float) else _de(v)])
    w.writerow([])
    w.writerow(["Jahr", "Strategie", "Benchmark", "Endstand", "Endstand Benchmark", "Teiljahr"])
    for y in res["yearly"]:
        w.writerow([y["year"], _de(y["strategy"], 6), _de(y["benchmark"], 6), _de(y["end"]),
                    _de(y.get("bench_end")), _de(bool(y.get("partial")))])
    w.writerow([])
    w.writerow(["Trades"])
    w.writerow(["Datum", "Aktion", "Instrument", "Kurs", "Stueck", "Wert", "Gebuehr",
                "Steuer", "Gewinn", "Haltedauer Tage"])
    for t in res["trades"]:
        w.writerow([t["date"], t["action"], t["symbol"], _de(t["price"], 4), _de(t["units"], 6),
                    _de(t["value"]), _de(t["fee"]), _de(t["tax"]), _de(t["gain"]),
                    _de(t.get("holding_days"))])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# HTTP-Handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, fmt, *args):                          # noqa: A003
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---- Antwort-Helfer
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _err(self, msg: str, code: int = 400, **extra):
        self._json({"ok": False, "error": msg, **extra}, code)

    def _body(self) -> Dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:                                       # noqa: BLE001
            return {}

    # ---- GET
    def do_GET(self):                                          # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path == "/favicon.ico":
                self._send(200, FAVICON, "image/svg+xml")
                return
            if u.path == "/api/bootstrap":
                self._json({
                    "ok": True, "version": VERSION, "app": APP_NAME,
                    "defaults": DEFAULTS, "presets": PRESETS,
                    "signal_types": SIGNAL_TYPES,
                    "tax_rules": E.TAX_RULE_CHOICES,
                    "cached": D.list_cached(),
                    "suggestions": D.PRESET_INSTRUMENTS,
                    "today": date.today().isoformat(),
                    "data_dir": D.DATA_DIR,
                })
                return
            if u.path == "/api/search":
                term = (q.get("q") or [""])[0]
                try:
                    self._json({"ok": True, "results": D.yahoo_search(term)})
                except Exception as e:                          # noqa: BLE001
                    self._err(f"Suche fehlgeschlagen: {e}", 502)
                return
            if u.path == "/api/series":
                sym = (q.get("symbol") or [""])[0]
                if not sym:
                    self._err("symbol fehlt")
                    return
                try:
                    s = get_series(sym)
                except Exception as e:                          # noqa: BLE001
                    self._err(str(e), 404)
                    return
                step = max(1, len(s) // 1500)
                self._json({"ok": True, "symbol": s.symbol, "name": s.name,
                            "currency": s.currency,
                            "dates": [d.isoformat() for d in s.dates[::step]],
                            "close": [round(v, 4) for v in s.close[::step]],
                            "n": len(s)})
                return
            if u.path == "/api/quality":
                sym = (q.get("symbol") or [""])[0]
                if not sym:
                    self._err("symbol fehlt")
                    return
                s = D.load_series(sym)
                if s is None:
                    self._err(f"{sym} ist nicht im Datenbestand.", 404)
                    return
                ref_sym = (q.get("ref") or [""])[0]
                ref = D.load_series(ref_sym) if ref_sym else None
                rep = E.quality_report(s, ref=ref)
                if not ref_sym:
                    try:
                        D.update_quality(sym, E.quality_report(s, check_age=False))
                    except Exception:                           # noqa: BLE001
                        pass
                self._json({"ok": True, "report": rep})
                return
            if u.path == "/api/health":
                self._json({"ok": True, "version": VERSION})
                return
            if u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)),
                                 daemon=True).start()
                return
            self._err("Unbekannter Pfad: " + u.path, 404)
        except Exception as e:                                  # noqa: BLE001
            traceback.print_exc()
            self._err(f"Interner Fehler: {e}", 500)

    # ---- POST
    def do_POST(self):                                         # noqa: N802
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/download":
                symbols = body.get("symbols") or []
                if isinstance(symbols, str):
                    symbols = [s.strip() for s in symbols.replace(",", " ").split() if s.strip()]
                out = []
                for sym in symbols[:40]:
                    try:
                        s = D.get_or_download(sym, force=bool(body.get("force")))
                        out.append({"symbol": sym, "ok": True, "name": s.name,
                                    "rows": len(s),
                                    "first": s.dates[0].isoformat() if s.dates else None,
                                    "last": s.dates[-1].isoformat() if s.dates else None})
                    except Exception as e:                      # noqa: BLE001
                        out.append({"symbol": sym, "ok": False, "error": str(e)})
                with _lock:
                    _synth_cache.clear()
                self._json({"ok": True, "results": out, "cached": D.list_cached()})
                return

            if u.path == "/api/import":
                text = body.get("csv") or ""
                name = body.get("name") or "Import"
                symbol = (body.get("symbol") or name).upper().replace(" ", "_")[:32]
                try:
                    s = D.parse_csv(text, symbol=symbol, name=name)
                    D.save_series(s)
                    self._json({"ok": True, "symbol": s.symbol, "rows": len(s),
                                "first": s.dates[0].isoformat(), "last": s.dates[-1].isoformat(),
                                "date_order": s.meta.get("csv_date_order"),
                                "decimal": s.meta.get("csv_decimal"),
                                "quality": E.quality_report(s, check_age=False),
                                "cached": D.list_cached()})
                except Exception as e:                          # noqa: BLE001
                    self._err(f"Import fehlgeschlagen: {e}")
                return

            if u.path == "/api/delete":
                sym = body.get("symbol")
                ok = D.delete_series(sym) if sym else False
                with _lock:
                    _synth_cache.clear()
                self._json({"ok": ok, "cached": D.list_cached()})
                return

            if u.path == "/api/backtest":
                cfg = body.get("config") or {}
                res = run_config(cfg, with_mc=bool(body.get("montecarlo")))
                self._json({"ok": True, "result": res})
                return

            if u.path == "/api/grid":
                cfg = normalize_config(body.get("config") or {})
                risk, sig, defen, instruments = load_instruments(cfg)
                objective = body.get("objective") or "calmar"
                rows = E.run_grid(risk, sig, cfg, defen,
                                  max_runs=int(body.get("max_runs") or 500),
                                  instruments=instruments, objective=objective)
                self._json({"ok": True, "rows": rows, "signal_type": cfg["signal_type"],
                            "objective": objective})
                return

            if u.path == "/api/walkforward":
                cfg = normalize_config(body.get("config") or {})
                risk, sig, defen, instruments = load_instruments(cfg)
                wf = E.walk_forward(risk, sig, cfg, defen,
                                    int(body.get("train_years") or 6),
                                    int(body.get("test_years") or 2),
                                    body.get("objective") or "calmar",
                                    instruments=instruments)
                self._json({"ok": True, "walkforward": wf})
                return

            if u.path == "/api/compare":
                cfg = normalize_config(body.get("config") or {})
                symbols = body.get("symbols") or []
                rows = []
                for sym in symbols[:20]:
                    c2 = dict(cfg, risk=sym)
                    if c2.get("rotation"):
                        c2["rotation"] = None
                        c2["defensive_is_cash"] = True
                    try:
                        r = run_config(c2)
                        m = r["metrics"]
                        row = {"symbol": sym, "name": r["meta"].get("risk_name"), "ok": True,
                               "start": m.get("start"), "end": m.get("end"),
                               "tax_rule": r["meta"]["tax"]["risk_rule_label"],
                               "quality": max((q["level"] for q in r.get("quality") or []),
                                              key=lambda x: {"ok": 0, "info": 1, "warn": 2}[x],
                                              default="ok")}
                        for k in ("cagr", "bench_cagr", "max_drawdown", "bench_max_drawdown",
                                  "sharpe", "bench_sharpe", "calmar", "bench_calmar", "martin",
                                  "volatility", "up_capture", "down_capture", "end_capital",
                                  "time_in_market"):
                            row[k] = m.get(k)
                        rows.append(row)
                    except Exception as e:                      # noqa: BLE001
                        rows.append({"symbol": sym, "ok": False, "error": str(e)})
                self._json({"ok": True, "rows": rows})
                return

            if u.path == "/api/export":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                text = results_csv(res)
                fname = (f"etf_timing_{(cfg.get('risk') or 'risiko').replace('^', '').replace('@', '_')}"
                         f"_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
                self._json({"ok": True, "filename": fname,
                            "content": b64_encode("\ufeff" + text),
                            "encoding": "base64-utf8"})
                return

            if u.path == "/api/export_json":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                self._json({"ok": True, "result": res})
                return

            self._err("Unbekannter Pfad: " + u.path, 404)
        except Exception as e:                                  # noqa: BLE001
            traceback.print_exc()
            self._err(f"Fehler: {e}", 500)


FAVICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           b'<rect width="64" height="64" rx="12" fill="#0f172a"/>'
           b'<path d="M8 46 L22 30 L32 38 L56 12" stroke="#22d3ee" stroke-width="5" '
           b'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
           b'<circle cx="56" cy="12" r="5" fill="#f59e0b"/></svg>')

UI_HTML = "<!doctype html><html><body>UI nicht eingebettet</body></html>"


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #

def find_free_port(preferred: int = 8765) -> int:
    for p in [preferred, 0]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            continue
    return 8765


def selftest() -> int:
    """Prüft Rechenregeln und behobene Fehler – ohne Internet, ohne Excel."""
    print(f"{APP_NAME} {VERSION} -- Selbsttest (ohne Internet und ohne Excel)")
    t0 = time.time()
    res = E.self_checks() + D.data_self_checks()
    for r in res:
        mark = "ok    " if r["ok"] else "FEHLER"
        print(f"  [{mark}] {r['name']}" + (f"  ({r['detail']})" if r["detail"] else ""))
    n_ok = sum(1 for r in res if r["ok"])
    print(f"  Ergebnis: {n_ok} von {len(res)} Prüfungen bestanden ({time.time() - t0:.1f} s).")
    return 0 if n_ok == len(res) else 1


def _print_quality(rep: Dict) -> None:
    print(f"{rep['symbol']}: {rep.get('name', '')} – {rep['rows']} Kurse "
          f"{rep.get('first')} .. {rep.get('last')} · {rep.get('calendar', '')} · Stufe {rep['level']}")
    for it in rep["issues"]:
        print(f"   [{it['level']:4s}] {it['type']}: {it['text']}")


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):                    # Konsole ohne UTF-8: nicht abstürzen
        try:
            stream.reconfigure(errors="replace")
        except Exception:                                       # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=f"{APP_NAME} -- Timing-Analyse für Aktien, ETFs und Krypto")
    ap.add_argument("--port", type=int, default=8765, help="Port (Standard 8765)")
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    ap.add_argument("--selftest", action="store_true", help="Rechenregeln prüfen und beenden")
    ap.add_argument("--download", nargs="+", metavar="SYMBOL",
                    help="Symbole herunterladen und beenden")
    ap.add_argument("--start", help="Startdatum (JJJJ-MM-TT) für --download")
    ap.add_argument("--quality", nargs="+", metavar="SYMBOL",
                    help="Datenqualität geladener Reihen prüfen und beenden")
    ap.add_argument("--backtest", metavar="KONFIG_JSON",
                    help="Backtest headless mit JSON-Konfiguration")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.download:
        for sym in args.download:
            try:
                s = D.get_or_download(sym, start=E.pdate(args.start))
                print(f"OK   {sym:14s} {len(s):5d} Kurse  {s.dates[0]} .. {s.dates[-1]}  {s.name}")
            except Exception as e:                              # noqa: BLE001
                print(f"FEHL {sym:14s} {e}")
        return 0

    if args.quality:
        for sym in args.quality:
            s = D.load_series(sym)
            if s is None:
                print(f"{sym}: nicht im Datenbestand (erst mit --download laden)")
                continue
            _print_quality(E.quality_report(s))
        return 0

    if args.backtest:
        try:
            cfg = json.loads(args.backtest)
        except json.JSONDecodeError as e:
            print("Ungültiges JSON:", e)
            return 2
        res = run_config(cfg)
        print(json.dumps(res["metrics"], indent=2, ensure_ascii=False))
        for rep in res.get("quality") or []:
            if rep["level"] == "warn":
                _print_quality(rep)
        return 0

    port = find_free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 74)
    print(f"  {APP_NAME} {VERSION}")
    print("=" * 74)
    print(f"  Oberfläche:  {url}")
    print(f"  Datenordner: {D.DATA_DIR}")
    print("  Beenden:     Strg+C im Fenster oder Button 'Beenden'")
    print("=" * 74)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
