"""
ETF-Timing-Werkzeug -- Datenschicht: Yahoo-Download, CSV-Import, lokaler Cache.

Reine Standardbibliothek (urllib, json, csv, gzip). Kein API-Key nötig.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from engine import Series, fnum, make_series, pdate

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def http_json(url: str, timeout: int = 25) -> dict:
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                continue
            raise RuntimeError(last)
        except Exception as e:                                    # noqa: BLE001
            last = str(e)
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Netzwerkfehler: {last}")


def _yahoo(path: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    err = None
    for host in YAHOO_HOSTS:
        try:
            return http_json(f"https://{host}/{path}?{q}")
        except Exception as e:                                    # noqa: BLE001
            err = e
    raise RuntimeError(str(err))


_YF_TYPE = {"EQUITY": "Aktie", "ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fonds",
            "CURRENCY": "Währung", "CRYPTOCURRENCY": "Krypto", "FUTURE": "Future"}


def yahoo_search(query: str, limit: int = 12) -> List[Dict]:
    if not query.strip():
        return []
    j = _yahoo("v1/finance/search", {
        "q": query.strip(), "quotesCount": limit, "newsCount": 0, "listsCount": 0,
        "enableFuzzyQuery": "false", "quotesQueryId": "tss_match_phrase_query"})
    out = []
    for x in j.get("quotes", []):
        sym = x.get("symbol")
        if not sym:
            continue
        out.append({"symbol": sym,
                    "name": x.get("shortname") or x.get("longname") or sym,
                    "exchange": x.get("exchDisp") or x.get("exchange") or "",
                    "type": _YF_TYPE.get(x.get("quoteType", ""), x.get("quoteType", ""))})
    return out


def yahoo_series(symbol: str, start: Optional[date] = None, end: Optional[date] = None,
                 interval: str = "1d") -> Series:
    p1 = int((start or date(1950, 1, 1)).strftime("%s")) if False else int(
        (datetime.combine(start, datetime.min.time()) - datetime(1970, 1, 1)).days * 86400) if start else 0
    p2 = int((datetime.combine(end, datetime.min.time()) - datetime(1970, 1, 1)).days * 86400) \
        if end else int(time.time()) + 86400
    j = _yahoo("v8/finance/chart/" + urllib.parse.quote(symbol), {
        "period1": p1, "period2": p2, "interval": interval,
        "events": "div%2Csplit", "includeAdjustedClose": "true"})
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        err = (j.get("chart") or {}).get("error")
        raise RuntimeError(f"Keine Daten für {symbol}" + (f" ({err})" if err else ""))
    r = res[0]
    meta = r.get("meta") or {}
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    closes = q.get("close") or []
    adj = ((r.get("indicators", {}).get("adjclose") or [{}])[0] or {}).get("adjclose")
    if not adj:
        adj = closes
    rows = []
    for i, t in enumerate(ts):
        d = datetime.utcfromtimestamp(t).date()
        c = fnum(closes[i]) if i < len(closes) else None
        a = fnum(adj[i]) if i < len(adj) else None
        if c is None:
            continue
        rows.append((d, c, a))
    if not rows:
        raise RuntimeError(f"Keine gültigen Kurse für {symbol}")
    rows.sort(key=lambda x: x[0])
    series = Series(symbol=symbol, name=meta.get("longName") or meta.get("shortName") or symbol,
                    currency=meta.get("currency") or "",
                    source="yahoo", meta={"exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                                          "type": meta.get("instrumentType") or "",
                                          "timezone": meta.get("exchangeTimezoneName") or "",
                                          "fetched": datetime.now().isoformat(timespec="seconds")},
                    dates=[r[0] for r in rows], close=[r[1] for r in rows],
                    adj=[r[2] if r[2] is not None else r[1] for r in rows])
    return series


# --------------------------------------------------------------------------- #
# CSV-Import
# --------------------------------------------------------------------------- #

DATE_KEYS = ("datum", "date", "tag", "time", "zeit")
PRICE_KEYS = ("adj close", "adjclose", "adj. close", "adjusted close", "bereinigt",
              "schluss", "close", "kurs", "letzter", "last", "preis", "price", "nav", "wert")
SKIP_PRICE = ("open", "eröffnung", "eroeffnung", "high", "hoch", "low", "tief",
              "volume", "volumen", "stück", "stueck")


def _split(line: str, delim: str) -> List[str]:
    if delim == ";":
        return [c.strip() for c in line.split(";")]
    return [c.strip() for c in next(csv.reader([line], delimiter=delim))]


def _guess_delim(text: str) -> str:
    head = [l for l in text.splitlines()[:20] if l.strip()]
    best, best_n = ";", -1
    for d in (";", ",", "\t"):
        n = sum(l.count(d) for l in head)
        if n > best_n:
            best, best_n = d, n
    return best


def parse_csv(text: str, symbol: str = "", name: str = "") -> Series:
    """Flexibler CSV-Parser: erkennt Trennzeichen, Kopfzeilen, Datums- und Kurs-Spalte."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    text = text.replace("\ufeff", "")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("CSV ist leer.")
    delim = _guess_delim(text)

    date_col, price_col, header_idx = None, None, None
    for i, line in enumerate(lines[:40]):
        cells = _split(line, delim)
        low = [c.lower() for c in cells]
        d_i = next((k for k, c in enumerate(low) if any(c.startswith(t) or c == t for t in DATE_KEYS)), None)
        if d_i is None:
            continue
        p_i = None
        for k, c in enumerate(low):
            if k == d_i:
                continue
            if any(t in c for t in PRICE_KEYS) and not any(s in c for s in SKIP_PRICE):
                p_i = k
                break
        if p_i is None:
            for k, c in enumerate(low):
                if k == d_i:
                    continue
                if any(t in c for t in PRICE_KEYS):
                    p_i = k
                    break
        if p_i is not None:
            date_col, price_col, header_idx = d_i, p_i, i
            break

    rows: List[Tuple[date, float]] = []
    if date_col is not None:
        for line in lines[header_idx + 1:]:
            cells = _split(line, delim)
            if len(cells) <= max(date_col, price_col):
                continue
            d = pdate(cells[date_col])
            v = fnum(cells[price_col])
            if d and v and v > 0:
                rows.append((d, v))
    if len(rows) < 10:
        # Fallback: erste Spalte = Datum, erste sinnvolle Zahlspalte = Kurs
        rows = []
        for line in lines:
            cells = _split(line, delim)
            if len(cells) < 2:
                continue
            d = pdate(cells[0])
            if not d:
                continue
            for c in cells[1:]:
                v = fnum(c)
                if v and v > 0:
                    rows.append((d, v))
                    break
    if len(rows) < 10:
        raise ValueError("Konnte im CSV keine Kursreihe erkennen (Datum + Kurs erwartet).")
    sym = symbol or (name or "CSV").upper().replace(" ", "_")[:24]
    series = make_series(sym, rows, name=name or sym, currency="", source="csv",
                         meta={"fetched": datetime.now().isoformat(timespec="seconds")})
    return series


# --------------------------------------------------------------------------- #
# Synthetische Hebelreihe (fuer den Original-Vergleich)
# --------------------------------------------------------------------------- #

def synthetic_leverage(base: Series, factor: float, rf_pct: float = 0.0,
                       spread_pct: float = 0.0, name: str = "") -> Series:
    """Bildet eine gehebelte Reihe aus Tagesrenditen nach: r_L = L*r - |L-1|*Kosten.

    Entspricht der Logik aus 'db_x-trackers' (dort: Faktor 1 / 2 / -1 / -2 auf den DAX).
    """
    if not base.dates:
        raise ValueError("Basisserie ist leer.")
    cost_daily = (rf_pct + spread_pct) / 100.0 / 252.0
    vals = [100.0]
    for i in range(1, len(base)):
        r = base.value_at(i) / base.value_at(i - 1) - 1.0 if base.value_at(i - 1) else 0.0
        r_l = factor * r - abs(factor - 1.0) * cost_daily
        vals.append(vals[-1] * (1.0 + r_l))
    sym = f"{base.symbol}_{factor:+.0f}x".replace("+", "L")
    return Series(symbol=sym, name=name or f"{base.name or base.symbol} ({factor:+.0f}x synthetisch)",
                  currency=base.currency, source="synthetic",
                  meta={"base": base.symbol, "factor": factor, "rf_pct": rf_pct,
                        "spread_pct": spread_pct},
                  dates=list(base.dates), close=vals, adj=list(vals))


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _ensure_dirs() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", symbol)[:80] or "series"


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, _safe_name(symbol) + ".json")


def load_index() -> Dict:
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"instruments": {}}


def save_index(idx: Dict) -> None:
    _ensure_dirs()
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_FILE)


def save_series(s: Series) -> None:
    _ensure_dirs()
    path = _cache_path(s.symbol)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    idx = load_index()
    idx.setdefault("instruments", {})[s.symbol] = {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "source": s.source,
        "rows": len(s), "first": s.dates[0].isoformat() if s.dates else None,
        "last": s.dates[-1].isoformat() if s.dates else None,
        "meta": s.meta,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    save_index(idx)


def load_series(symbol: str) -> Optional[Series]:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Series.from_dict(json.load(f))
    except Exception:
        return None


def delete_series(symbol: str) -> bool:
    path = _cache_path(symbol)
    ok = False
    if os.path.exists(path):
        try:
            os.remove(path)
            ok = True
        except Exception:
            ok = False
    idx = load_index()
    if symbol in idx.get("instruments", {}):
        del idx["instruments"][symbol]
        save_index(idx)
        ok = True
    return ok


def list_cached() -> List[Dict]:
    idx = load_index().get("instruments", {})
    out = list(idx.values())
    out.sort(key=lambda x: (x.get("name") or x.get("symbol") or "").lower())
    return out


def get_or_download(symbol: str, start: Optional[date] = None,
                    end: Optional[date] = None, force: bool = False) -> Series:
    """Liefert die Reihe aus dem Cache oder laedt sie (und ergänzt fehlende Tage)."""
    cached = None if force else load_series(symbol)
    if cached is not None and len(cached) > 0:
        last = cached.dates[-1]
        need_more = (end is None and last < date.today() - timedelta(days=3)) or \
                    (end is not None and last < end)
        if not need_more:
            if start and cached.dates[0] > start:
                try:
                    fresh = yahoo_series(symbol, start=start, end=end)
                    merged = _merge_series(cached, fresh)
                    save_series(merged)
                    return merged
                except Exception:
                    return cached
            return cached
        try:
            fresh = yahoo_series(symbol, start=start or cached.dates[0], end=end)
            merged = _merge_series(cached, fresh)
            save_series(merged)
            return merged
        except Exception:
            return cached
    s = yahoo_series(symbol, start=start, end=end)
    save_series(s)
    return s


def _merge_series(a: Series, b: Series) -> Series:
    d: Dict[date, Tuple[float, Optional[float]]] = {}
    for i, dt in enumerate(a.dates):
        d[dt] = (a.close[i], a.adj[i] if a.adj else None)
    for i, dt in enumerate(b.dates):
        d[dt] = (b.close[i], b.adj[i] if b.adj else None)
    dates = sorted(d)
    return Series(symbol=b.symbol or a.symbol, name=b.name or a.name,
                  currency=b.currency or a.currency, source=b.source or a.source,
                  meta={**(a.meta or {}), **(b.meta or {})},
                  dates=dates, close=[d[x][0] for x in dates],
                  adj=[d[x][1] for x in dates])


# --------------------------------------------------------------------------- #
# Kuratierte Instrumente (Vorschlagsliste im UI)
# --------------------------------------------------------------------------- #

PRESET_INSTRUMENTS = [
    {"symbol": "IWDA.AS", "name": "iShares Core MSCI World (Acc, EUR)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "EUNL.DE", "name": "iShares Core MSCI World (Acc, Xetra)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "VWCE.DE", "name": "Vanguard FTSE All-World (Acc)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "EXS1.DE", "name": "iShares Core DAX (DE)", "group": "Deutschland", "type": "ETF"},
    {"symbol": "SXR8.DE", "name": "iShares Core S&P 500 (Acc, EUR)", "group": "USA", "type": "ETF"},
    {"symbol": "VUSA.L", "name": "Vanguard S&P 500 (Dist, GBP)", "group": "USA", "type": "ETF"},
    {"symbol": "EUNL.AS", "name": "iShares Core MSCI Europe", "group": "Europa", "type": "ETF"},
    {"symbol": "XMME.DE", "name": "Xtrackers MSCI Emerging Markets", "group": "Schwellenländer", "type": "ETF"},
    {"symbol": "IUAG.L", "name": "iShares Global Aggregate Bond", "group": "Anleihen", "type": "ETF"},
    {"symbol": "4GLD.DE", "name": "Xetra-Gold (EUR)", "group": "Gold", "type": "ETF"},
    {"symbol": "^GDAXI", "name": "DAX Index", "group": "Index", "type": "Index"},
    {"symbol": "^MDAXI", "name": "MDAX Index", "group": "Index", "type": "Index"},
    {"symbol": "^GSPC", "name": "S&P 500 Index", "group": "Index", "type": "Index"},
    {"symbol": "^NDX", "name": "Nasdaq 100 Index", "group": "Index", "type": "Index"},
    {"symbol": "^STOXX50E", "name": "EURO STOXX 50 Index", "group": "Index", "type": "Index"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "group": "Index", "type": "Index"},
    {"symbol": "URTH", "name": "iShares MSCI World ETF (USD)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust (USD)", "group": "USA", "type": "ETF"},
]
