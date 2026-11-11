"""Public discovery sources. Never reads personal profiles or play history."""
import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

def now():
    return datetime.now(timezone.utc).isoformat()

def fetch(url, token=None):
    headers = {"User-Agent": "Pakoteca-Discovery/1.0 (+public source attribution)"}
    if token:
        headers["Authorization"] = "Bearer " + token
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=35) as r:
                if r.status == 202:
                    time.sleep(10)
                    continue
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {e.code}") from None
            time.sleep(10 * (attempt+1))
    raise RuntimeError("Source unavailable")

def numeric(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def hot_games(token):
    root = ET.fromstring(fetch("https://boardgamegeek.com/xmlapi2/hot?type=boardgame", token))
    if root.tag != "items":
        raise ValueError("Invalid hot list")
    hot = [{"id": int(x.get("id")), "hotRank": int(x.get("rank")), "name": x.find("name").get("value")} for x in root.findall("item")]
    if not hot:
        raise ValueError("Empty hot list")
    result = []
    for offset in range(0, len(hot), 20):
        time.sleep(5.2)
        ids = ",".join(str(x["id"]) for x in hot[offset:offset+20])
        details = ET.fromstring(fetch("https://boardgamegeek.com/xmlapi2/thing?id="+ids+"&stats=1", token))
        for item in details.findall("item"):
            gid = int(item.get("id"))
            position = next((x["hotRank"] for x in hot if x["id"] == gid), None)
            if not position:
                continue
            value = lambda path: numeric(item.find(path).get("value")) if item.find(path) is not None else None
            names = [x.get("value") for x in item.findall("name")]
            primary = next((x.get("value") for x in item.findall("name") if x.get("type") == "primary"), names[0])
            taxonomy = lambda kind: [{"id": int(x.get("id")), "name": x.get("value")} for x in item.findall("link") if x.get("type") == kind]
            result.append({"id": gid, "name": primary, "names": names, "hotRank": position, "year": value("yearpublished"), "image": item.findtext("image"), "thumbnail": item.findtext("thumbnail"), "public": {"minplayers": value("minplayers"), "maxplayers": value("maxplayers"), "maxplaytime": value("maxplaytime"), "average": value("statistics/ratings/average")}, "bgg_details": {"schema": 1, "type": item.get("type"), "complexity": value("statistics/ratings/averageweight"), "categories": taxonomy("boardgamecategory"), "mechanics": taxonomy("boardgamemechanic")}})
    if len(result) != len(hot):
        raise ValueError("Incomplete BGG details")
    return {"updated": now(), "url": "https://boardgamegeek.com/hotness", "items": sorted(result, key=lambda x: x["hotRank"]), "warning": None}

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.current, self.stack = [], None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article" and "product-miniature" in a.get("class", ""):
            self.current = {"id": a.get("data-id-product"), "texts": {}, "url": None, "image": None}
            self.stack = []
        if self.current is None:
            return
        cls = a.get("class", "").split()
        if tag == "a" and "product-thumbnail" in cls:
            self.current["url"] = a.get("href")
        if tag == "img" and ("first_img" in cls or self.current["image"] is None):
            self.current["image"] = a.get("data-src") or a.get("src")
        if tag not in ("img", "input", "br", "meta", "link", "hr"):
            self.stack.append((tag, cls))

    def handle_data(self, data):
        if self.current is None:
            return
        active = {x for _, classes in self.stack for x in classes}
        for kind in ("product-title", "price", "regular-price", "available_stock"):
            if kind in active:
                self.current["texts"][kind] = self.current["texts"].get(kind, "") + data

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == "article":
            self.items.append(self.current)
            self.current = None
            self.stack = []
            return
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                break

def price(text):
    value = re.sub(r"[^\d,.]", "", text)
    if not value:
        return None
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return numeric(value)

def parse_products(html, section):
    parser = ArticleParser()
    parser.feed(html)
    result = []
    for p in parser.items:
        url = p["url"] or ""
        if not url.startswith("https://dracotienda.com/") or not any('/'+x+'/' in url for x in ("juegos-de-tablero", "juegos-de-cartas", "juegos-infantiles", "wargames")):
            continue
        t = p["texts"]
        name = " ".join(t.get("product-title", "").split())
        current, reference = price(t.get("price", "")), price(t.get("regular-price", ""))
        if not name or current is None or current <= 0:
            continue
        stock = " ".join(t.get("available_stock", "").split())
        availability = "preorder" if "preventa" in stock.lower() else "in_stock" if "stock" in stock.lower() and "sin stock" not in stock.lower() or "últimas unidades" in stock.lower() else "unknown"
        if "preventa" in (name+" "+url).lower() and availability == "in_stock":
            availability = "preorder"
            stock = "Posible preventa; confirma fecha y disponibilidad en la tienda"
        if "agotado" in stock.lower() and "casi" not in stock.lower():
            availability = "out_of_stock"
        result.append({"id": "draco_"+str(p["id"]), "name": name, "url": url, "image": p["image"], "price": current, "referencePrice": reference if reference and reference > current else None, "currency": "EUR", "availability": availability, "stockLabel": stock or "Disponibilidad no indicada", "store": "Dracotienda", "section": section})
    return result

def draco_offers():
    items = {}
    for page in range(1, 4):
        if page > 1:
            time.sleep(2)
        url = "https://dracotienda.com/1879-ofertas" + (f"?page={page}" if page > 1 else "")
        parsed = parse_products(fetch(url), "offers")
        if not parsed:
            raise ValueError("Shop format changed or empty page")
        for p in parsed:
            items[p["id"]] = p
    return {"updated": now(), "url": "https://dracotienda.com/1879-ofertas", "items": list(items.values()), "coverage": "Primeras 3 páginas de ofertas; solo juegos de tablero, cartas, infantiles y wargames.", "warning": None}

def draco_new():
    html = fetch("https://dracotienda.com/")
    # First homepage carousel is explicitly labelled board-game novelties.
    start = html.find('1881-novedades-juegos-de-mesa')
    segment = html[start:] if start >= 0 else ""
    parsed = parse_products(segment, "new")[:12]
    if not parsed:
        raise ValueError("No homepage novelties")
    return {"updated": now(), "url": "https://dracotienda.com/", "items": parsed, "warning": None}

def blog_news():
    root = ET.fromstring(fetch("https://misutmeeple.com/feed/"))
    items = []
    for item in root.findall("channel/item")[:12]:
        link = item.findtext("link", "")
        if not link.startswith("https://misutmeeple.com/"):
            continue
        date = item.findtext("pubDate")
        try:
            date = parsedate_to_datetime(date).isoformat()
        except (TypeError, ValueError):
            date = None
        items.append({"title": unescape(item.findtext("title", "")), "url": link, "published": date, "source": "Misut Meeple"})
    if not items:
        raise ValueError("Empty editorial feed")
    return {"updated": now(), "url": "https://misutmeeple.com/", "items": items, "warning": None}

def main():
    try:
        with open("discovery.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"schema": 1}
    for key,fn in (("hot", lambda: hot_games(os.environ["BGG_TOKEN"])), ("offers", draco_offers), ("new", draco_new), ("news", blog_news)):
        try:
            data[key] = fn()
            print(f"{key}: {len(data[key]['items'])} public items")
        except Exception as error:
            previous = data.get(key, {"updated": None, "items": []})
            previous["warning"] = "No se pudo actualizar esta fuente; se conserva la última copia disponible."
            data[key] = previous
            print(f"{key}: retaining cache ({type(error).__name__})")
    data["checked"] = now()
    history = data.setdefault("priceHistory", {})
    for p in data.get("offers", {}).get("items", []):
        stamp = data["offers"].get("updated")
        if not stamp or data["offers"].get("warning"):
            continue
        points = history.setdefault(p["id"], [])
        point = {"date": stamp[:10], "price": p["price"]}
        if points and points[-1]["date"] == point["date"]:
            points[-1] = point
        else:
            points.append(point)
        history[p["id"]] = points[-90:]
    from market_sync import update_market
    data = update_market(data, fetch)
    with open("discovery.json.tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace("discovery.json.tmp", "discovery.json")

if __name__ == "__main__":
    main()

