"""Build dist/index.html from cached OSM data and the HTML template.

Layers produced (all GeoJSON, embedded into the page):
  banned     - ways closed to motorcycles (motorcycle=no / access=no / private)
  corridors  - recommended riding corridors from data/corridors.json
  hazards    - speed bumps, level crossings, speed cameras, bad surfaces
  pois       - petrol stations, tyre shops, motorcycle shops / parking
"""
import hashlib
import json
import re
import shutil
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
SIGN_WORDS = ("rider", "sign", "знак", "motorcycle", "мотоцикл", "restricted area")


def source_note(src, closed):
    """Turn OSM edit history into a confidence label + short note."""
    if not src or not src.get("date"):
        return "unknown", "Источник правки не найден"
    year = int(src["date"][:4])
    comment = (src.get("comment") or "").lower()
    who = f"{src.get('user', '?')}, {src['date']}"
    if closed:
        return "closed", f"Дорога закрыта для всех ({who})"
    if any(k in comment for k in SIGN_WORDS):
        return "sign", f"Отмечено по дорожным знакам ({who})"
    if "#wbgeo" in comment:
        return "bulk", f"Массовая правка по панорамам ({who}). Стоит проверить знак на месте"
    if year < 2020:
        return "old", f"Старые данные ({who}). Могли устареть, проверьте знак"
    return "osm", f"Правка OSM ({who})"


def build_banned():
    src_path = ROOT / "data" / "ban_sources.json"
    sources = json.loads(src_path.read_text(encoding="utf-8")) if src_path.exists() else {}
    feats = []
    conf_count = {}
    for w in load("banned"):
        t = w["tags"]
        if t.get("name") == "разворот":
            continue
        closed = False
        if t.get("motorcycle") in ("no", "private"):
            reason, tag = "Мотоциклам запрещено", "motorcycle=" + t["motorcycle"]
        elif t.get("motorcycle:conditional"):
            reason, tag = "Запрет по условию", "motorcycle:conditional=" + t["motorcycle:conditional"]
        elif t.get("motor_vehicle") in ("no", "private"):
            reason, tag, closed = "Закрыто для моторного транспорта", "motor_vehicle=" + t["motor_vehicle"], True
        else:
            reason, tag, closed = "Проезд закрыт", "access=" + str(t.get("access")), True
        src = sources.get(str(w["id"]), {})
        conf, note = source_note(src, closed)
        conf_count[conf] = conf_count.get(conf, 0) + 1
        props = {
            "id": w["id"], "n": ru_name(t) or "Без названия", "h": hw_ru(t),
            "reason": reason, "tag": tag, "conf": conf, "src": note,
            "lanes": t.get("lanes"), "maxspeed": t.get("maxspeed"),
        }
        if src.get("comment"):
            props["comment"] = src["comment"][:140]
        feats.append(feature("LineString", way_coords(w), props))
    print("  ban sources:", conf_count)
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
        if e["type"] == "relation":
            continue  # enforcement relations go to the radars layer
        if e["type"] == "node":
            if t.get("traffic_calming"):
                kind, label = "bump", CALMING_RU.get(t["traffic_calming"], "Искусственная неровность")
            elif t.get("railway") == "level_crossing":
                kind, label = "crossing", "Ж/д переезд"
            elif t.get("highway") == "speed_camera":
                continue  # radars layer
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


# ---------------------------------------------------------------- radars
DIR_RU = {"forward": "по ходу", "backward": "против хода", "both": "в обе стороны"}
ENF_RU = {"maxspeed": ("speed", "Радар скорости"), "traffic_signals": ("signals", "Камера на светофоре"),
          "maxheight": ("height", "Контроль высоты"), "average_speed": ("avg", "Средняя скорость"),
          "check": ("check", "Пост контроля")}


def direction_ru(v):
    if not v:
        return None
    if v in DIR_RU:
        return DIR_RU[v]
    if v.replace(".", "").isdigit():
        return f"направление {v}°"
    return v


def build_radars():
    import math
    feats, cams = [], []
    for e in load("hazards"):
        t = e["tags"]
        if e["type"] == "node" and t.get("highway") == "speed_camera":
            enf = t.get("enforcement", "maxspeed")
            kind, label = ENF_RU.get(enf, ("speed", "Радар скорости"))
            cams.append((e["lat"], e["lon"]))
            feats.append(feature("Point", [rnd(e["lon"]), rnd(e["lat"])], {
                "kind": kind, "label": label, "maxspeed": t.get("maxspeed"), "dir": direction_ru(t.get("direction")),
                "n": t.get("name:ru") or t.get("name") or t.get("description"), "id": e["id"], "t": "node"}))
    # enforcement relations: add only if there is no camera node within ~40 m
    for e in load("hazards"):
        t = e["tags"]
        if e["type"] != "relation" or "center" not in e:
            continue
        lat, lon = e["center"]["lat"], e["center"]["lon"]
        if any(math.hypot((lat - a) * 110574, (lon - b) * 111320 * 0.75) < 40 for a, b in cams):
            continue
        kind, label = ENF_RU.get(t.get("enforcement", ""), ("other", "Контроль"))
        feats.append(feature("Point", [rnd(lon), rnd(lat)], {
            "kind": kind, "label": label, "maxspeed": t.get("maxspeed"), "dir": None,
            "n": t.get("name:ru") or t.get("name") or t.get("description"), "id": e["id"], "t": "relation"}))
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
        name = t.get("name:ru") or t.get("name") or ""
        if t.get("amenity") == "fuel":
            kind = classify_fuel(t)
            if not kind:
                continue
            fuels = ["АИ-" + k.split("_")[1] for k, v in sorted(t.items()) if k.startswith("fuel:octane_") and v == "yes"]
            if t.get("fuel:diesel") == "yes":
                fuels.append("дизель")
            if t.get("fuel:cng") == "yes":
                fuels.append("метан")
            if t.get("fuel:lpg") == "yes":
                fuels.append("пропан")
            label = name or t.get("brand") or t.get("operator") or "АЗС"
            if fuels:
                sub = ", ".join(fuels)
            else:
                sub = "бензин" if kind == "petrol" else "виды топлива в OSM не указаны"
        elif t.get("shop") in ("motorcycle", "motorcycle_repair") or t.get("craft") == "motorcycle_repair":
            kind, label, sub = "moto", name or "Мотосалон / мотосервис", "мотоциклы, запчасти, сервис"
        elif t.get("amenity") == "motorcycle_parking":
            kind, label, sub = "parking", name or "Мотопарковка", "парковка для мото"
        elif t.get("shop") == "tyres" or t.get("service") == "tyres":
            kind, label, sub = "tyres", name or "Шиномонтаж", "шиномонтаж"
        else:
            continue
        props = {"kind": kind, "label": label, "sub": sub, "id": e["id"], "t": e["type"]}
        brand = t.get("brand") or t.get("operator")
        if brand and brand != label:
            props["brand"] = brand
        if t.get("opening_hours"):
            props["hours"] = t["opening_hours"]
        phone = t.get("phone") or t.get("contact:phone")
        if phone:
            props["phone"] = phone.split(";")[0].strip()
        site = t.get("website") or t.get("contact:website")
        if site:
            props["site"] = site
        addr = " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
        if addr:
            props["addr"] = addr
        feats.append(feature("Point", [rnd(lon), rnd(lat)], props))
    return fc(feats)


# ------------------------------------------------------------ basemap style
STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"


def build_style():
    """Local copy of the OpenFreeMap Liberty style with Russian labels first (name:ru -> latin -> name)."""
    import urllib.request
    cache = RAW / "style_liberty.json"
    try:
        req = urllib.request.Request(STYLE_URL, headers={"User-Agent": "MapMot/1.0"})
        data = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
        json.loads(data)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(data, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("  style download failed, using cache:", e)
    if not cache.exists():
        return False
    style = json.loads(cache.read_text(encoding="utf-8"))
    patched = 0
    for layer in style.get("layers", []):
        tf = layer.get("layout", {}).get("text-field")
        if tf is None or "name" not in json.dumps(tf):
            continue
        layer["layout"]["text-field"] = ["coalesce", ["get", "name:ru"], ["get", "name:latin"], ["get", "name"]]
        patched += 1
    (DIST / "style").mkdir(exist_ok=True)
    (DIST / "style" / "liberty-ru.json").write_text(json.dumps(style, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("  style: %d label layers switched to name:ru" % patched)
    return True


def main():
    build_style()
    banned = build_banned()
    banned_ids = {f["properties"]["id"] for f in banned["features"]}
    corridors, counts = build_corridors(banned_ids)
    hazards = build_hazards()
    radars = build_radars()
    pois = build_pois()
    osm_date = json.loads((RAW / "banned.json").read_text(encoding="utf-8"))["osm3s"]["timestamp_osm_base"][:10]

    tmpl = (ROOT / "build" / "template.html").read_text(encoding="utf-8")
    marks_path = ROOT / "data" / "marks.json"
    marks = json.loads(marks_path.read_text(encoding="utf-8")) if marks_path.exists() else []
    data = {"banned": banned, "corridors": corridors, "hazards": hazards, "radars": radars, "pois": pois,
            "marks": marks, "osmDate": osm_date,
            "stats": {"banned": len(banned["features"]), "corridors": counts, "radars": len(radars["features"]),
                      "hazards": len(hazards["features"]), "pois": len(pois["features"])}}
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out = tmpl.replace("/*__DATA__*/null", js)
    # static assets (service worker, manifest, vendored Leaflet, icons)
    static = ROOT / "build" / "static"
    shutil.copytree(static, DIST, dirs_exist_ok=True)
    version = hashlib.sha1((out + (static / "sw.js").read_text(encoding="utf-8")).encode("utf-8")).hexdigest()[:10]
    out = out.replace("__VERSION__", version)
    (DIST / "index.html").write_text(out, encoding="utf-8")
    (DIST / "sw.js").write_text((static / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", version), encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    print("banned %d | corridors %d | hazards %d | radars %d | pois %d | OSM %s" % (
        len(banned["features"]), sum(counts.values()), len(hazards["features"]), len(radars["features"]),
        len(pois["features"]), osm_date))
    print("  corridors:", counts)
    print("docs/index.html %d KB" % (len(out.encode("utf-8")) // 1024))


if __name__ == "__main__":
    main()
