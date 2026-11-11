"""BuyWise phase 1: discover and recheck one Saudi Google Shopping offer.

Python 3.10+. No external packages. From a terminal:
    python buywise_probe.py discover "Sony WH-CH520"
    python buywise_probe.py refresh

The prompt takes a SerpApi key without displaying or saving it. An existing
SERPAPI_KEY environment variable is also accepted.
"""

import argparse
import getpass
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import urlopen


API_URL = "https://serpapi.com/search.json"
SNAPSHOT = Path("buywise_offer.json")
STORES = {
    "noon.com": "Noon",
    "extra.com": "eXtra",
    "aliexpress.com": "AliExpress",
    "asasonline.net": "Asas",
}
TRACKING = {"gclid", "fbclid", "srsltid"}
SAR_PRICE = re.compile(r"(?:(?<![a-z])(?:SAR|SR)(?![a-z])|ر\.?\s*س\.?|ريال سعودي)", re.I)


def request(key, **params):
    url = API_URL + "?" + urlencode({**params, "api_key": key})
    try:
        with urlopen(url, timeout=30) as response:
            data = json.load(response)
    except HTTPError as error:
        raise ValueError(f"SerpApi returned HTTP {error.code}. Check your key and quota.") from None
    except (URLError, TimeoutError):
        raise ValueError("Could not reach SerpApi. Check the network and try again.") from None
    if data.get("error"):
        message = str(data["error"]).replace(key, "[hidden]")
        raise ValueError(message[:240])
    return data


def identity(link):
    """Keep seller and variant parameters; ignore only known tracking parameters."""
    parsed = urlsplit(link)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in {"http", "https"}:
        return None
    store = next((name for domain, name in STORES.items()
                  if host == domain or host.endswith("." + domain)), None)
    if not store or not parsed.path.strip("/"):
        return None
    params = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                    if k.lower() not in TRACKING and not k.lower().startswith("utm_"))
    return store, host + parsed.path.rstrip("/") + "?" + urlencode(params)


def parse_offers(payload):
    offers = []
    rejected = []
    for row in payload.get("product_results", {}).get("stores", []):
        link = row.get("link") or ""
        match = identity(link)
        price_text = str(row.get("price") or "")
        reason = offer_rejection(match, price_text, row.get("extracted_price"))
        if reason:
            rejected.append((row.get("name") or "(unknown store)",
                             urlsplit(link).hostname or "(no domain)",
                             price_text or "(no price)", reason))
            continue
        price = Decimal(str(row["extracted_price"]))
        store, listing = match
        seller = row.get("name") or store
        offers.append({"store": store, "seller": seller, "listing": listing,
                       "title": row.get("title") or "", "url": link,
                       "price": str(price), "currency": "SAR"})
    return offers, rejected


def offer_rejection(match, price_text, extracted_price):
    if not match:
        return "No direct link to a supported store"
    # Do not infer currency from the locale or accept a range as one price.
    if not SAR_PRICE.search(price_text) or re.search(r"[-–—$€£]", price_text):
        return "Price is not clearly a single SAR amount"
    try:
        price = Decimal(str(extracted_price))
    except InvalidOperation:
        return "No numeric price"
    if not price.is_finite() or price <= 0:
        return "Invalid numeric price"
    return None


def fetch_offers(key, token):
    data = request(key, engine="google_immersive_product", page_token=token,
                   more_stores="true", no_cache="true")
    return parse_offers(data)


def choose(rows, label):
    for number, row in enumerate(rows, 1):
        print(f"{number}. {label(row)}")
    pick = input("Choose a number: ").strip()
    if not pick.isascii() or not pick.isdigit() or not 1 <= int(pick) <= len(rows):
        raise ValueError("Choose one of the displayed numbers.")
    return rows[int(pick) - 1]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(snapshot):
    temp = SNAPSHOT.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(SNAPSHOT)


def discover(key, query):
    data = request(key, engine="google_shopping", q=query, gl="sa", hl="en")
    products = [row for row in data.get("shopping_results", [])
                if row.get("immersive_product_page_token")][:12]
    if not products:
        raise ValueError("No products with offer details. Try a more specific model name.")
    product = choose(products, lambda p: p.get("title", "(Untitled)"))
    token = product["immersive_product_page_token"]
    offers, rejected = fetch_offers(key, token)
    if not offers:
        print(f"Offers returned on this page: {len(rejected)}", flush=True)
        for seller, domain, price, reason in rejected[:15]:
            print(f"{seller} | {domain} | {price} | {reason}", flush=True)
        raise ValueError("No offer passed the store and SAR price checks.")
    offer = choose(offers, lambda o: f'{o["seller"]} | {o["title"]} | {o["price"]} SAR')
    save({"query": query, "product_title": product.get("title"),
          "image_url": product.get("thumbnail"), "page_token": token,
          "offer": offer, "observations": [{"price": offer["price"], "observed_at": now()}]})
    print(f'Saved {offer["seller"]}: {offer["price"]} SAR\n{offer["url"]}\nSnapshot: {SNAPSHOT}')


def refresh(key):
    if not SNAPSHOT.exists():
        raise ValueError("Run discover first.")
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    original = snapshot["offer"]
    offers, _ = fetch_offers(key, snapshot["page_token"])
    candidates = [o for o in offers
                  if all(o[k] == original[k] for k in ("listing", "seller", "currency"))]
    if not candidates or len({o["price"] for o in candidates}) != 1:
        raise ValueError("The same offer has no unambiguous price now. Previous readings were kept.")
    price = candidates[0]["price"]
    previous = snapshot["observations"][-1]["price"]
    snapshot["observations"].append({"price": price, "observed_at": now()})
    save(snapshot)
    print(f'Offer matched: {original["seller"]} | {previous} -> {price} SAR')
    print(f'Open to compare with the retailer: {original["url"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("discover", help="Find and save one offer.")
    first.add_argument("query")
    sub.add_parser("refresh", help="Fetch the same offer again.")
    args = parser.parse_args()
    try:
        if args.command == "refresh" and not SNAPSHOT.exists():
            raise ValueError("Run discover first.")
        key = (os.getenv("SERPAPI_KEY") or getpass.getpass("SerpApi key: ")).strip()
        if not key:
            raise ValueError("A SerpApi key is required.")
        if args.command == "discover":
            discover(key, args.query)
        else:
            refresh(key)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()