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


_last_request = 0.0


def fetch_xml(url):
    global _last_request
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "Pako-BGG-Collection-Sync/1.1"
    })
    for attempt in range(6):
        time.sleep(max(0, 5.2 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status == 202:
                    time.sleep(min(10 * (attempt + 1), 60))
                    continue
                if response.status != 200:
                    raise RuntimeError(f"BGG HTTP {response.status}")
                root = ET.fromstring(response.read())
                if root.tag != "items" or root.find("error") is not None:
                    raise RuntimeError("BGG no devolvió una lista válida")
                return root
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(min(10 * (attempt + 1), 60))
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            if attempt == 5:
                raise
            time.sleep(10)
    raise RuntimeError("BGG no respondió tras seis intentos")


def parse_details(item, updated):
    kind = item.attrib.get("type")
    if kind not in ("boardgame", "boardgameexpansion", "boardgameaccessory"):
        return None
    bases, expansions = {}, {}
    for link in item.findall("link"):
        if link.attrib.get("type") != "boardgameexpansion":
            continue
        linked_id = as_int(link.attrib.get("id"))
        if not linked_id or linked_id == as_int(item.attrib.get("id")):
            continue
        target = bases if link.attrib.get("inbound") == "true" else expansions
        target[linked_id] = {"id": linked_id, "name": link.attrib.get("value", "")}
    return {"schema": 1, "type": kind, "updated": updated,
            "base_games": list(bases.values()), "expansions": list(expansions.values())}


def enrich_games(games):
    # The regular collection response labels expansions as boardgame.
    # Only the authenticated /thing response is authoritative for type/links.
    try:
        with open("collection.json", encoding="utf-8") as source:
            old = json.load(source)
        cache = {int(g["id"]): g["bgg_details"] for g in old.get("games", [])
                 if g.get("bgg_details", {}).get("schema") == 1}
    except (OSError, ValueError, TypeError, KeyError):
        cache = {}
    now = datetime.now(timezone.utc)
    needed = []
    for game_id in sorted({g["id"] for g in games}):
        detail = cache.get(game_id)
        try:
            fresh = detail and (now - datetime.fromisoformat(detail["updated"])).total_seconds() < 30 * 86400
        except (KeyError, ValueError, TypeError):
            fresh = False
        if not fresh:
            needed.append(game_id)
    errors = []
    for offset in range(0, len(needed), 20):
        batch = needed[offset:offset + 20]
        try:
            response = fetch_xml("https://boardgamegeek.com/xmlapi2/thing?id=" + ",".join(map(str, batch)))
            returned = set()
            for item in response.findall("item"):
                game_id = as_int(item.attrib.get("id"))
                if game_id not in batch:
                    continue
                detail = parse_details(item, datetime.now(timezone.utc).isoformat())
                if detail:
                    cache[game_id] = detail
                    returned.add(game_id)
            if len(returned) != len(batch):
                errors.append("BGG omitió algunos IDs; se mantiene su caché si existe")
            print(f"Fichas BGG: {min(offset + 20, len(needed))}/{len(needed)}", flush=True)
        except Exception as error:
            # A failed enrichment must never erase previously valid details.
            errors.append(f"Actualización parcial: {type(error).__name__}")
            break
    for game in games:
        game["collection_subtype"] = game.get("subtype")
        detail = cache.get(game["id"])
        if detail:
            game["bgg_details"] = detail
            game["subtype"] = detail["type"]
        else:
            game["subtype"] = None
    unique = {g["id"]: g for g in games}
    return {"schema": 1, "verified_count": sum(bool(g.get("bgg_details")) for g in unique.values()),
            "pending_count": sum(not g.get("bgg_details") for g in unique.values()),
            "cache_days": 30, "warnings": sorted(set(errors))}


root = fetch_xml(BGG_URL)
if not root.findall("item"):
    raise RuntimeError("Colección vacía: conservamos el JSON anterior")

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
        "image": item.findtext("image"),
        "thumbnail": item.findtext("thumbnail"),
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


owned_ids = sorted(set(owned_ids))
wishlist_ids = sorted(set(wishlist_ids))

enrichment = enrich_games(games)

result = {
    "metadata": {
        "username": BGG_USERNAME,
        "enrichment": enrichment,
        "updated": datetime.now(timezone.utc).isoformat(),
        "total_items": len(games),
        "owned_count": len(owned_ids),
        "wishlist_count": len(wishlist_ids),
    },
    "owned_ids": owned_ids,
    "wishlist_ids": wishlist_ids,
    "games": sorted(games, key=lambda g: (g["name"].lower(), g["id"])),
}


with open("collection.json.tmp", "w", encoding="utf-8") as f:
    json.dump(
        result,
        f,
        indent=2,
        ensure_ascii=False
    )


os.replace("collection.json.tmp", "collection.json")

print(
    f"OK: {len(games)} elementos, "
    f"{len(owned_ids)} juegos poseídos y "
    f"{len(wishlist_ids)} juegos en wishlist."
)

