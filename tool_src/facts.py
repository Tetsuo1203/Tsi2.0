"""Fakten für die Anleitung: Presets, Defaults, Signaltypen."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import ETF_Tool as T


def main():
    print("Presets:")
    for p in T.PRESETS:
        c = p["config"]
        print(f"  {p['id']:22s} {p['name']}")
        print(f"      risk={c['risk']:12s} signal={c['signal']:10s} "
              f"typ={c['signal_type']:9s} rotation={'ja' if c.get('rotation') else 'nein'} "
              f"lag={c.get('exec_lag')}")
    print("\nDefaults:")
    for k, v in T.DEFAULTS.items():
        if k != "params":
            print(f"  {k:20s} {v!r}")
    print("\nParams:", T.DEFAULTS["params"])
    print("\nSignaltypen:")
    for k, v in T.E.SIGNAL_INFO.items():
        print(f"  {k:10s} {v}")


if __name__ == "__main__":
    main()
