"""Prüft Token und Repository-Zustand über die GitHub-API (Token nur aus der Umgebung)."""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ.get("GH_TOKEN", "")
OWNER = "Tetsuo1203"
REPO = "Tsi2.0"


def api(path, method="GET"):
    url = "https://api.github.com" + path
    req = urllib.request.Request(url, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + TOKEN,
        "User-Agent": "etf-tool-push",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = None
        try:
            body = json.load(e)
        except Exception:                                       # noqa: BLE001
            pass
        return e.code, body, dict(e.headers)
    except Exception as e:                                      # noqa: BLE001
        return None, str(e), {}


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "check"
    if not TOKEN:
        print("KEIN TOKEN in GH_TOKEN")
        return 2

    if phase == "pre":
        st, user, hdrs = api("/user")
        if st != 200 or not isinstance(user, dict):
            print(f"TOKEN UNGUELTIG (HTTP {st}): {user}")
            return 1
        scopes = hdrs.get("X-OAuth-Scopes") or hdrs.get("x-oauth-scopes") or ""
        print(f"Token ok fuer '{user.get('login')}' (id {user.get('id')})")
        print(f"Scopes: {scopes or '(keine angegeben)'}")
        st2, repo, _ = api(f"/repos/{OWNER}/{REPO}")
        if st2 == 200 and isinstance(repo, dict):
            print(f"Repo vorhanden: {repo['full_name']} | privat: {repo['private']} | "
                  f"default: {repo.get('default_branch')} | Groesse: {repo.get('size')} KB")
            st3, branch, _ = api(f"/repos/{OWNER}/{REPO}/branches/main")
            if st3 == 200 and isinstance(branch, dict):
                print(f"Branch main existiert bereits, letzter Commit: {branch['commit']['sha'][:7]}")
            else:
                print("Branch main ist noch leer")
        elif st2 == 404:
            print("Repo NICHT gefunden – bitte unter github.com/new anlegen (Name Tsi2.0)")
            return 3
        else:
            print(f"Repo-Abfrage HTTP {st2}: {repo}")
            return 3
        return 0

    # phase == "post"
    st, repo, _ = api(f"/repos/{OWNER}/{REPO}")
    if st != 200 or not isinstance(repo, dict):
        print(f"Repo-Abfrage fehlgeschlagen: HTTP {st}")
        return 1
    print(f"Repo: {repo['full_name']} | privat: {repo['private']} | "
          f"default: {repo.get('default_branch')} | Groesse: {repo.get('size')} KB")
    st2, commit, _ = api(f"/repos/{OWNER}/{REPO}/commits?per_page=1")
    if st2 == 200 and isinstance(commit, list) and commit:
        c = commit[0]
        print(f"Letzter Commit: {c['sha'][:7]} – {c['commit']['message'].splitlines()[0]}")
        print(f"Autor: {c['commit']['author']['name']} <{c['commit']['author']['email']}>")
        if c.get("author"):
            print(f"GitHub-Konto zugeordnet: {c['author']['login']}")
        else:
            print("GitHub-Konto: NICHT zugeordnet")
    st3, tree, _ = api(f"/repos/{OWNER}/{REPO}/contents/")
    if st3 == 200 and isinstance(tree, list):
        names = sorted(x["name"] for x in tree)
        print(f"Dateien im Wurzelverzeichnis ({len(names)}): {', '.join(names)}")
    st4, langs, _ = api(f"/repos/{OWNER}/{REPO}/languages")
    if st4 == 200 and isinstance(langs, dict):
        print("Sprachen:", ", ".join(f"{k} {v}B" for k, v in langs.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
