import os
import json
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


BGG_USERNAME = "_pako_"
BGG_URL = f"https://boardgamegeek.com/xmlapi2/collection?username={BGG_USERNAME}"

token = os.environ.get("BGG_TOKEN")

if not token:
    raise RuntimeError("No se ha encontrado la variable BGG_TOKEN")


def download_collection():
    request = urllib.request.Request(
        BGG_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Pako-BGG-Collection-Sync/1.0"
        }
    )

    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()

        except urllib.error.HTTPError as e:
            if e.code == 202:
                print(
                    f"BGG está preparando la colección. "
                    f"Intento {attempt}/5. Esperando 10 segundos..."
                )
                time.sleep(10)
                continue
            raise

    raise RuntimeError(
        "BGG siguió devolviendo HTTP 202 después de varios intentos."
    )


xml_data = download_collection()
print("Primeros 1000 caracteres de la respuesta de BGG:")
print(xml_data[:1000].decode("utf-8", errors="replace"))

root = ET.fromstring(xml_data)

owned = []
wishlist = []

for item in root.findall("item"):
    game_id = int(item.attrib["objectid"])
    status = item.find("status")

    if status is None:
        continue

    if status.attrib.get("own") == "1":
        owned.append(game_id)

    if status.attrib.get("wishlist") == "1":
        wishlist.append(game_id)


result = {
    "username": BGG_USERNAME,
    "updated": datetime.now(timezone.utc).isoformat(),
    "owned_count": len(owned),
    "wishlist_count": len(wishlist),
    "owned": sorted(owned),
    "wishlist": sorted(wishlist)
}


with open("collection.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)


print(
    f"OK: {len(owned)} juegos poseídos y "
    f"{len(wishlist)} juegos en wishlist."
)
