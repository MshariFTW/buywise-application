"""Refresh saved rug offers once. Retailer pages are free; provider calls require --serpapi."""

import argparse
import os
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from buywise_db import connect_db, password_prompt
from buywise_probe import fetch_offers
from buywise_retail import fetch_page, read_listing
from buywise_retrieval import dimensions


def matched_price(rows, saved):
    oid, store, seller, listing, currency, token, url, title, size, color = saved
    if not size or not title or currency != "SAR":
        return None
    same = [
        r
        for r in rows
        if (r["store"], r["seller"], r["listing"], r["currency"])
        == (store, seller, listing, currency)
    ]
    if any(r["title"] != title or dimensions(r["title"]) != size for r in same):
        return None
    prices = {Decimal(r["price"]) for r in same}
    return prices.pop() if len(prices) == 1 else None


def refresh(password, provider_key=None, product_id=None):
    with connect_db(password) as conn:
        sql = """SELECT o.id,o.store,o.seller,o.listing_key,o.currency,o.page_token,o.url,
                 o.advertised_title,o.size_label,o.color_label FROM offers o JOIN products p ON o.product_id=p.id
                 WHERE p.category='rugs' AND o.currency='SAR'"""
        rows = conn.execute(
            sql + (" AND p.id=%s" if product_id is not None else "") + " ORDER BY o.id",
            (product_id,) if product_id is not None else (),
        ).fetchall()
    cache = {}
    counts = defaultdict(int)
    for saved in rows:
        oid, store, seller, listing, currency, token, url, title, size, color = saved
        try:
            if listing.startswith(("IKEA:", "Home Centre:")):
                fresh = read_listing(url, fetch_page(url))
                if (
                    fresh["key"],
                    fresh["store"],
                    fresh["title"],
                    fresh["size"],
                    fresh["color"],
                ) != (listing, store, seller, title, size, color):
                    raise ValueError("SKU, seller, title, size or colour changed")
                price = Decimal(fresh["price"])
            elif provider_key and token:
                if token not in cache:
                    cache[token] = None
                    cache[token] = fetch_offers(provider_key, token)[0]
                price = matched_price(cache[token] or [], saved)
                if price is None:
                    raise ValueError("Offer no longer matches uniquely")
            else:
                counts["skipped"] += 1
                continue
            with connect_db(password) as conn:
                result = conn.execute(
                    """INSERT INTO price_observations (offer_id,observed_at,price)
                    SELECT id,clock_timestamp(),%s FROM offers WHERE id=%s AND store=%s AND seller=%s
                    AND listing_key=%s AND currency=%s AND url=%s AND advertised_title=%s AND size_label=%s
                    AND color_label IS NOT DISTINCT FROM %s""",
                    (
                        price,
                        oid,
                        store,
                        seller,
                        listing,
                        currency,
                        url,
                        title,
                        size,
                        color,
                    ),
                )
                if not result.rowcount:
                    raise ValueError("Stored listing changed during refresh")
            counts["saved"] += 1
        except (ValueError, OSError, KeyError, TypeError, InvalidOperation) as exc:
            counts["failed"] += 1
            print(f"Offer {oid}: previous history retained ({type(exc).__name__}).")
    return dict(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serpapi",
        action="store_true",
        help="Also refresh token-backed offers; consumes provider quota.",
    )
    parser.add_argument("--product", type=int, help="Refresh one product ID")
    args = parser.parse_args()
    key = os.getenv("SERPAPI_KEY") if args.serpapi else None
    if args.serpapi and not key:
        parser.error("--serpapi requires SERPAPI_KEY")
    print(refresh(password_prompt(), key, args.product))
