"""Markdown-Sanity-Check: Codezäune, Tabellen, Bilder, Verweise."""
import os
import re

BT = chr(96) * 3
FENCE = re.compile("^" + BT + r"[A-Za-z0-9]*$")


def check(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    fences = [(i + 1, l.strip()) for i, l in enumerate(lines) if FENCE.match(l.strip())]
    inside, blocks, cur = False, [], []
    for i, l in enumerate(lines):
        if FENCE.match(l.strip()):
            inside = not inside
            continue
        if not inside and l.strip().startswith("|"):
            cur.append((i + 1, l.replace("\\|", "")))
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)
    bad = [b for b in blocks if len({r.count("|") for _, r in b}) > 1]
    words = len(re.findall(r"\w+", "\n".join(lines)))
    print(f"{path}: {len(lines)} Zeilen · {words} Wörter · {len(fences)} Codezäune "
          f"({'gerade' if len(fences) % 2 == 0 else 'UNGERADE!'}) · "
          f"{len(blocks)} Tabellen · {len(bad)} fehlerhaft")
    for b in bad:
        print("   Tabelle ab Zeile", b[0][0], "Spaltenzahlen:", sorted({r.count("|") for _, r in b}))
    return len(fences) % 2 == 0 and not bad


def main():
    ok = True
    for p in ("Anleitung.md", "README_ETF_Tool.md",
              "Analyse_ETF_Simulation_Original.md"):
        ok &= check(p)
    t = open("Anleitung.md", encoding="utf-8").read()
    imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", t)
    missing = [m for m in imgs if not os.path.exists(m)]
    print("Bilder:", len(imgs), "fehlend:", missing)
    heads = sorted(set(re.findall(r"^## (\d+)\.", t, flags=re.M)))
    refs = sorted(set(re.findall(r"Abschnitt (\d+)", t)))
    print("Abschnittsverweise:", refs, "→ gültig:", all(r in heads for r in refs))
    print("ERGEBNIS:", "ok" if ok and not missing else "prüfen")


if __name__ == "__main__":
    main()
