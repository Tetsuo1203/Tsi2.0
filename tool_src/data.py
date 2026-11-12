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

from engine import Series, fnum, make_series, pdate, quality_report

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
    epoch = date(1970, 1, 1)
    p1 = (start - epoch).days * 86400 if start else 0
    p2 = ((end - epoch).days + 1) * 86400 if end else int(time.time()) + 86400
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
    # Zeitstempel = Handelsbeginn in UTC; mit dem Börsen-Offset ergibt sich der lokale
    # Handelstag (wichtig z. B. für Asien/Australien). Doppelte Tage: letzter Wert gilt.
    off = int(meta.get("gmtoffset") or 0)
    by_day: Dict[date, Tuple[float, Optional[float]]] = {}
    for i, t in enumerate(ts):
        if t is None:
            continue
        d = (datetime(1970, 1, 1) + timedelta(seconds=int(t) + off)).date()
        c = fnum(closes[i]) if i < len(closes) else None
        a = fnum(adj[i]) if i < len(adj) else None
        if c is None or c <= 0:
            continue
        by_day[d] = (c, a if a is not None and a > 0 else None)
    if not by_day:
        raise RuntimeError(f"Keine gültigen Kurse für {symbol}")
    days = sorted(by_day)
    series = Series(symbol=symbol, name=meta.get("longName") or meta.get("shortName") or symbol,
                    currency=meta.get("currency") or "",
                    source="yahoo", meta={"exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                                          "type": meta.get("instrumentType") or "",
                                          "timezone": meta.get("exchangeTimezoneName") or "",
                                          "fetched": datetime.now().isoformat(timespec="seconds")},
                    dates=days, close=[by_day[d][0] for d in days],
                    adj=[by_day[d][1] if by_day[d][1] is not None else by_day[d][0] for d in days])
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


def _detect_dayfirst(samples: List[str]) -> Optional[bool]:
    """TT/MM vs. MM/TT einer Datumsspalte anhand der Werte > 12 (None = nicht entscheidbar)."""
    dmy = mdy = 0
    for raw in samples:
        t = (raw or "").strip().strip('"').strip("'").split("T")[0].split(" ")[0]
        m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", t)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if dmy > mdy:
        return True
    if mdy > dmy:
        return False
    return None


def _detect_decimal(samples: List[str]) -> Optional[str]:
    """Dezimaltrennzeichen einer Zahlenspalte ("," oder "."; None = je Wert entscheiden).

    Eindeutig sind Werte mit beiden Zeichen oder mit 1-2/4+ Nachkommastellen; "1.234" allein
    ist mehrdeutig und zählt nicht.
    """
    comma = dot = 0
    for raw in samples:
        t = re.sub(r"[^\d,.\-]", "", raw or "")
        if not t:
            continue
        lc, ld = t.rfind(","), t.rfind(".")
        if lc > ld and (ld >= 0 or len(t) - lc - 1 != 3):
            comma += 1
        elif ld > lc and (lc >= 0 or len(t) - ld - 1 != 3):
            dot += 1
    if comma > dot:
        return ","
    if dot > comma:
        return "."
    return None


def _parse_pairs(raw: List[Tuple[str, str]]):
    """Datum/Kurs-Paare einheitlich je Spalte auswerten (kein Mischen von TT/MM und MM/TT)."""
    dayfirst = _detect_dayfirst([d for d, _ in raw])
    dec = _detect_decimal([p for _, p in raw])
    rows: List[Tuple[date, float]] = []
    for ds, ps in raw:
        d = pdate(ds, dayfirst=dayfirst)
        v = fnum(ps, decimal=dec)
        if d and v and v > 0:
            rows.append((d, v))
    return rows, dayfirst, dec


def parse_csv(text: str, symbol: str = "", name: str = "") -> Series:
    """Flexibler CSV-Parser: erkennt Trennzeichen, Kopfzeilen, Datums- und Kurs-Spalte,
    Datumsformat (TT/MM oder MM/TT) und Dezimaltrennzeichen je Datei."""
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
    dayfirst, dec = None, None
    if date_col is not None:
        raw = []
        for line in lines[header_idx + 1:]:
            cells = _split(line, delim)
            if len(cells) > max(date_col, price_col):
                raw.append((cells[date_col], cells[price_col]))
        rows, dayfirst, dec = _parse_pairs(raw)
    if len(rows) < 10:
        # Fallback: erste Spalte = Datum, erste sinnvolle Zahlspalte = Kurs
        raw = []
        for line in lines:
            cells = _split(line, delim)
            if len(cells) < 2 or not pdate(cells[0]):
                continue
            for c in cells[1:]:
                v = fnum(c)
                if v and v > 0:
                    raw.append((cells[0], c))
                    break
        rows, dayfirst, dec = _parse_pairs(raw)
    if len(rows) < 10:
        raise ValueError("Konnte im CSV keine Kursreihe erkennen (Datum + Kurs erwartet).")
    sym = symbol or (name or "CSV").upper().replace(" ", "_")[:24]
    fmt = {True: "TT/MM/JJJJ", False: "MM/TT/JJJJ", None: "eindeutig"}[dayfirst]
    series = make_series(sym, rows, name=name or sym, currency="", source="csv",
                         meta={"fetched": datetime.now().isoformat(timespec="seconds"),
                               "csv_date_order": fmt, "csv_decimal": dec or "auto"})
    return series


# --------------------------------------------------------------------------- #
# Synthetische Hebelreihe (fuer den Original-Vergleich)
# --------------------------------------------------------------------------- #

def synthetic_leverage(base: Series, factor: float, rf_pct: float = 0.0,
                       spread_pct: float = 0.0, name: str = "") -> Series:
    """Bildet eine täglich gehebelte Long-/Short-Reihe aus Tagesrenditen nach:

        r_L = L·r + (1 − L)·rf·Δt − |L|·Kosten·Δt        (Δt in Kalenderjahren)

    Long-Hebel (L > 1) zahlt Finanzierung auf den geliehenen Teil (L − 1); Short-Produkte
    (L < 0) erhalten Zinsen auf Sicherheit und Leerverkaufserlös (1 − L). Laufende Kosten
    (Swap/TER) skalieren mit dem Exposure |L|. Ohne Zins/Kosten entspricht das der Logik
    aus 'db_x-trackers' (Faktor 1 / 2 / -1 / -2 auf den DAX).
    """
    if not base.dates:
        raise ValueError("Basisserie ist leer.")
    rf, cost = rf_pct / 100.0, spread_pct / 100.0
    vals = [100.0]
    for i in range(1, len(base)):
        prev = base.value_at(i - 1)
        r = base.value_at(i) / prev - 1.0 if prev else 0.0
        dt = (base.dates[i] - base.dates[i - 1]).days / 365.0
        r_l = factor * r + (1.0 - factor) * rf * dt - abs(factor) * cost * dt
        vals.append(max(vals[-1] * (1.0 + r_l), 1e-6))
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
    try:
        q = quality_report(s, check_age=False)
        qsum = {"level": q["level"], "n_warn": q["n_warn"], "n_info": q["n_info"]}
    except Exception:                                           # noqa: BLE001
        qsum = None
    idx = load_index()
    idx.setdefault("instruments", {})[s.symbol] = {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "source": s.source,
        "rows": len(s), "first": s.dates[0].isoformat() if s.dates else None,
        "last": s.dates[-1].isoformat() if s.dates else None,
        "meta": s.meta, "quality": qsum,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    save_index(idx)


def update_quality(symbol: str, rep: Dict) -> None:
    """Qualitäts-Kurzinfo im Verzeichnis nach einer manuellen Prüfung aktualisieren."""
    idx = load_index()
    entry = idx.get("instruments", {}).get(symbol)
    if entry is None:
        return
    entry["quality"] = {"level": rep.get("level"), "n_warn": rep.get("n_warn", 0),
                        "n_info": rep.get("n_info", 0)}
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
    """Verzeichnis der geladenen Reihen; fehlende Qualitätsinfos (Bestände aus Version 1.0)
    werden einmalig nachgetragen."""
    idx = load_index()
    inst = idx.get("instruments", {})
    changed = False
    for sym, entry in inst.items():
        if "quality" in entry:
            continue
        s = load_series(sym)
        try:
            q = quality_report(s, check_age=False) if s is not None and len(s) else None
            entry["quality"] = ({"level": q["level"], "n_warn": q["n_warn"], "n_info": q["n_info"]}
                                if q else None)
        except Exception:                                       # noqa: BLE001
            entry["quality"] = None
        changed = True
    if changed:
        try:
            save_index(idx)
        except Exception:                                       # noqa: BLE001
            pass
    out = list(inst.values())
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
    {"symbol": "^SP500TR", "name": "S&P 500 Total Return (mit Dividenden, USD, ab 1988)", "group": "Index", "type": "Index"},
    {"symbol": "URTH", "name": "iShares MSCI World ETF (USD)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust (USD)", "group": "USA", "type": "ETF"},
    {"symbol": "BTC-EUR", "name": "Bitcoin in Euro (ab 2014, 7 Tage/Woche)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "BTC-USD", "name": "Bitcoin in US-Dollar (ab 2014)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "ETH-EUR", "name": "Ethereum in Euro (ab 2017)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "BTCE.DE", "name": "Bitwise Physical Bitcoin ETC (Xetra, EUR, ab 2020)", "group": "Krypto", "type": "ETC"},
    {"symbol": "VBTC.DE", "name": "VanEck Bitcoin ETN (Xetra, EUR, ab 2020)", "group": "Krypto", "type": "ETN"},
    {"symbol": "IBIT", "name": "iShares Bitcoin Trust ETF (USD, ab 2024)", "group": "Krypto", "type": "ETF"},
]


# --------------------------------------------------------------------------- #
# Selbsttest der Datenschicht (ohne Internet)
# --------------------------------------------------------------------------- #

def data_self_checks() -> List[Dict]:
    res: List[Dict] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        res.append({"name": name, "ok": bool(cond), "detail": detail})

    try:
        d0 = date(2015, 1, 1)
        days = [d0 + timedelta(days=i) for i in range(60)]
        s1 = parse_csv("\n".join(["Datum;Schluss"] + [f"{d.strftime('%d/%m/%Y')};{100 + i},50"
                                                      for i, d in enumerate(days)]), symbol="A")
        s2 = parse_csv("\n".join(["Date,Close"] + [f"{d.strftime('%m/%d/%Y')},{1000 + i}.25"
                                                   for i, d in enumerate(days)]), symbol="B")
        s3 = parse_csv("\n".join(["Datum;Kurs"] + [f"{d.strftime('%d.%m.%Y')};"
                                                   f"{'1.234' if i % 2 else '1.234,50'}"
                                                   for i, d in enumerate(days[:30])]), symbol="C")
        ok = (s1.dates == days and abs(s1.close[0] - 100.5) < 1e-9 and s2.dates == days
              and abs(s2.close[0] - 1000.25) < 1e-9 and all(1233.9 < v < 1234.6 for v in s3.close))
        check("CSV: TT/MM/JJJJ, MM/TT/JJJJ und Tausenderpunkte je Datei korrekt", ok,
              f"{s1.dates[0]} … {s1.dates[-1]} / {s2.dates[0]} / {s3.close[:2]}")
    except Exception as e:                                      # noqa: BLE001
        check("CSV-Import", False, f"Ausnahme {type(e).__name__}: {e}")
    try:
        ds = [date(2020, 1, 1) + timedelta(days=i) for i in range(366)]
        flat = Series("B", ds, [100.0] * 366, [100.0] * 366)
        g_short = synthetic_leverage(flat, -1.0, rf_pct=2.0, spread_pct=0.3).close[-1] / 100.0 - 1.0
        g_long = synthetic_leverage(flat, 2.0, rf_pct=2.0, spread_pct=0.3).close[-1] / 100.0 - 1.0
        check("Synthetischer Hebel: Short erhält Zinsen, Long zahlt Finanzierung",
              0.036 < g_short < 0.039 and -0.027 < g_long < -0.025,
              f"-1x: {g_short * 100:+.2f} % p. a., 2x: {g_long * 100:+.2f} % p. a. (Index unverändert)")
    except Exception as e:                                      # noqa: BLE001
        check("Synthetischer Hebel", False, f"Ausnahme {type(e).__name__}: {e}")
    return res
