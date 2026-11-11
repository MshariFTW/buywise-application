from datetime import datetime, timezone
from decimal import Decimal
from getpass import getpass
import re
from urllib.parse import urlsplit, parse_qs
import psycopg

from buywise_probe import fetch_offers


password = getpass("PostgreSQL password: ")
api_key = getpass("SerpApi key: ").strip()


def connect_db():
    return psycopg.connect(
        host="localhost", port=5432, dbname="buywise",
        user="postgres", password=password,
    )


with connect_db() as conn:
    saved_offers = conn.execute(
        """
        SELECT id, store, seller, listing_key, currency, page_token
        FROM offers
        ORDER BY id
        """
    ).fetchall()
def same_listing(saved, returned):
    if saved == returned:
        return True

    a = urlsplit("https://" + saved)
    b = urlsplit("https://" + returned)

    if a.hostname != "noon.com" or b.hostname != "noon.com":
        return False

    if a.path.split("/")[1] != b.path.split("/")[1]:
        return False

    sku_a = re.search(r"/([A-Z0-9]+)/p/?$", a.path)
    sku_b = re.search(r"/([A-Z0-9]+)/p/?$", b.path)
    if not sku_a or not sku_b or sku_a[1] != sku_b[1]:
        return False

    params_a = parse_qs(a.query, keep_blank_values=True)
    params_b = parse_qs(b.query, keep_blank_values=True)

    offer = params_a.get("o", [])
    if len(offer) != 1 or not offer[0] or offer != params_b.get("o"):
        return False

    store_a = params_a.pop("store", None)
    store_b = params_b.pop("store", None)

    if store_b is not None and (len(store_b) != 1 or not store_b[0]):
        return False
    if store_a is not None and store_a != store_b:
        return False

    return params_a == params_b
for offer_id, store, seller, listing_key, currency, token in saved_offers:
    try:
        fresh_offers, rejected = fetch_offers(api_key, token)
    except (ValueError, OSError) as error:
        print(f"Offer {offer_id}: could not fetch — {error}")
        continue

    matches = [
        offer for offer in fresh_offers
        if offer["store"] == store
        and offer["seller"] == seller
        and same_listing(listing_key, offer["listing"])
        and offer["currency"] == currency
    ]

    prices = {offer["price"] for offer in matches}
    listings = {offer["listing"] for offer in matches}

    if len(prices) != 1 or len(listings) != 1:
        print(f"Offer {offer_id}: no single matching offer. Old readings kept.")
        continue

    matched = matches[0]
    price = Decimal(next(iter(prices)))
    observed_at = datetime.now(timezone.utc)

    with connect_db() as conn:
        conn.execute(
            "UPDATE offers SET listing_key = %s, url = %s WHERE id = %s",
            (matched["listing"], matched["url"], offer_id),
        )
        conn.execute(
            """
            INSERT INTO price_observations (offer_id, observed_at, price)
            VALUES (%s, %s, %s)
            """,
            (offer_id, observed_at, price),
        )

    print(f"Saved offer {offer_id} ({store}): {price} {currency}")