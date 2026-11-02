"""GitHub-Benutzerdaten für die Push-Vorbereitung ermitteln."""
import json
import urllib.request

USER = "Tetsuo1203"
REPO = "Tsi2.0"


def get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "etf-tool-push-prep",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.load(r)
    except Exception as e:                                      # noqa: BLE001
        return None, str(e)


def main():
    st, d = get(f"https://api.github.com/users/{USER}")
    if not isinstance(d, dict):
        print("Fehler:", st, d)
        return
    print("login   :", d["login"])
    print("id      :", d["id"])
    print("name    :", d.get("name"))
    print("noreply :", f"{d['id']}+{d['login']}@users.noreply.github.com")
    print("public  :", d["public_repos"])
    st2, repo = get(f"https://api.github.com/repos/{USER}/{REPO}")
    print("repo    :", st2, (repo or {}).get("full_name") if isinstance(repo, dict) else repo)


if __name__ == "__main__":
    main()
