import argparse
import re
from getpass import getpass

import psycopg

from buywise_probe import request


def connect_db(password):
    return psycopg.connect(
        host="localhost", port=5432, dbname="buywise",
        user="postgres", password=password,
    )


def discover_products(query, category, password, api_key=None):
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

    key = getpass("SerpApi key: ").strip() if api_key is None else api_key

    if not key:
        raise ValueError(
            "هذا البحث غير محفوظ. أعد تشغيل السيرفر وأدخل مفتاح SerpApi للجلب."
        )
    data = request(
        key, engine="google_shopping", q=query_key, gl="sa", hl="en"
    )

    products = {}
    with connect_db(password) as conn:
        for result in data.get("shopping_results", [])[:20]:
            source_id = str(result.get("product_id") or "")
            title = result.get("title") or ""
            image_url = result.get("thumbnail") or ""

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
                    (model_key, title, image_url, category, discovery_token)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (model_key) DO UPDATE
                SET discovery_token = COALESCE(
                    EXCLUDED.discovery_token, products.discovery_token
                )
                WHERE products.category = EXCLUDED.category
                RETURNING id, title
                """,
                (
                    "google:" + source_id,
                    title,
                    image_url,
                    category,
                    result.get("immersive_product_page_token"),
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

    password = getpass("PostgreSQL password: ")
    rows = discover_products(args.query, args.category, password)

    print(f"\nProducts in this batch: {len(rows)}")
    for product_id, title in rows:
        print(f"{product_id}. {title}")