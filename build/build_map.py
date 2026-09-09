"""Build dist/index.html from cached OSM data and the HTML template.

Layers produced (all GeoJSON, embedded into the page):
  banned     - ways closed to motorcycles (motorcycle=no / access=no / private)
  corridors  - recommended riding corridors from data/corridors.json
  hazards    - speed bumps, level crossings, speed cameras, bad surfaces
  pois       - petrol stations, tyre shops, motorcycle shops / parking
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DIST = ROOT / "docs"
DIST.mkdir(exist_ok=True)

HW_RU = {
    "motorway": "автомагистраль", "trunk": "магистраль", "primary": "главная дорога",
    "secondary": "второстепенная", "tertiary": "третьестепенная", "residential": "жилая улица",
    "living_street": "жилая зона", "unclassified": "местная дорога", "service": "проезд",
}


def load(name):
    return json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))["elements"]


def rnd(v):
    return round(v, 5)


def way_coords(way):
    return [[rnd(p["lon"]), rnd(p["lat"])] for p in way.get("geometry", [])]


def ru_name(tags):
    return tags.get("name:ru") or tags.get("name") or ""


def hw_ru(tags):
    hw_full = tags.get("highway") or ""
    hw = hw_full.replace("_link", "")
    base = HW_RU.get(hw, hw)
    return base + (" (съезд)" if "_link" in hw_full else "")


def feature(geom_type, coords, props):
    return {"type": "Feature", "geometry": {"type": geom_type, "coordinates": coords}, "properties": props}


def fc(features):
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------- banned
def build_banned():
    feats = []
    for w in load("banned"):
        t = w["tags"]
        if t.get("name") == "разворот":
            continue
        if t.get("motorcycle") in ("no", "private"):
            reason, tag = "Мотоциклам запрещено", "motorcycle=" + t["motorcycle"]
        elif t.get("motorcycle:conditional"):
            reason, tag = "Запрет по условию", "motorcycle:conditional=" + t["motorcycle:conditional"]
        elif t.get("motor_vehicle") in ("no", "private"):
            reason, tag = "Закрыто для моторного транспорта", "motor_vehicle=" + t["motor_vehicle"]
        else:
            reason, tag = "Проезд закрыт", "access=" + str(t.get("access"))
        feats.append(feature("LineString", way_coords(w), {
            "id": w["id"], "n": ru_name(t) or "Без названия", "h": hw_ru(t),
            "reason": reason, "tag": tag,
            "lanes": t.get("lanes"), "maxspeed": t.get("maxspeed"),
        }))
    return fc(feats)


# ------------------------------------------------------------- corridors
def build_corridors(banned_ids):
    cfg = json.loads((ROOT / "data" / "corridors.json").read_text(encoding="utf-8"))
    feats = []
    counts = {}
    for w in load("network"):
        t = w["tags"]
        if w["id"] in banned_ids:
            continue
        names = " | ".join(x.lower() for x in (t.get("name", ""), t.get("name:ru", ""), t.get("name:uz", "")))
        for c in cfg:
            if any(m in names for m in c["match"]) and not any(e in names for e in c.get("exclude", [])):
                counts[c["name"]] = counts.get(c["name"], 0) + 1
                feats.append(feature("LineString", way_coords(w), {
                    "id": w["id"], "n": c["name"], "h": hw_ru(t), "note": c["note"],
                    "surface": t.get("surface"), "smoothness": t.get("smoothness"),
                    "lanes": t.get("lanes"), "maxspeed": t.get("maxspeed"),
                }))
                break
    for c in cfg:
        if not counts.get(c["name"]):
            print("  ! corridor matched nothing: " + c["name"])
    return fc(feats), counts


# --------------------------------------------------------------- hazards
SURF_RU = {"unpaved": "без покрытия", "gravel": "гравий", "cobblestone": "булыжник", "sett": "брусчатка",
           "dirt": "грунт", "ground": "грунт", "compacted": "укатанный грунт", "paving_stones": "плитка",
           "concrete:plates": "бетонные плиты"}
SMOOTH_RU = {"bad": "плохое", "very_bad": "очень плохое", "horrible": "ужасное", "very_horrible": "ужасное",
             "impassable": "непроезжее"}
CALMING_RU = {"bump": "Лежачий полицейский", "hump": "Лежачий полицейский", "table": "Приподнятая площадка",
              "rumble_strip": "Шумовая полоса", "choker": "Сужение дороги", "yes": "Искусственная неровность"}


def build_hazards():
    feats = []
    for e in load("hazards"):
        t = e["tags"]
        if e["type"] == "node":
            if t.get("traffic_calming"):
                kind, label = "bump", CALMING_RU.get(t["traffic_calming"], "Искусственная неровность")
            elif t.get("railway") == "level_crossing":
                kind, label = "crossing", "Ж/д переезд"
            elif t.get("highway") == "speed_camera":
                kind = "camera"
                label = "Камера" + ((" · " + t["maxspeed"] + " км/ч") if t.get("maxspeed") else "")
            elif t.get("hazard"):
                kind, label = "hazard", "Опасность: " + t["hazard"]
            else:
                continue
            feats.append(feature("Point", [rnd(e["lon"]), rnd(e["lat"])], {"kind": kind, "label": label, "id": e["id"]}))
        else:
            if t.get("hazard"):
                label = "Опасность: " + t["hazard"]
            elif t.get("surface") in SURF_RU:
                label = "Покрытие: " + SURF_RU[t["surface"]]
            elif t.get("smoothness") in SMOOTH_RU:
                label = "Состояние дороги: " + SMOOTH_RU[t["smoothness"]]
            else:
                continue
            feats.append(feature("LineString", way_coords(e), {"kind": "surface", "label": label,
                                                                "n": ru_name(t), "h": hw_ru(t), "id": e["id"]}))
    return fc(feats)


# ------------------------------------------------------------------ pois
GAS_RE = re.compile(r"метан|пропан|агнск|агзс|gaz|газ|cng|lpg|metan|propan", re.I)
PETROL_KEYS = ("fuel:petrol", "fuel:octane_80", "fuel:octane_91", "fuel:octane_92",
               "fuel:octane_95", "fuel:octane_98", "fuel:octane_100")


def classify_fuel(t):
    fuel_tags = {k: v for k, v in t.items() if k.startswith("fuel:")}
    petrol = any(fuel_tags.get(k) == "yes" for k in PETROL_KEYS)
    gas = any(fuel_tags.get(k) == "yes" for k in ("fuel:cng", "fuel:lpg"))
    name = " ".join(t.get(k, "") for k in ("name", "brand", "operator", "name:ru"))
    if petrol:
        return "petrol"
    if gas or GAS_RE.search(name):
        return None
    if fuel_tags:  # tagged, but no petrol listed (e.g. diesel only)
        return None
    return "fuel_unknown"


def build_pois():
    feats = []
    for e in load("pois"):
        t = e["tags"]
        if e["type"] == "node":
            lon, lat = e["lon"], e["lat"]
        else:
            lon, lat = e["center"]["lon"], e["center"]["lat"]
        if t.get("amenity") == "fuel":
            kind = classify_fuel(t)
            if not kind:
                continue
            octanes = sorted(k.split("_")[1] for k, v in t.items() if k.startswith("fuel:octane_") and v == "yes")
            label = t.get("brand") or t.get("name") or t.get("operator") or "АЗС"
            if octanes:
                sub = "АИ-" + ", АИ-".join(octanes)
            else:
                sub = "бензин" if kind == "petrol" else "тип топлива не указан"
        elif t.get("shop") in ("motorcycle", "motorcycle_repair") or t.get("craft") == "motorcycle_repair":
            kind, label, sub = "moto", t.get("name") or "Мотосалон / мотосервис", "мотоциклы"
        elif t.get("amenity") == "motorcycle_parking":
            kind, label, sub = "parking", t.get("name") or "Мотопарковка", "парковка для мото"
        elif t.get("shop") == "tyres" or t.get("service") == "tyres":
            kind, label, sub = "tyres", t.get("name") or "Шиномонтаж", "шиномонтаж"
        else:
            continue
        props = {"kind": kind, "label": label, "sub": sub, "id": e["id"], "t": e["type"]}
        if t.get("opening_hours"):
            props["hours"] = t["opening_hours"]
        phone = t.get("phone") or t.get("contact:phone")
        if phone:
            props["phone"] = phone
        feats.append(feature("Point", [rnd(lon), rnd(lat)], props))
    return fc(feats)


def main():
    banned = build_banned()
    banned_ids = {f["properties"]["id"] for f in banned["features"]}
    corridors, counts = build_corridors(banned_ids)
    hazards = build_hazards()
    pois = build_pois()
    osm_date = json.loads((RAW / "banned.json").read_text(encoding="utf-8"))["osm3s"]["timestamp_osm_base"][:10]

    tmpl = (ROOT / "build" / "template.html").read_text(encoding="utf-8")
    data = {"banned": banned, "corridors": corridors, "hazards": hazards, "pois": pois,
            "osmDate": osm_date,
            "stats": {"banned": len(banned["features"]), "corridors": counts,
                      "hazards": len(hazards["features"]), "pois": len(pois["features"])}}
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out = tmpl.replace("/*__DATA__*/null", js)
    (DIST / "index.html").write_text(out, encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    print("banned %d | corridors %d %s | hazards %d | pois %d | OSM %s" % (
        len(banned["features"]), sum(counts.values()), counts, len(hazards["features"]), len(pois["features"]), osm_date))
    print("docs/index.html %d KB" % (len(out.encode("utf-8")) // 1024))


if __name__ == "__main__":
    main()
