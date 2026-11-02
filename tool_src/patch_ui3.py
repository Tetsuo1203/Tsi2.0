"""Dritte Runde UI-Feinschliff: Mehrfachauswahl und Tabellenzellen."""
import os

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
            raise SystemExit(f"{path}: {s.count(old)}x für {old[:60]!r}")
        s = s.replace(old, new)
        changed += 1
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print(f"{path}: {changed} Änderung(en)")


CSS = [
    ("tbody td{padding:7px 10px;border-bottom:1px solid #16233a;text-align:right;font-variant-numeric:tabular-nums}",
     "tbody td{padding:7px 10px;border-bottom:1px solid #16233a;text-align:right;\n"
     "  font-variant-numeric:tabular-nums;white-space:nowrap}"),
    (".inp.multi{height:auto;margin-top:8px}",
     ".inp.multi{height:auto;margin-top:8px;padding:6px;background:#0a1524}\n"
     "select[multiple] option{background:#0b1524;color:var(--txt);padding:3px 6px}\n"
     "select[multiple] option:checked{background:#12405a;color:#e8f7ff}"),
]

HTML = [
    ('<select id="compareSel" class="inp multi" multiple size="8"></select>',
     '<select id="compareSel" class="inp multi" multiple size="5"></select>'),
]

if __name__ == "__main__":
    patch("tool_src/ui/style.css", CSS)
    patch("tool_src/ui/index.html", HTML)
