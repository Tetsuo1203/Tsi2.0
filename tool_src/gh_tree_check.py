"""Vergleicht den Remote-Baum mit dem lokalen HEAD (Token aus GH_TOKEN)."""
import json
import os
import subprocess
import sys
import urllib.request

T = os.environ.get("GH_TOKEN", "")
OWNER, REPO = "Tetsuo1203", "Tsi2.0"


def api(path):
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + T,
        "User-Agent": "etf-tree-check",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def main():
    ref = api(f"/repos/{OWNER}/{REPO}/git/ref/heads/main")
    sha = ref["object"]["sha"]
    print("Remote main SHA:", sha)

    commits = api(f"/repos/{OWNER}/{REPO}/commits?per_page=10")
    print(f"Commits im Remote: {len(commits)}")
    for c in commits:
        a = c["commit"]["author"]
        login = (c.get("author") or {}).get("login") or "-"
        print(f"  {c['sha'][:7]}  {a['name']} <{a['email']}>  Konto: {login}  "
              f"{c['commit']['message'].splitlines()[0][:60]}")

    tree = api(f"/repos/{OWNER}/{REPO}/git/trees/{sha}?recursive=1")
    remote = sorted(t["path"] for t in tree.get("tree", []) if t["type"] == "blob")
    print(f"Dateien im Remote-Baum: {len(remote)}  (truncated: {tree.get('truncated')})")

    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                         capture_output=True, text=True, encoding="utf-8")
    local = sorted(x for x in out.stdout.splitlines() if x)
    print(f"Dateien lokal (HEAD):   {len(local)}")

    only_remote = sorted(set(remote) - set(local))
    only_local = sorted(set(local) - set(remote))
    print("nur im Remote:", only_remote[:10] or "keine")
    print("nur lokal    :", only_local[:10] or "keine")
    print("identisch    :", not only_remote and not only_local)
    return 0 if not only_local else 1


if __name__ == "__main__":
    sys.exit(main())
