"""For every banned way find who/when added the restriction tag and the changeset comment.

Result is cached in data/ban_sources.json (committed, keyed by way id) so the
build can show a confidence note in the popup. Only ways missing from the cache
are fetched, so re-runs are cheap.
"""
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "ban_sources.json"
API = "https://api.openstreetmap.org/api/0.6/"
RESTRICT = {"motorcycle": ("no", "private"), "access": ("no", "private"), "motor_vehicle": ("no", "private")}


def get(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "MapMot/1.0 (tashkent moto map)"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def is_restricted(tags):
    if tags.get("motorcycle:conditional"):
        return True
    return any(tags.get(k) in v for k, v in RESTRICT.items())


def main():
    force = "--force" in sys.argv
    cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() and not force else {}
    changesets = {}
    ways = json.loads((RAW / "banned.json").read_text(encoding="utf-8"))["elements"]
    todo = [w for w in ways if str(w["id"]) not in cache]
    print(f"{len(ways)} banned ways, {len(todo)} to fetch")
    for i, w in enumerate(todo, 1):
        wid = str(w["id"])
        try:
            hist = ET.fromstring(get(f"way/{wid}/history"))
            first = None
            for v in hist.findall("way"):
                tags = {t.get("k"): t.get("v") for t in v.findall("tag")}
                if is_restricted(tags):
                    first = v
                    break
            if first is None:
                cache[wid] = {}
                continue
            csid = first.get("changeset")
            if csid not in changesets:
                c = ET.fromstring(get(f"changeset/{csid}")).find("changeset")
                ct = {t.get("k"): t.get("v") for t in c.findall("tag")}
                changesets[csid] = {"comment": ct.get("comment", "")[:200], "source": ct.get("source", "")[:80]}
            cache[wid] = {"user": first.get("user"), "date": first.get("timestamp")[:10], "changeset": csid,
                          **changesets[csid]}
        except Exception as e:  # noqa: BLE001
            print(f"  way {wid}: {e}", file=sys.stderr)
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}")
            OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.2)
    OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {len(cache)} entries to {OUT.name}")


if __name__ == "__main__":
    main()
