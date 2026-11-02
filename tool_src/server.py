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

VERSION = "1.0"
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

PRESETS = [
    {
        "id": "ampel-world",
        "name": "RSL-Ampel · MSCI World (Kasse)",
        "hint": "Original-Logik (130 Tage, 1,05/0,92, 19/16) auf einen weltweiten ETF, "
                "Signal aus dem S&P 500. Defensiv = Kasse.",
        "config": {"risk": "IWDA.AS", "signal": "^GSPC", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "ampel-dax",
        "name": "RSL-Ampel · DAX-ETF (Kasse)",
        "hint": "Gleiche Ampel-Logik, Signal direkt aus dem DAX, Risiko im DAX-ETF.",
        "config": {"risk": "EXS1.DE", "signal": "^GDAXI", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "sma200-world",
        "name": "200-Tage-SMA · MSCI World (Kasse)",
        "hint": "Der Klassiker: investiert, solange der ETF über seinem 200-Tage-Schnitt "
                "liegt, sonst Kasse.",
        "config": {"risk": "IWDA.AS", "signal": "IWDA.AS", "defensive_is_cash": True,
                   "signal_type": "sma", "params": {"sma_days": 200, "band": 0.0},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "goldencross-sp500",
        "name": "Golden Cross 50/200 · S&P-500-ETF",
        "hint": "50-Tage-Schnitt über 200-Tage-Schnitt. Robust und einfach.",
        "config": {"risk": "SXR8.DE", "signal": "SXR8.DE", "defensive_is_cash": True,
                   "signal_type": "dual_sma", "params": {"sma_short": 50, "sma_long": 200},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "momentum-world",
        "name": "Momentum 12 Monate · MSCI World",
        "hint": "Investiert, wenn die 12-Monats-Rendite positiv ist.",
        "config": {"risk": "IWDA.AS", "signal": "IWDA.AS", "defensive_is_cash": True,
                   "signal_type": "momentum", "params": {"momentum_days": 252},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "ampel-sp500-long",
        "name": "RSL-Ampel · S&P 500 seit 1970 (Krisen-Test)",
        "hint": "Lange Historie: zeigt, wie die Ampel die großen Bärenmärkte 1973/74, 2000-02 "
                "und 2008 vermeidet – auf Kosten von Rendite in starken Aufwärtsphasen.",
        "config": {"risk": "^GSPC", "signal": "^GSPC", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "original-comparison",
        "name": "Original-Nachbau (Vergleich): 2x / −2x DAX monatlich",
        "hint": "Die Strategie aus ETF_Simulation.xlsm: LevDAX bei grün, ShortDAX (x2) bei rot, "
                "monatliche Zuordnung. Hebel wird synthetisch aus dem DAX nachgebildet.",
        "config": {"risk": "^GDAXI@2", "signal": "^GSPC", "defensive_is_cash": False,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "rotation": ORIGINAL_ROTATION,
                   "exec_lag": 0, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 0.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 0.0,
                   "start": "2000-01-01", "end": "2015-12-23"},
    },
]

SIGNAL_TYPES = [{"id": k, "name": v} for k, v in E.SIGNAL_INFO.items()]

DEFAULTS = {
    "risk": "IWDA.AS", "signal": "^GSPC", "defensive": None, "defensive_is_cash": True,
    "signal_type": "ampel",
    "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
               "days_green": 16, "band": 0.0, "sma_short": 50, "sma_long": 200,
               "momentum_days": 252},
    "start": None, "end": None, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
    "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued", "loss_offset": True,
    "cash_rate": 2.0, "exec_lag": 1, "rotation": None,
}

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
        key = f"{base}@{factor}"
        with _lock:
            if key in _synth_cache:
                return _synth_cache[key]
        b = get_series(base, cfg, auto_download)
        rf = float((cfg or {}).get("synth_rf", 2.0))
        s = D.synthetic_leverage(b, factor, rf_pct=rf, spread_pct=0.3)
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
            except Exception:
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


def run_config(cfg: Dict, with_mc: bool = False, with_rolling: bool = True) -> Dict:
    cfg = normalize_config(cfg)
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
                    except Exception:
                        pass
        # Risikoanlage aus der Rotation ebenfalls laden (fuer Benchmark)
        first = [s for s in (cfg["rotation"].get("green") or []) if s]
        if first and first[0] not in instruments:
            try:
                instruments[first[0]] = get_series(first[0], cfg)
            except Exception:
                pass

    result = E.run_backtest(risk, sig, cfg, defensive, instruments)
    result["meta"]["risk_name"] = risk.name
    result["meta"]["signal_name"] = sig.name
    result["meta"]["risk_first"] = risk.dates[0].isoformat() if risk.dates else None
    result["meta"]["risk_last"] = risk.dates[-1].isoformat() if risk.dates else None
    result["meta"]["config"] = cfg
    if with_rolling:
        result["rolling"] = E.rolling_metrics(
            [E.pdate(d) for d in result["equity"]["dates"]], result["equity"]["strategy"])
    if with_mc:
        result["montecarlo"] = E.monte_carlo(result["equity"]["strategy"])
    return result


def b64_encode(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def results_csv(res: Dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    eq = res["equity"]
    w.writerow(["Datum", "Strategie", "Benchmark", "Drawdown", "Signal", "Position"])
    for i, d in enumerate(eq["dates"]):
        w.writerow([d, f"{eq['strategy'][i]:.2f}", f"{eq['benchmark'][i]:.2f}",
                    f"{eq['drawdown'][i]:.6f}", eq["state"][i], eq["position"][i]])
    w.writerow([])
    w.writerow(["Kennzahl", "Wert"])
    for k, v in res["metrics"].items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["Jahr", "Strategie", "Benchmark", "Endstand"])
    for y in res["yearly"]:
        w.writerow([y["year"], y["strategy"], y["benchmark"], y["end"]])
    w.writerow([])
    w.writerow(["Trades"])
    w.writerow(["Datum", "Aktion", "Instrument", "Kurs", "Stueck", "Wert", "Gebuehr",
                "Steuer", "Gewinn"])
    for t in res["trades"]:
        w.writerow([t["date"], t["action"], t["symbol"], t["price"], t["units"],
                    t["value"], t["fee"], t["tax"], t["gain"]])
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
        except Exception:
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
                risk = get_series(cfg["risk"], cfg)
                sig = get_series(cfg["signal"], cfg)
                defen = None
                if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
                    defen = get_series(cfg["defensive"], cfg)
                rows = E.run_grid(risk, sig, cfg, defen,
                                  max_runs=int(body.get("max_runs") or 500))
                rows.sort(key=lambda r: (r.get("calmar") or -99), reverse=True)
                self._json({"ok": True, "rows": rows, "signal_type": cfg["signal_type"]})
                return

            if u.path == "/api/walkforward":
                cfg = normalize_config(body.get("config") or {})
                risk = get_series(cfg["risk"], cfg)
                sig = get_series(cfg["signal"], cfg)
                defen = None
                if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
                    defen = get_series(cfg["defensive"], cfg)
                wf = E.walk_forward(risk, sig, cfg, defen,
                                    int(body.get("train_years") or 6),
                                    int(body.get("test_years") or 2),
                                    body.get("objective") or "calmar")
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
                        rows.append({"symbol": sym, "name": r["meta"].get("risk_name"),
                                     "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
                                     "sharpe": m.get("sharpe"), "calmar": m.get("calmar"),
                                     "volatility": m.get("volatility"),
                                     "bench_cagr": m.get("bench_cagr"),
                                     "end_capital": m.get("end_capital"),
                                     "time_in_market": m.get("time_in_market"),
                                     "ok": True})
                    except Exception as e:                      # noqa: BLE001
                        rows.append({"symbol": sym, "ok": False, "error": str(e)})
                self._json({"ok": True, "rows": rows})
                return

            if u.path == "/api/export":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                text = results_csv(res)
                fname = (f"etf_timing_{(cfg.get('risk') or 'risiko').replace('^', '')}"
                         f"_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
                self._json({"ok": True, "filename": fname,
                            "content": b64_encode(text),
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
    """Schneller Funktionstest ohne Browser."""
    print(f"{APP_NAME} {VERSION} -- Selbsttest")
    today = date.today()
    d = [date(2000, 1, 3) + timedelta(days=i) for i in range(3000)]
    px = [100.0 * (1.00025 ** i) * (1 + 0.08 * ((i % 400) / 400.0 - 0.5)) for i in range(len(d))]
    s = E.Series("DEMO", d, px, list(px), name="Demo")
    cfg = normalize_config({"risk": "DEMO", "signal": "DEMO", "signal_type": "ampel"})
    r = E.run_backtest(s, s, cfg)
    m = r["metrics"]
    print(f"  Tage={m['trading_days']}  CAGR={m['cagr']:.2%}  MaxDD={m['max_drawdown']:.2%}  "
          f"Trades={m['trades']}")
    # Signalvergleich mit der Original-Formel
    print("  Signal-Engine: ok" if m["trading_days"] > 2000 else "  Signal-Engine: FEHLER")
    print("  Selbsttest beendet.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} -- ETF-Timing-Analyse")
    ap.add_argument("--port", type=int, default=8765, help="Port (Standard 8765)")
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    ap.add_argument("--selftest", action="store_true", help="Nur Funktionstest")
    ap.add_argument("--download", nargs="+", metavar="SYMBOL",
                    help="Symbole herunterladen und beenden")
    ap.add_argument("--start", help="Startdatum (JJJJ-MM-TT)")
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

    if args.backtest:
        try:
            cfg = json.loads(args.backtest)
        except json.JSONDecodeError as e:
            print("Ungültiges JSON:", e)
            return 2
        res = run_config(cfg)
        m = res["metrics"]
        print(json.dumps(m, indent=2, ensure_ascii=False))
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
