# MapMot — мото-карта Ташкента

Карта для езды на мотоцикле по Ташкенту: где запрещено, где удобно ехать, где лежачие полицейские, переезды, камеры и АЗС с бензином.
Одна HTML-страница на Leaflet, данные из OpenStreetMap встроены в файл.

## Структура

```
build/fetch_osm.py     скачивает данные из Overpass в data/raw/*.json (кэш)
build/build_map.py     собирает docs/index.html из data/raw и build/template.html
build/template.html    интерфейс карты (Leaflet, светлая тема, мобильный вид)
data/corridors.json    список рекомендуемых коридоров (правится руками)
data/raw/              сырые ответы Overpass (banned, network, hazards, pois)
docs/index.html        готовая карта, один файл
```

## Пересборка

```bash
python build/fetch_osm.py           # скачать свежие данные (кэш пропускается)
python build/fetch_osm.py --force   # перекачать всё
python build/build_map.py           # собрать docs/index.html
python -m http.server 8765 --directory docs   # открыть http://localhost:8765
```

## Слои

| Слой | Источник | По умолчанию |
|------|----------|--------------|
| Запрет (красный) | ways с `motorcycle=no/private`, `motorcycle:conditional`, `motor_vehicle=no`, `access=no` на крупных дорогах | вкл |
| Коридоры (зелёный) | `data/corridors.json`, подбор по названию улицы в сети trunk…tertiary | вкл |
| Опасности (оранжевый) | `traffic_calming`, `railway=level_crossing`, `highway=speed_camera`, `hazard`, плохое `surface`/`smoothness` | выкл |
| АЗС / сервис (синий) | `amenity=fuel` без чисто газовых, `shop=motorcycle`, шиномонтаж, мотопарковки | выкл |

Точки опасностей и сервиса рисуются начиная с 14-го зума. Выбор слоёв запоминается в браузере.

## Ограничения

Карта основана на данных OSM и не заменяет знаки. Часть запретов в OSM может быть устаревшей или неполной.
