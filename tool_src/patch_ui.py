"""Wendet kleine UI-/Server-Anpassungen idempotent an."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(path, pairs):
    full = os.path.join(ROOT, path)
    with open(full, "r", encoding="utf-8") as f:
        s = f.read()
    changed = 0
    for old, new in pairs:
        if new in s and old not in s:
            continue
        if s.count(old) != 1:
            raise SystemExit(f"{path}: Muster {s.count(old)}x gefunden: {old[:70]!r}")
        s = s.replace(old, new)
        changed += 1
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print(f"{path}: {changed} Änderung(en)")


APP = [
    (
        "  function showTab(name) {\n    S.tab = name;",
        "  function showTab(name) {\n"
        "    if (!name || !document.getElementById('view-' + name)) name = 'overview';\n"
        "    S.tab = name;\n"
        "    if (history.replaceState) history.replaceState(null, '', '#' + name);",
    ),
    (
        '      wireEvents();\n      showTab("overview");',
        '      wireEvents();\n      showTab((location.hash || "").replace("#", "") || "overview");',
    ),
    (
        '    window.addEventListener("resize", () => {',
        '    window.addEventListener("hashchange", () => {\n'
        '      showTab((location.hash || "").replace("#", "") || "overview");\n'
        '    });\n'
        '    window.addEventListener("resize", () => {',
    ),
    (
        '      kpi("Kosten", eur(m.fees + m.taxes), `${eur(m.fees)} Gebühr · ${eur(m.taxes)} Steuer`, "neutral"),',
        '      kpi("Kosten & Steuer", eur(m.fees + m.taxes),\n'
        '        `${eur(m.fees)} Gebühr · ${eur(m.taxes)} Steuer (anteilig)`, "neutral"),',
    ),
    (
        '    Charts.heatmap($("#chartGrid"), {\n'
        '      years: ys, months: xs.map(x => num(x, 2)), values,\n'
        '    });',
        '    Charts.heatmap($("#chartGrid"), {\n'
        '      years: ys, months: xs.map(x => num(x, 2)), values,\n'
        '      rowLabel: "RSL-Tage", fmt: "num",\n'
        '    });',
    ),
]

SERVER = [
    (
        '    {\n        "id": "original-comparison",',
        '''    {
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
        "id": "original-comparison",''',
    ),
]

if __name__ == "__main__":
    patch("tool_src/ui/app.js", APP)
    patch("tool_src/server.py", SERVER)
