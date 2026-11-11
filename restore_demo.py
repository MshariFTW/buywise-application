
"""Restore the BuyWise demo into an empty local catalog."""
from datetime import datetime
from decimal import Decimal
from getpass import getpass
import json
from pathlib import Path

import psycopg


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS product_categories (
        category_key TEXT PRIMARY KEY, name TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        model_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        image_url TEXT,
        category TEXT REFERENCES product_categories(category_key),
        discovery_token TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS offers (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        product_id BIGINT NOT NULL REFERENCES products(id),
        store TEXT NOT NULL, seller TEXT NOT NULL, listing_key TEXT NOT NULL,
        url TEXT NOT NULL, page_token TEXT NOT NULL, currency TEXT NOT NULL,
        advertised_title TEXT,
        UNIQUE (store, seller, listing_key, currency)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_observations (
        offer_id BIGINT NOT NULL REFERENCES offers(id),
        observed_at TIMESTAMPTZ NOT NULL,
        price NUMERIC(12, 2) NOT NULL CHECK (price > 0),
        PRIMARY KEY (offer_id, observed_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS product_image_embeddings (
        product_id BIGINT PRIMARY KEY REFERENCES products(id),
        model_name TEXT NOT NULL, source_url TEXT NOT NULL,
        embedding DOUBLE PRECISION[] NOT NULL CHECK (cardinality(embedding) = 512)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_cache (
        query_key TEXT NOT NULL,
        category TEXT NOT NULL REFERENCES product_categories(category_key),
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        product_ids BIGINT[] NOT NULL,
        PRIMARY KEY (query_key, category)
    )
    """,
)


def restore(snapshot, password):
    settings = {"host": "localhost", "port": 5432, "user": "postgres", "password": password}
    with psycopg.connect(**settings, dbname="postgres", autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = 'buywise'").fetchone() is None:
            admin.execute("CREATE DATABASE buywise")

    with psycopg.connect(**settings, dbname="buywise") as conn:
        exists = conn.execute("SELECT to_regclass('public.products')").fetchone()[0]
        if exists and conn.execute("SELECT EXISTS (SELECT 1 FROM products)").fetchone()[0]:
            return False

        for statement in SCHEMA:
            conn.execute(statement)
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO product_categories (category_key, name) VALUES (%s, %s) "
                "ON CONFLICT (category_key) DO NOTHING", snapshot["categories"],
            )
            cursor.executemany(
                "INSERT INTO products (id, model_key, title, image_url, category) "
                "OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s, %s)", snapshot["products"],
            )
            cursor.executemany(
                "INSERT INTO product_image_embeddings (product_id, model_name, source_url, embedding) "
                "VALUES (%s, %s, %s, %s)", snapshot["embeddings"],
            )
            cursor.executemany(
                "INSERT INTO offers (id, product_id, store, seller, currency, url, advertised_title, "
                "listing_key, page_token) OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '')",
                [(*row, "demo:" + str(row[0])) for row in snapshot["offers"]],
            )
            cursor.executemany(
                "INSERT INTO price_observations (offer_id, observed_at, price) VALUES (%s, %s, %s)",
                [(offer, datetime.fromisoformat(at), Decimal(price))
                 for offer, at, price in snapshot["history"]],
            )

        for table in ("products", "offers"):
            conn.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1), "
                f"MAX(id) IS NOT NULL) FROM {table}", (table,),
            )
    return True


def main():
    snapshot = json.loads(Path(__file__).with_name("demo_catalog.json").read_text(encoding="utf-8"))
    if snapshot.get("format") != 1:
        raise SystemExit("Unsupported demo catalog format.")
    if restore(snapshot, getpass("PostgreSQL password: ")):
        print("Restored", len(snapshot["products"]), "demo products.")
    else:
        print("The existing catalog already has products. No demo data was written.")


if __name__ == "__main__":
    main()
