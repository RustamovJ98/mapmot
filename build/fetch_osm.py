"""Fetch raw OSM data for the Tashkent moto map via Overpass API.

Each query is cached in data/raw/<name>.json. Delete a file (or pass --force)
to re-download. Only the standard library is used.
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

BBOX = "41.18,69.10,41.42,69.45"  # south, west, north, east — Tashkent + ring
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERIES = {
    # Roads explicitly closed to motorcycles
    "banned": f"""
[out:json][timeout:180];
(
  way["highway"]["motorcycle"~"^(no|private)$"]({BBOX});
  way["highway"]["motorcycle:conditional"]({BBOX});
  way["highway"]["motor_vehicle"~"^(no|private)$"]["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"]({BBOX});
  way["highway"]["access"~"^(no|private)$"]["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"]["motorcycle"!~"."]({BBOX});
);
out tags geom;
""",
    # Main road network for scoring "good" riding roads
    "network": f"""
[out:json][timeout:300];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"]({BBOX});
);
out tags geom;
""",
    # Point hazards
    "hazards": f"""
[out:json][timeout:180];
(
  node["traffic_calming"]({BBOX});
  node["railway"="level_crossing"]({BBOX});
  node["highway"="speed_camera"]({BBOX});
  node["hazard"]({BBOX});
  way["hazard"]({BBOX});
  way["highway"~"^(trunk|primary|secondary|tertiary)(_link)?$"]["surface"~"^(unpaved|gravel|cobblestone|sett|dirt|ground|compacted|paving_stones|concrete:plates)$"]({BBOX});
  way["highway"~"^(trunk|primary|secondary|tertiary)(_link)?$"]["smoothness"~"^(bad|very_bad|horrible|very_horrible|impassable)$"]({BBOX});
)->.a;
.a out tags geom;
relation["type"="enforcement"]({BBOX})->.b;
.b out tags center;
""",
    # Useful POIs for a rider
    "pois": f"""
[out:json][timeout:180];
(
  nwr["amenity"="fuel"]({BBOX});
  nwr["shop"="motorcycle"]({BBOX});
  nwr["shop"="motorcycle_repair"]({BBOX});
  nwr["craft"="motorcycle_repair"]({BBOX});
  nwr["amenity"="motorcycle_parking"]({BBOX});
  nwr["service"="tyres"]({BBOX});
  nwr["shop"="tyres"]({BBOX});
);
out tags center;
""",
    # Russian street names for ride-mode voice prompts (tags only, every named street of the city)
    "names": f"""
[out:json][timeout:180];
way["highway"]["name"]["name:ru"]({BBOX});
out tags;
""",
}


MAX_LAG_HOURS = 36  # mirrors can lag by days; stale tags would silently roll the map back


def data_age_hours(result):
    ts = result.get("osm3s", {}).get("timestamp_osm_base")
    if not ts:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600


def fetch(query: str) -> dict:
    data = query.encode("utf-8")
    last_err, got_stale = None, False
    for endpoint in ENDPOINTS:
        for attempt in range(4):
            try:
                req = urllib.request.Request(endpoint, data=data, headers={"User-Agent": "MapMot/1.0 (tashkent moto map)"})
                with urllib.request.urlopen(req, timeout=400) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                age = data_age_hours(result)
                if age is not None and age > MAX_LAG_HOURS:
                    print(f"  {endpoint}: data is {age:.0f} h old, trying another server", file=sys.stderr)
                    got_stale = True
                    break
                return result
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"  {endpoint} attempt {attempt + 1} failed: {e}", file=sys.stderr)
                time.sleep(15 * (attempt + 1))
    if got_stale:
        raise RuntimeError("only stale Overpass data available; keeping the cached files")
    raise RuntimeError(f"all endpoints failed: {last_err}")


def main() -> None:
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name, query in QUERIES.items():
        if only and name not in only:
            continue
        out = RAW / f"{name}.json"
        if out.exists() and not force:
            print(f"{name}: cached ({out.stat().st_size // 1024} KB)")
            continue
        print(f"{name}: downloading…")
        result = fetch(query)
        out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        # mirrors (e.g. overpass.kumi.systems) can lag by days: always look at the data timestamp
        osm_ts = result.get("osm3s", {}).get("timestamp_osm_base", "?")
        print(f"{name}: {len(result.get('elements', []))} elements, {out.stat().st_size // 1024} KB, OSM as of {osm_ts}")
        time.sleep(3)


if __name__ == "__main__":
    main()
