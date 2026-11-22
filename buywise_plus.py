"""BuyWise Plus: adds a shopping workspace to the existing local BuyWise app.

Place this file beside buywise_api.py and run: python buywise_plus.py
This launcher uses the existing catalog, search, image index and price history.
"""

import argparse
import json
from getpass import getpass
from hashlib import sha256
from decimal import Decimal, InvalidOperation
from threading import Lock
from uuid import UUID, uuid4

import os
from pathlib import Path
from buywise_db import connect_db, migrate, password_prompt
from buywise_hosting import RequestLimit, USAGE_SCHEMA
from buywise_retrieval import dimensions
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from buywise_api import create_app
from buywise_probe import fetch_offers


PORT = 8002
LOCAL_VISITOR = UUID(int=0)
VISITOR_COOKIE = "__Host-buywise_visitor"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS buywise_saved_items (
        product_id BIGINT REFERENCES products(id) ON DELETE CASCADE,
        visitor UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
        target_price NUMERIC(12, 2) CHECK (target_price > 0),
        note TEXT NOT NULL DEFAULT '',
        saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (product_id, visitor)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS buywise_offer_scans (
        product_id BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        saved_count INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL
    )
    """,
    """
    ALTER TABLE offers ADD COLUMN IF NOT EXISTS advertised_title TEXT
    """,
)


BEST_OFFER = """
                LEFT JOIN LATERAL (
                    SELECT last_reading.price, o.store, last_reading.observed_at, o.size_label
                    FROM offers AS o
                    CROSS JOIN LATERAL (
                        SELECT price, observed_at
                        FROM price_observations
                        WHERE offer_id = o.id
                        ORDER BY observed_at DESC
                        LIMIT 1
                    ) AS last_reading
                    WHERE o.product_id = p.id AND o.currency = 'SAR'
                    ORDER BY last_reading.price, last_reading.observed_at DESC, o.id
                    LIMIT 1
                ) AS best ON TRUE
"""


class SavedItem(BaseModel):
    target_price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    note: str = Field(default="", max_length=500)


def install_schema(password):
    with connect_db(password) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('buywise:schema'))")
        migrate(conn)
        for statement in SCHEMA:
            conn.execute(statement)
        # Existing local shortlists are assigned to the regular desktop session.
        if not conn.execute("""
            SELECT 1 FROM pg_attribute
            WHERE attrelid = 'buywise_saved_items'::regclass
              AND attname = 'visitor' AND NOT attisdropped
        """).fetchone():
            conn.execute("""
                ALTER TABLE buywise_saved_items
                    ADD COLUMN visitor UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
                    DROP CONSTRAINT buywise_saved_items_pkey,
                    ADD PRIMARY KEY (product_id, visitor)
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS buywise_saved_by_visitor ON buywise_saved_items(visitor)")
        conn.execute(USAGE_SCHEMA)


def choose_offers(candidates):
    """Keep a listing only when the provider gives it one usable price."""
    grouped = {}
    for offer in candidates:
        fields = tuple(
            offer.get(key) for key in ("store", "seller", "listing", "currency")
        )
        if not all(fields) or fields[-1] != "SAR":
            continue
        try:
            price = Decimal(str(offer.get("price")))
        except InvalidOperation:
            continue
        if not price.is_finite() or not 0 < price < Decimal("10000000000"):
            continue
        grouped.setdefault(fields, {})[(price, offer.get("title"))] = offer
    selected = []
    skipped = 0
    for variants in grouped.values():
        if len(variants) != 1:
            skipped += 1
            continue
        (price, _), offer = next(iter(variants.items()))
        selected.append((offer, price))
    return selected, skipped


def iso(value):
    return value.isoformat() if value is not None else None


def money(value):
    return str(value) if value is not None else None


def build_app(password, api_key, *, public_origin=""):
    hosted = bool(public_origin)
    online = RequestLimit(
        password, "serpapi",
        int(os.getenv("BUYWISE_SERP_DAILY", "15")),
        int(os.getenv("BUYWISE_SERP_MONTHLY", "225")),
        int(os.getenv("BUYWISE_SERP_PER_VISITOR", "5")),
    )
    refresh_limit = RequestLimit(password, "retailer", daily=150, monthly=2000, visitor_daily=10)
    app = create_app(password, api_key, claim_online=online.claim if hosted else None)
    if not isinstance(app, FastAPI):
        raise RuntimeError("buywise_api.create_app() must return the FastAPI app.")
    scan_lock = Lock()

    @app.middleware("http")
    async def check_write_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected_origins = {public_origin} if hosted else {
                f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"
            }
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin not in expected_origins
            ):
                return JSONResponse(
                    {"detail": "Open BuyWise and retry the request."}, 403
                )
        visitor = LOCAL_VISITOR
        new_visitor = False
        if hosted:
            try:
                visitor = UUID(request.cookies.get(VISITOR_COOKIE, ""))
                if visitor.version != 4:
                    raise ValueError("Invalid visitor")
            except ValueError:
                visitor = uuid4()
                new_visitor = True
        request.state.visitor = visitor
        response = await call_next(request)
        if hosted and request.url.path in {"/", "/shop", "/shop/api/product-info", "/shop/api/list"}:
            response.headers["Cache-Control"] = "private, no-store"
        if new_visitor and (request.url.path == "/" or request.url.path.startswith(("/shop", "/discover"))):
            response.set_cookie(
                VISITOR_COOKIE, str(visitor), max_age=365 * 24 * 60 * 60,
                httponly=True, secure=True, samesite="lax",
            )
        return response

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/shop", response_class=HTMLResponse, include_in_schema=False)
    def shop():
        return Path(__file__).with_name("buywise_ui.html").read_text(encoding="utf-8")

    @app.get("/BuyWise_Demo.html", response_class=HTMLResponse, include_in_schema=False)
    def interface_demo():
        return Path(__file__).with_name("BuyWise_Demo.html").read_text(encoding="utf-8")

    @app.get("/shop/api/product-info")
    def product_info(request: Request, ids: list[int] = Query()):
        if len(ids) > 200 or any(item < 1 for item in ids):
            raise HTTPException(400, "أرقام المنتجات غير صالحة.")
        with connect_db(password) as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.category, NULLIF(p.discovery_token, '') IS NOT NULL,
                       s.product_id IS NOT NULL, p.size_label, best.price, best.store, best.observed_at, p.title, p.image_url
                FROM products AS p
                LEFT JOIN buywise_saved_items AS s ON s.product_id = p.id AND s.visitor = %s
                {BEST_OFFER}
                WHERE p.id = ANY(%s)
                """,
                (request.state.visitor, ids),
            ).fetchall()
        return [
            {
                "id": item_id,
                "category": category,
                "title": title, "image_url": image_url,
                "can_scan": can_scan,
                "saved": saved,
                "size": size,
                "latest_price": money(price),
                "store": store,
                "observed_at": iso(at),
            }
            for item_id, category, can_scan, saved, size, price, store, at, title, image_url in rows
        ]

    @app.get("/shop/api/products/{product_id}/offers")
    def offers_for(product_id: int):
        with connect_db(password) as conn:
            if not conn.execute(
                "SELECT id FROM products WHERE id = %s AND category = 'rugs'", (product_id,)
            ).fetchone():
                raise HTTPException(404, "المنتج غير موجود.")
            rows = conn.execute(
                """
                SELECT o.id, o.store, o.seller, o.currency, o.url, o.advertised_title, o.listing_key,
                       last_reading.price, last_reading.observed_at, o.size_label
                FROM offers AS o
                LEFT JOIN LATERAL (
                    SELECT price, observed_at
                    FROM price_observations
                    WHERE offer_id = o.id
                    ORDER BY observed_at DESC
                    LIMIT 1
                ) AS last_reading ON TRUE
                WHERE o.product_id = %s
                ORDER BY o.currency, last_reading.price ASC NULLS LAST, o.id
                """,
                (product_id,),
            ).fetchall()
        return [
            {
                "id": item_id,
                "store": store,
                "seller": seller,
                "currency": currency,
                "url": url,
                "advertised_title": title,
                "direct": listing.startswith(("IKEA:", "Home Centre:")),
                "price": money(price),
                "observed_at": iso(observed_at),
                "size": size,
            }
            for item_id, store, seller, currency, url, title, listing, price, observed_at, size in rows
        ]

    @app.post("/shop/api/products/{product_id}/refresh")
    def refresh_retail_offer(request: Request, product_id: int):
        from buywise_refresh_db import refresh

        if hosted:
            refresh_limit.claim(request.state.visitor)
        with scan_lock:
            return refresh(password, product_id=product_id)

    @app.post("/shop/api/products/{product_id}/scan-offers")
    def scan_offers(request: Request, product_id: int):
        with scan_lock:
            with connect_db(password) as conn:
                item = conn.execute(
                    "SELECT discovery_token,title,size_label FROM products WHERE id = %s AND category='rugs'",
                    (product_id,),
                ).fetchone()
                if item is None:
                    raise HTTPException(404, "المنتج غير موجود.")
                cached = conn.execute(
                    """
                    SELECT saved_count, skipped_count, checked_at
                    FROM buywise_offer_scans
                    WHERE product_id = %s AND checked_at > NOW() - INTERVAL '1 day'
                    """,
                    (product_id,),
                ).fetchone()
            if cached is not None:
                return {
                    "saved": cached[0],
                    "skipped": cached[1],
                    "checked_at": iso(cached[2]),
                    "cached": True,
                }
            if not item[0]:
                raise HTTPException(
                    409, "هذا المنتج ما له بيانات جلب. تقدر تشوف عروضه المحفوظة."
                )
            if not api_key:
                raise HTTPException(
                    503, "الجلب الجديد يحتاج مفتاح SerpApi عند تشغيل السيرفر."
                )
            if hosted:
                online.claim(request.state.visitor)
            try:
                candidates, rejected = fetch_offers(api_key, item[0])
            except (ValueError, OSError) as exc:
                raise HTTPException(
                    502, "تعذّر جلب العروض الآن. راجع اتصال SerpApi والمفتاح."
                ) from exc

            selected, skipped = choose_offers(candidates)
            skipped += len(rejected)
            saved = 0
            with connect_db(password) as conn:
                for offer, price in selected:
                    offer_size = dimensions(offer.get("title"))
                    if (
                        not offer_size
                        or not item[2]
                        or offer_size != item[2]
                        or offer.get("title", "").casefold().strip()
                        != item[1].casefold().strip()
                    ):
                        skipped += 1
                        continue
                    result = conn.execute(
                        """
                        INSERT INTO offers
                            (product_id, store, seller, listing_key, url, page_token,
                             currency, advertised_title, size_label)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (store, seller, listing_key, currency) DO UPDATE
                        SET url = EXCLUDED.url, page_token = EXCLUDED.page_token,
                            advertised_title = EXCLUDED.advertised_title
                        WHERE offers.product_id = EXCLUDED.product_id AND offers.size_label = EXCLUDED.size_label
                          AND offers.advertised_title = EXCLUDED.advertised_title
                        RETURNING id
                        """,
                        (
                            product_id,
                            offer["store"],
                            offer["seller"],
                            offer["listing"],
                            offer["url"],
                            item[0],
                            offer["currency"],
                            offer.get("title"),
                            offer_size,
                        ),
                    ).fetchone()
                    if result is None:
                        skipped += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO price_observations (offer_id, observed_at, price)
                        VALUES (%s, NOW(), %s)
                        ON CONFLICT (offer_id, observed_at) DO NOTHING
                        """,
                        (result[0], price),
                    )
                    saved += 1
                checked_at = conn.execute(
                    """
                    INSERT INTO buywise_offer_scans (product_id, saved_count, skipped_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (product_id) DO UPDATE
                    SET checked_at = NOW(), saved_count = EXCLUDED.saved_count,
                        skipped_count = EXCLUDED.skipped_count
                    RETURNING checked_at
                    """,
                    (product_id, saved, skipped),
                ).fetchone()[0]
            return {
                "saved": saved,
                "skipped": skipped,
                "checked_at": iso(checked_at),
                "cached": False,
            }

    @app.get("/shop/api/list")
    def list_items(request: Request):
        with connect_db(password) as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.title, p.image_url, p.category,
                       NULLIF(p.discovery_token, '') IS NOT NULL,
                       s.target_price, s.note, best.price, best.store, best.observed_at, CASE WHEN best.store IS NULL THEN p.size_label ELSE best.size_label END
                FROM buywise_saved_items AS s
                JOIN products AS p ON p.id = s.product_id
                {BEST_OFFER}
                WHERE p.category = 'rugs' AND s.visitor = %s
                ORDER BY s.saved_at DESC, p.id
                """,
                (request.state.visitor,),
            ).fetchall()
        return [
            {
                "id": item_id,
                "title": title,
                "image_url": image,
                "category": category,
                "can_scan": can_scan,
                "saved": True,
                "target_price": money(target),
                "note": note,
                "latest_price": money(price),
                "store": store,
                "observed_at": iso(observed_at),
                "size": size,
            }
            for item_id, title, image, category, can_scan, target, note, price, store, observed_at, size in rows
        ]

    @app.post("/shop/api/list/{product_id}")
    def save_item(request: Request, product_id: int):
        with connect_db(password) as conn:
            if not conn.execute(
                "SELECT id FROM products WHERE id = %s AND category = 'rugs'", (product_id,)
            ).fetchone():
                raise HTTPException(404, "المنتج غير موجود.")
            conn.execute(
                """
                INSERT INTO buywise_saved_items (product_id, visitor) VALUES (%s, %s)
                ON CONFLICT (product_id, visitor) DO NOTHING
                """,
                (product_id, request.state.visitor),
            )
        return {"saved": True}

    @app.put("/shop/api/list/{product_id}")
    def edit_item(request: Request, product_id: int, item: SavedItem):
        with connect_db(password) as conn:
            cursor = conn.execute(
                """
                UPDATE buywise_saved_items SET target_price = %s, note = %s
                WHERE product_id = %s AND visitor = %s
                """,
                (item.target_price, item.note.strip(), product_id, request.state.visitor),
            )
            if not cursor.rowcount:
                raise HTTPException(404, "المنتج غير موجود في القائمة.")
        return {"saved": True}

    @app.delete("/shop/api/list/{product_id}")
    def remove_item(request: Request, product_id: int):
        with connect_db(password) as conn:
            conn.execute(
                "DELETE FROM buywise_saved_items WHERE product_id = %s AND visitor = %s",
                (product_id, request.state.visitor),
            )
        return {"saved": False}

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="Prompt privately for a SerpApi key")
    args = parser.parse_args()
    public_origin = (os.getenv("BUYWISE_PUBLIC_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
    hosted = bool(public_origin)
    if hosted and not os.getenv("DATABASE_URL"):
        parser.error("Hosted BuyWise requires DATABASE_URL.")
    password = password_prompt()
    api_key = os.getenv("SERPAPI_KEY", "").strip()
    if args.online and not api_key and not hosted:
        api_key = getpass("SerpApi key (hidden, Enter to skip): ").strip()
    from restore_demo import restore, SNAPSHOT

    snapshot = SNAPSHOT.read_bytes()
    if hosted:
        install_schema(password)
        seed_hash = sha256(snapshot).hexdigest()
        with connect_db(password) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS buywise_imported_catalogs (seed_hash TEXT PRIMARY KEY)
            """)
        with connect_db(password) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('buywise:seed'))")
            if not conn.execute("SELECT 1 FROM buywise_imported_catalogs WHERE seed_hash=%s", (seed_hash,)).fetchone():
                restore(json.loads(snapshot), password, create=False)
                conn.execute("INSERT INTO buywise_imported_catalogs VALUES (%s)", (seed_hash,))
    else:
        restore(json.loads(snapshot), password)
        install_schema(password)
    app = build_app(password, api_key, public_origin=public_origin)
    port = int(os.getenv("PORT", str(PORT))) if hosted else PORT
    print(f"BuyWise: {public_origin or f'http://127.0.0.1:{port}'}/shop", flush=True)
    uvicorn.run(app, host="0.0.0.0" if hosted else "127.0.0.1", port=port, access_log=not hosted)


if __name__ == "__main__":
    main()
