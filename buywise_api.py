import json
import re
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from buywise_discover import discover_products
from buywise_db import connect_db
from buywise_image_index import approved_image, index_missing_images
from buywise_online_image import index_selected_rug
from buywise_retrieval import catalog, ranked, terms, threshold

BASE = Path(__file__).parent
STORE_NAMES = {
    "ABYAT": "أبيات ابيات", "CB2": "سي بي تو",
    "Zara Home": "زارا زارا هوم", "Weft Rug": "ويفت",
    "IKEA": "ايكيا إيكيا", "Home Centre": "هوم سنتر",
}


def photo(url):
    if not approved_image(url):
        raise HTTPException(404, "Unsupported photo")
    local = BASE / "photos" / (sha256(url.encode()).hexdigest() + ".jpg")
    return FileResponse(local) if local.is_file() else RedirectResponse(url)


def create_app(password, serpapi_key="", claim_online=None):
    app = FastAPI()
    patterns = json.loads((BASE / "rug_patterns.json").read_text(encoding="utf-8"))

    @app.get("/shop/api/settings")
    def shop_settings():
        settings = json.loads((BASE / "shop_settings.json").read_text(encoding="utf-8-sig"))
        email = str(settings.get("merchant_email", "")).strip()
        valid = re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email)
        return {"merchant_email": email if valid else "", "online_enabled": bool(serpapi_key)}

    @app.get("/shop/api/patterns")
    def pattern_tiles():
        with connect_db(password) as conn:
            photos = dict(conn.execute(
                "SELECT model_key, image_url FROM products WHERE category='rugs'"
            ).fetchall())
        tiles = []
        for key, group in patterns.items():
            present = [item for item in group["products"] if item in photos]
            if present:
                cover = photos.get(group["cover"]) or photos[present[0]]
                tiles.append(dict(key=key, en=group["en"], ar=group["ar"],
                                  image_url=cover, count=len(present)))
        return tiles

    @app.get("/price-history")
    def price_history(offer_id: int = 1):
        with connect_db(password) as conn:
            offer = conn.execute(
                """
                SELECT products.title, offers.store, offers.currency
                FROM offers
                JOIN products ON products.id = offers.product_id
                WHERE offers.id = %s
                """,
                (offer_id,),
            ).fetchone()

            if offer is None:
                raise HTTPException(status_code=404, detail="Offer not found")

            readings = conn.execute(
                """
                SELECT price, observed_at
                FROM price_observations
                WHERE offer_id = %s
                ORDER BY observed_at
                """,
                (offer_id,),
            ).fetchall()

        title, store, currency = offer
        return {
            "product": title,
            "store": store,
            "currency": currency,
            "readings": [
                {"price": str(price), "observed_at": observed_at.isoformat()}
                for price, observed_at in readings
            ],
        }

    @app.get("/photo")
    def product_photo(url: str):
        return photo(url)

    @app.get("/")
    def home():
        return FileResponse(
            Path(__file__).with_name("buywise_ui.html"),
            media_type="text/html",
        )

    @app.get("/search")
    def search_products(q: str, pattern: str | None = None):
        if pattern is not None and pattern not in patterns:
            raise HTTPException(422, "Unknown pattern")
        members = set(patterns[pattern]["products"]) if pattern else None
        query = terms(q)
        aliases = terms("rug rugs carpet carpets سجادة سجاد")
        with connect_db(password) as conn:
            rows = conn.execute(
                """SELECT p.id, p.title, p.image_url, p.size_label, p.model_key,
                          ARRAY(SELECT DISTINCT o.store FROM offers o WHERE o.product_id=p.id)
                   FROM products p WHERE p.category='rugs' ORDER BY p.id"""
            ).fetchall()
        return [
            {"id": pid, "title": title, "image_url": image, "size": size}
            for pid, title, image, size, key, stores in rows
            if (members is None or key in members) and query <= (terms(title) | aliases | terms(" ".join(
                name + " " + STORE_NAMES.get(name, "") for name in stores
            )))
        ]

    @app.get("/products/{product_id}/similar")
    def similar_products(
        product_id: int, minimum: float | None = Query(None, ge=-1, le=1)
    ):
        cutoff = threshold() if minimum is None else minimum
        with connect_db(password) as conn:
            items = catalog(conn, online=True)
        if product_id not in items:
            try:
                index_selected_rug(password, product_id)
            except (OSError, ValueError) as error:
                raise HTTPException(503, "Could not process this rug photo") from error
            with connect_db(password) as conn:
                items = catalog(conn, online=True)
        if product_id not in items:
            raise HTTPException(404, "A current rug embedding is unavailable")
        return [
            {
                "id": rug.id,
                "title": rug.title,
                "image_url": rug.image,
                "similarity": score,
            }
            for rug, score in ranked(items, items[product_id], "image")
            if score > cutoff
        ]

    @app.get("/shop/api/products/{product_id}/alternatives")
    def shop_alternatives(product_id: int, step: int = Query(0, ge=0, le=3)):
        initial = threshold()
        levels = [initial, *(level for level in (0.88, 0.86, 0.84) if level < initial)]
        # Every cutoff selects a prefix of this one stable, deduplicated ranking.
        products = similar_products(product_id, minimum=-1)
        counts = []
        for cutoff in levels:
            count = sum(product["similarity"] > cutoff for product in products)
            if not counts or count != counts[-1]:
                counts.append(count)
        closest_only = counts == [0] and bool(products)
        if closest_only:
            counts.append(min(3, len(products)))
        if step >= len(counts):
            raise HTTPException(422, "No wider result set")
        count = counts[step]
        additional = counts[step + 1] - count if step + 1 < len(counts) else 0
        return {
            "broadened": step > 0,
            "can_widen": additional > 0,
            "closest_available": closest_only and step > 0,
            "next_additions": additional,
            "products": products[:count],
        }

    @app.get("/products/{product_id}/offers")
    def product_offers(product_id: int):
        with connect_db(password) as conn:
            exists = conn.execute(
                "SELECT id FROM products WHERE id = %s",
                (product_id,),
            ).fetchone()

            if exists is None:
                raise HTTPException(status_code=404, detail="Product not found")

            rows = conn.execute(
                """
                SELECT o.id, o.store, o.seller, o.currency, o.url,
                       p.price, p.observed_at
                FROM offers AS o
                LEFT JOIN price_observations AS p
                    ON p.offer_id = o.id
                    AND p.observed_at = (
                        SELECT MAX(observed_at)
                        FROM price_observations
                        WHERE offer_id = o.id
                    )
                WHERE o.product_id = %s
                ORDER BY o.currency, p.price ASC NULLS LAST, o.id
                """,
                (product_id,),
            ).fetchall()

        return [
            {
                "offer_id": offer_id,
                "store": store,
                "seller": seller,
                "currency": currency,
                "url": url,
                "price": str(price) if price is not None else None,
                "observed_at": observed_at.isoformat() if observed_at else None,
            }
            for offer_id, store, seller, currency, url, price, observed_at in rows
        ]

    @app.post("/discover")
    def discover(req: Request, background_tasks: BackgroundTasks, q: str, category: str):
        try:
            options = {}
            if claim_online is not None:
                options["before_search"] = lambda: claim_online(req.state.visitor)
            rows = discover_products(q, category, password, api_key=serpapi_key, **options)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        can_index = (
            find_spec("torch") is not None and find_spec("transformers") is not None
        )
        if rows and can_index:
            product_ids = [row[0] for row in rows]
            background_tasks.add_task(index_missing_images, password, product_ids)

        return {"count": len(rows), "index_scheduled": bool(rows and can_index),
                "product_ids": [row[0] for row in rows]}

    return app


if __name__ == "__main__":
    from buywise_plus import main

    main()
