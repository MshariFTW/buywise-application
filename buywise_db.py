"""PostgreSQL configuration and additive catalog migrations."""

import os
from getpass import getpass
import psycopg
from psycopg import sql


def password_prompt():
    if os.getenv("DATABASE_URL"):
        return None
    if "PGPASSWORD" in os.environ:
        return os.environ["PGPASSWORD"]
    return getpass("PostgreSQL password: ")


def connect_db(password, database=None, **kwargs):
    if url := os.getenv("DATABASE_URL"):
        if database is not None:
            kwargs["dbname"] = database
        return psycopg.connect(url, connect_timeout=5, **kwargs)
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=database or os.getenv("PGDATABASE", "buywise"),
        password=password,
        **kwargs,
    )


def create_database(password):
    name = os.getenv("PGDATABASE", "buywise")
    with connect_db(password, "postgres", autocommit=True) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
        ).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


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


def migrate(conn):
    for statement in SCHEMA:
        conn.execute(statement)
    conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS design_key TEXT")
    conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS size_label TEXT")
    conn.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS advertised_title TEXT")
    conn.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS size_label TEXT")
    conn.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS color_label TEXT")
    conn.execute(
        "ALTER TABLE product_image_embeddings ADD COLUMN IF NOT EXISTS image_sha256 TEXT"
    )
    conn.execute(
        "ALTER TABLE product_image_embeddings ADD COLUMN IF NOT EXISTS model_revision TEXT"
    )
    conn.execute(
        "INSERT INTO product_categories VALUES ('rugs','Patterned rugs') ON CONFLICT DO NOTHING"
    )
