"""Read BGG history; publish only an encrypted snapshot for private PAKOTECA."""
import base64
import json
import os
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

USERNAME = "_pako_"

def integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def parse_play(node):
    item = node.find("item")
    if item is None or not integer(node.get("id")) or not integer(item.get("objectid")):
        raise ValueError("Incomplete BGG play")
    return {"id": integer(node.get("id")), "gameId": integer(item.get("objectid")),
            "gameName": item.get("name", ""), "date": node.get("date", ""),
            "quantity": max(1, integer(node.get("quantity"), 1)),
            "minutes": integer(node.get("length")) or None,
            "incomplete": node.get("incomplete") == "1", "nowinstats": node.get("nowinstats") == "1",
            "location": node.get("location", ""), "notes": node.findtext("comments", ""),
            "players": [{k: p.get(k, "") for k in ("username", "userid", "name", "startposition", "color", "score", "new", "rating", "win")} for p in node.findall("players/player")]}

def fetch_page(token, subtype, page):
    url = f"https://boardgamegeek.com/xmlapi2/plays?username={USERNAME}&subtype={subtype}&page={page}"
    for attempt in range(6):
        time.sleep(5.2 if attempt == 0 else min(10 * attempt, 60))
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Pakoteca-Private-History/1.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status == 202:
                    continue
                root = ET.fromstring(response.read())
                if root.tag != "plays" or root.find("error") is not None or integer(root.get("total")) is None:
                    raise ValueError("Invalid BGG plays response")
                return root
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"BGG history HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            if attempt == 5:
                raise RuntimeError("BGG history unavailable") from None
    raise RuntimeError("BGG history exhausted retries")

def collect(token):
    merged = {}
    for subtype in ("boardgame", "boardgameexpansion"):
        first = fetch_page(token, subtype, 1)
        total = integer(first.get("total"))
        if total > 100000:
            raise ValueError("History exceeds supported size")
        found = {}
        for page in range(1, (total + 99) // 100 + 1):
            root = first if page == 1 else fetch_page(token, subtype, page)
            if integer(root.get("total")) != total:
                raise ValueError("History changed during pagination; retaining previous snapshot")
            for node in root.findall("play"):
                play = parse_play(node)
                if play["id"] in found:
                    raise ValueError("Duplicate play across pages; retaining previous snapshot")
                found[play["id"]] = play
        if len(found) != total:
            raise ValueError("Incomplete pagination; retaining previous snapshot")
        merged.update(found)
    return {"schema": 1, "username": USERNAME, "updated": datetime.now(timezone.utc).isoformat(),
            "plays": sorted(merged.values(), key=lambda p: (p["date"], p["id"]), reverse=True)}

def encrypt(payload, public_key):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, iv = os.urandom(32), os.urandom(12)
    public = serialization.load_pem_public_key(public_key.encode())
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    ciphertext = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), b"pakoteca-bgg-plays-v1")
    b64 = lambda x: base64.b64encode(x).decode()
    return {"schema": 1, "updated": payload["updated"], "wrappedKey": b64(wrapped), "iv": b64(iv), "ciphertext": b64(ciphertext)}

if __name__ == "__main__":
    token = os.environ.get("BGG_TOKEN")
    if not token:
        raise RuntimeError("Missing BGG_TOKEN")
    payload = collect(token)
    with open("pakoteca-history-public.pem", encoding="utf-8") as f:
        envelope = encrypt(payload, f.read())
    with open("plays.encrypted.json.tmp", "w", encoding="utf-8") as f:
        json.dump(envelope, f, separators=(",", ":"))
    os.replace("plays.encrypted.json.tmp", "plays.encrypted.json")
    print(f"Encrypted history updated: {len(payload['plays'])} BGG records. No player data logged.")

