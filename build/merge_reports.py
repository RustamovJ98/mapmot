"""Merge sign reports sent from the app as a GitHub issue into data/marks.json and docs/marks.json.

Runs in .github/workflows/sign-reports.yml. The issue body is untrusted: only the JSON block after the
<!-- mapmot-reports --> marker is read, and every field is validated and normalised. Outputs for the
workflow: added=<count>, summary=<comment text that contains no user-provided text>.

Local test: ISSUE_BODY=... MARKS_FILE=/tmp/m.json DOCS_MARKS_FILE=/tmp/d.json python build/merge_reports.py
"""
import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKS = Path(os.environ.get("MARKS_FILE", ROOT / "data" / "marks.json"))
DOCS_MARKS = Path(os.environ.get("DOCS_MARKS_FILE", ROOT / "docs" / "marks.json"))
SOUTH, WEST, NORTH, EAST = 41.05, 68.95, 41.55, 69.60  # Tashkent and surroundings
MAX_PER_ISSUE = 50
PAYLOAD_RE = re.compile(r"<!--\s*mapmot-reports\s*-->\s*```(?:json)?\s*(.+?)\s*```", re.S)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
DATE_RE = re.compile(r"^20\d\d-[01]\d-[0-3]\d$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean(v, limit):
    if not isinstance(v, str):
        return ""
    v = "".join(ch for ch in v if ch.isprintable())
    return re.sub(r"\s+", " ", v).strip()[:limit]


def validate(m, author):
    if not isinstance(m, dict) or m.get("type") not in ("sign", "nosign"):
        return None
    lat, lon = m.get("lat"), m.get("lon")
    if not (number(lat) and number(lon) and SOUTH <= lat <= NORTH and WEST <= lon <= EAST):
        return None
    if not (isinstance(m.get("id"), str) and ID_RE.match(m["id"])):
        return None
    if not (isinstance(m.get("date"), str) and DATE_RE.match(m["date"])):
        return None
    out = {"id": m["id"], "type": m["type"], "lat": round(lat, 5), "lon": round(lon, 5), "date": m["date"]}
    for key in ("way", "osmWay"):
        value = m.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**12:
            out[key] = value
    for key, limit in (("street", 120), ("text", 200)):
        value = clean(m.get(key), limit)
        if value:
            out[key] = value
    if number(m.get("ts")) and 1.5e12 < m["ts"] < 4e12:
        out["ts"] = int(m["ts"])
    if author and LOGIN_RE.match(author):
        out["by"] = author
    return out


def way_geometry(way_id):
    """Geometry of the OSM way a sign was reported on (best effort), so the map can draw that street piece."""
    query = urllib.parse.urlencode({"data": f"[out:json][timeout:25];way({way_id});out geom;"}).encode()
    for endpoint in OVERPASS:
        try:
            req = urllib.request.Request(endpoint, data=query, headers={"User-Agent": "MapMot/1.0 (sign reports bot)"})
            elements = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("elements", [])
            if elements and elements[0].get("geometry"):
                return [[round(p["lat"], 5), round(p["lon"], 5)] for p in elements[0]["geometry"][:500]]
        except Exception as e:  # noqa: BLE001
            print("  overpass", endpoint, e)
    return None


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    delimiter = "EOF_" + os.urandom(8).hex()
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main():
    body = os.environ.get("ISSUE_BODY", "")
    author = os.environ.get("ISSUE_AUTHOR", "")
    found = PAYLOAD_RE.search(body)
    try:
        payload = json.loads(found.group(1)) if found else None
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, list) or not payload:
        set_output("added", "0")
        set_output("summary", "Не нашёл в заявке данных отметок. Отправьте их кнопкой «Знаки в общую карту» в приложении.")
        return

    marks = json.loads(MARKS.read_text(encoding="utf-8")) if MARKS.exists() else []
    index = {m.get("id"): i for i, m in enumerate(marks)}
    added = updated = rejected = 0
    kinds = {"sign": 0, "nosign": 0}
    for raw in payload[:MAX_PER_ISSUE]:
        m = validate(raw, author)
        if not m:
            rejected += 1
            continue
        if m["id"] in index:  # the same report sent again: keep the newer version
            old = marks[index[m["id"]]]
            if m.get("ts", 0) > old.get("ts", 0):
                if old.get("geom") and old.get("osmWay") == m.get("osmWay"):
                    m["geom"] = old["geom"]
                marks[index[m["id"]]] = m
                updated += 1
            continue
        if m["type"] == "sign" and m.get("osmWay") and not m.get("way"):
            geom = way_geometry(m["osmWay"])
            if geom and len(geom) > 1:
                m["geom"] = geom
        index[m["id"]] = len(marks)
        marks.append(m)
        kinds[m["type"]] += 1
        added += 1
    rejected += max(0, len(payload) - MAX_PER_ISSUE)

    if added or updated:
        MARKS.write_text(json.dumps(marks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        DOCS_MARKS.parent.mkdir(parents=True, exist_ok=True)
        DOCS_MARKS.write_text(json.dumps(marks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    parts = []
    if added:
        parts.append(f"Добавлено в общую карту: {added} (знак есть: {kinds['sign']}, знака нет: {kinds['nosign']}).")
    if updated:
        parts.append(f"Обновлено: {updated}.")
    if not added and not updated:
        parts.append("Новых отметок нет, все уже есть в общей карте.")
    if rejected:
        parts.append(f"Отклонено как некорректные: {rejected}.")
    if added or updated:
        parts.append("Карта на сайте обновится через пару минут.")
    summary = " ".join(parts)
    set_output("added", str(added + updated))
    set_output("summary", summary)
    print(summary)


if __name__ == "__main__":
    main()
