import os
import json
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


BGG_USERNAME = "_pako_"
BGG_URL = f"https://boardgamegeek.com/xmlapi2/collection?username={BGG_USERNAME}&stats=1"

token = os.environ.get("BGG_TOKEN")

if not token:
    raise RuntimeError("No se ha encontrado la variable BGG_TOKEN")


def as_bool(value):
    return value == "1"


def as_int(value, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def as_float(value, default=None):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def download_collection():
    request = urllib.request.Request(
        BGG_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Pako-BGG-Collection-Sync/1.0"
        }
    )

    for attempt in range(1, 11):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:

                # BGG usa HTTP 202 mientras prepara colecciones
                # que requieren más procesamiento, por ejemplo stats=1.
                if response.status == 202:
                    print(
                        f"BGG está preparando la colección. "
                        f"Intento {attempt}/10. Esperando 10 segundos..."
                    )
                    time.sleep(10)
                    continue

                if response.status != 200:
                    raise RuntimeError(
                        f"Respuesta inesperada de BGG: HTTP {response.status}"
                    )

                data = response.read()

                if not data:
                    raise RuntimeError(
                        "BGG devolvió una respuesta vacía."
                    )

                return data

        except urllib.error.HTTPError as e:
            if e.code == 202:
                print(
                    f"BGG está preparando la colección. "
                    f"Intento {attempt}/10. Esperando 10 segundos..."
                )
                time.sleep(10)
                continue

            raise

    raise RuntimeError(
        "BGG no terminó de preparar la colección "
        "después de 10 intentos."
    )


xml_data = download_collection()
root = ET.fromstring(xml_data)

games = []
owned_ids = []
wishlist_ids = []

for item in root.findall("item"):
    game_id = as_int(item.attrib.get("objectid"))
    name_node = item.find("name")
    year_node = item.find("yearpublished")
    status = item.find("status")
    rating = item.find("stats/rating")

    if game_id is None or name_node is None:
        continue

    game = {
        "id": game_id,
        "name": name_node.text,
        "year": as_int(year_node.text) if year_node is not None else None,
        "subtype": item.attrib.get("subtype"),
        "collection_id": as_int(item.attrib.get("collid")),
        "plays": as_int(item.findtext("numplays"), 0),
    }

    if status is not None:
        game["status"] = {
            "own": as_bool(status.attrib.get("own")),
            "prevowned": as_bool(status.attrib.get("prevowned")),
            "fortrade": as_bool(status.attrib.get("fortrade")),
            "want": as_bool(status.attrib.get("want")),
            "wanttoplay": as_bool(status.attrib.get("wanttoplay")),
            "wanttobuy": as_bool(status.attrib.get("wanttobuy")),
            "wishlist": as_bool(status.attrib.get("wishlist")),
            "preordered": as_bool(status.attrib.get("preordered")),
            "lastmodified": status.attrib.get("lastmodified"),
        }

        wishlist_priority = status.attrib.get("wishlistpriority")
        if wishlist_priority not in (None, ""):
            game["status"]["wishlistpriority"] = as_int(wishlist_priority)

        if game["status"]["own"]:
            owned_ids.append(game_id)

        if game["status"]["wishlist"]:
            wishlist_ids.append(game_id)

    # Datos personales de valoración/comentario cuando existan
    personal_rating = item.find("stats/rating")
    if personal_rating is not None:
        value = personal_rating.attrib.get("value")
        if value and value != "N/A":
            game["personal_rating"] = as_float(value)

    comment = item.findtext("comment")
    if comment:
        game["comment"] = comment

    private_comment = item.findtext("privatecomment")
    if private_comment:
        game["private_comment"] = private_comment

    # Datos públicos básicos incluidos en stats=1
    stats = item.find("stats")
    if stats is not None:
        game["public"] = {
            "minplayers": as_int(stats.attrib.get("minplayers")),
            "maxplayers": as_int(stats.attrib.get("maxplayers")),
            "minplaytime": as_int(stats.attrib.get("minplaytime")),
            "maxplaytime": as_int(stats.attrib.get("maxplaytime")),
            "playingtime": as_int(stats.attrib.get("playingtime")),
            "numowned": as_int(stats.attrib.get("numowned")),
        }

        rating_node = stats.find("rating")
        if rating_node is not None:
            average_node = rating_node.find("average")
            bayes_node = rating_node.find("bayesaverage")
            usersrated_node = rating_node.find("usersrated")
            weight_node = rating_node.find("averageweight")
            ranks_node = rating_node.find("ranks")

            if average_node is not None:
                game["public"]["average"] = as_float(
                    average_node.attrib.get("value")
                )

            if bayes_node is not None:
                game["public"]["bayesaverage"] = as_float(
                    bayes_node.attrib.get("value")
                )

            if usersrated_node is not None:
                game["public"]["usersrated"] = as_int(
                    usersrated_node.attrib.get("value")
                )

            if weight_node is not None:
                game["public"]["averageweight"] = as_float(
                    weight_node.attrib.get("value")
                )

            if ranks_node is not None:
                ranks = []

                for rank in ranks_node.findall("rank"):
                    ranks.append({
                        "type": rank.attrib.get("type"),
                        "id": rank.attrib.get("id"),
                        "name": rank.attrib.get("name"),
                        "friendlyname": rank.attrib.get("friendlyname"),
                        "value": as_int(rank.attrib.get("value")),
                        "bayesaverage": as_float(
                            rank.attrib.get("bayesaverage")
                        ),
                    })

                game["public"]["ranks"] = ranks

    games.append(game)


result = {
    "metadata": {
        "username": BGG_USERNAME,
        "updated": datetime.now(timezone.utc).isoformat(),
        "total_items": len(games),
        "owned_count": len(owned_ids),
        "wishlist_count": len(wishlist_ids),
    },
    "owned_ids": sorted(set(owned_ids)),
    "wishlist_ids": sorted(set(wishlist_ids)),
    "games": sorted(games, key=lambda g: (g["name"].lower(), g["id"])),
}


with open("collection.json", "w", encoding="utf-8") as f:
    json.dump(
        result,
        f,
        indent=2,
        ensure_ascii=False
    )


print(
    f"OK: {len(games)} elementos, "
    f"{len(set(owned_ids))} juegos poseídos y "
    f"{len(set(wishlist_ids))} juegos en wishlist."
)
