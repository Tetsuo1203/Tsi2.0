"""End-to-End-Test der HTTP-API des Werkzeugs."""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    t0 = time.time()
    print("1) bootstrap")
    b = get("/api/bootstrap")
    assert b["ok"] and b["presets"], "bootstrap fehlerhaft"
    print(f"   {len(b['presets'])} Presets, {len(b['cached'])} Instrumente im Cache, "
          f"{len(b['signal_types'])} Signaltypen")

    print("2) Download (Yahoo)")
    d = post("/api/download", {"symbols": ["IWDA.AS", "^GSPC", "EXS1.DE", "^GDAXI"]})
    for r in d["results"]:
        print(f"   {'OK ' if r['ok'] else 'ERR'} {r['symbol']:10s} "
              f"{r.get('rows', 0):5d} Kurse {r.get('first', '')} .. {r.get('last', '')} "
              f"{r.get('error', '')}")
    assert any(r["ok"] for r in d["results"])

    print("3) Backtest (Preset 1)")
    cfg = b["presets"][0]["config"]
    r = post("/api/backtest", {"config": cfg})
    m = r["result"]["metrics"]
    print(f"   {m['start']} .. {m['end']}  Tage={m['trading_days']}  "
          f"CAGR={m['cagr']:.2%}  Bench={m['bench_cagr']:.2%}  "
          f"MaxDD={m['max_drawdown']:.2%}  Trades={m['trades']}")
    assert m["trading_days"] > 1000
    assert -1 < m["max_drawdown"] < 0

    print("4) Monte-Carlo")
    r2 = post("/api/backtest", {"config": cfg, "montecarlo": True})
    mc = r2["result"].get("montecarlo") or {}
    print(f"   Pfade={mc.get('n_paths')}  Median={mc.get('final', {}).get('p50')}")
    assert mc.get("p50")

    print("5) Parameter-Suche")
    t = time.time()
    g = post("/api/grid", {"config": cfg})
    rows = g["rows"]
    print(f"   {len(rows)} Varianten in {time.time() - t:.1f}s  "
          f"best: {rows[0]['params']} Calmar={rows[0]['calmar']}")
    assert len(rows) > 20

    print("6) Walk-Forward")
    t = time.time()
    w = post("/api/walkforward", {"config": cfg, "objective": "calmar",
                                  "train_years": 6, "test_years": 2})
    wf = w["walkforward"]
    print(f"   {len(wf['windows'])} Fenster in {time.time() - t:.1f}s  "
          f"OOS={'ja' if wf['oos'] else 'nein'}  {wf.get('note', '')}")
    assert wf["oos"] is not None

    print("7) Vergleich")
    c = post("/api/compare", {"config": cfg, "symbols": ["IWDA.AS", "EXS1.DE"]})
    for row in c["rows"]:
        print(f"   {row['symbol']:10s} CAGR={row.get('cagr')} DD={row.get('max_drawdown')}")

    print("8) Export")
    e = post("/api/export", {"config": cfg})
    import base64
    txt = base64.b64decode(e["content"]).decode("utf-8")
    print(f"   {e['filename']}  {len(txt):,} Zeichen, {txt.count(chr(10))} Zeilen")
    assert "Datum;Strategie" in txt

    print("9) Original-Nachbau (synthetischer Hebel)")
    orig = [p for p in b["presets"] if p["id"] == "original-comparison"][0]["config"]
    ro = post("/api/backtest", {"config": orig})
    mo = ro["result"]["metrics"]
    print(f"   {mo['start']} .. {mo['end']}  Endkapital={mo['end_capital']:,.2f}  "
          f"CAGR={mo['cagr']:.2%}  MaxDD={mo['max_drawdown']:.2%}  Trades={mo['trades']}")

    print("10) CSV-Import")
    csv_text = "Datum;Kurs\n" + "\n".join(
        f"{(i % 28) + 1:02d}.{(i // 28) % 12 + 1:02d}.20{10 + i // 336:02d};{100 + i * 0.1:.2f}"
        for i in range(400))
    imp = post("/api/import", {"name": "TestImport", "csv": csv_text})
    print(f"   {imp['symbol']} {imp['rows']} Kurse {imp['first']} .. {imp['last']}")
    assert imp["rows"] > 300

    print(f"\nAlle API-Tests erfolgreich ({time.time() - t0:.1f}s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                                    # noqa: BLE001
        print("FEHLER:", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)
