import argparse
import os
import re
from getpass import getpass

from buywise_db import connect_db, password_prompt
from buywise_retrieval import dimensions

from buywise_probe import request


def discover_products(query, category, password, api_key=None, *, before_search=None):
    if category != "rugs":
        raise ValueError("Select the rugs category.")
    query_key = " ".join(query.lower().split())
    if not 2 <= len(query_key) <= 120:
        raise ValueError("Write a search query between 2 and 120 characters.")

    with connect_db(password) as conn:
        valid_category = conn.execute(
            "SELECT category_key FROM product_categories WHERE category_key = %s",
            (category,),
        ).fetchone()
        if not valid_category:
            raise ValueError("Category not found.")

        cached = conn.execute(
            """
            SELECT product_ids FROM discovery_cache
            WHERE query_key = %s AND category = %s
              AND checked_at > NOW() - INTERVAL '1 day'
            """,
            (query_key, category),
        ).fetchone()

        if cached is not None:
            print("Using saved search. No SerpApi request.")
            return conn.execute(
                "SELECT id, title FROM products WHERE id = ANY(%s) ORDER BY id",
                (cached[0],),
            ).fetchall()

    key = (
        (os.getenv("SERPAPI_KEY") or getpass("SerpApi key: ").strip())
        if api_key is None
        else api_key
    )

    if not key:
        raise ValueError(
            "هذا البحث غير محفوظ. أعد تشغيل السيرفر وأدخل مفتاح SerpApi للجلب."
        )
    if before_search is not None:
        before_search()
    data = request(
        key,
        engine="google_shopping",
        q=query_key
        if re.search(r"\b(rugs?|carpets?)\b", query_key)
        else query_key + " rug",
        gl="sa",
        hl="en",
    )

    products = {}
    with connect_db(password) as conn:
        for result in data.get("shopping_results", [])[:20]:
            source_id = str(result.get("product_id") or "")
            title = result.get("title") or ""
            image_url = result.get("thumbnail") or ""

            if not re.search(r"\b(rugs?|carpets?)\b|سجاد", title, re.I):
                continue
            if re.search(
                r"\b(?:rug pads?|carpet tape|cleaner|shampoo|underlay|gripper|rug grippers?)\b",
                title,
                re.I,
            ):
                continue
            if not source_id or not title or not image_url.startswith("https://"):
                continue
            if re.search(
                r"\b(bundle|protection pack|used|renewed|refurbished|pre[- ]?owned)\b"
                r"|مستعمل|مجدّد|مجدد",
                title,
                re.IGNORECASE,
            ):
                continue

            row = conn.execute(
                """
                INSERT INTO products
                    (model_key, title, image_url, category, discovery_token, size_label)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_key) DO UPDATE
                SET image_url=EXCLUDED.image_url,
                    discovery_token = COALESCE(
                    EXCLUDED.discovery_token, products.discovery_token
                )
                WHERE products.category = EXCLUDED.category AND products.title=EXCLUDED.title
                  AND products.size_label IS NOT DISTINCT FROM EXCLUDED.size_label
                RETURNING id, title
                """,
                (
                    "google:" + source_id,
                    title,
                    image_url,
                    category,
                    result.get("immersive_product_page_token"),
                    dimensions(title),
                ),
            ).fetchone()

            if row:
                products[row[0]] = row[1]

        conn.execute(
            """
            INSERT INTO discovery_cache (query_key, category, product_ids)
            VALUES (%s, %s, %s)
            ON CONFLICT (query_key, category) DO UPDATE
            SET checked_at = NOW(), product_ids = EXCLUDED.product_ids
            """,
            (query_key, category, list(products)),
        )

    return list(products.items())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("category")
    args = parser.parse_args()

    password = password_prompt()
    rows = discover_products(args.query, args.category, password)

    print(f"\nProducts in this batch: {len(rows)}")
    for product_id, title in rows:
        print(f"{product_id}. {title}")
