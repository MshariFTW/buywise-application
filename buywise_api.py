from getpass import getpass
from nltk.stem import PorterStemmer
from math import sqrt
from pathlib import Path
from fastapi.responses import FileResponse
import re
import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from buywise_discover import discover_products
from fastapi import BackgroundTasks
from buywise_image_index import index_missing_images
stemmer = PorterStemmer()


def search_terms(text):
    words = re.findall(r"\w+", text.lower())
    return [
        stemmer.stem(word) if word.isascii() and word.isalpha() else word
        for word in words
    ]
CATEGORY_TERMS = {
    "headphones": "headphones",
    "keyboards": "keyboard",
    "mice": "mouse mice",
}


def product_search_terms(title, category):
    terms = set(search_terms(title))
    terms.update(search_terms(CATEGORY_TERMS.get(category, "")))

    if re.search(
        r"\b(?:wireless|bluetooth)\b|لاسلك|بلوتوث|אלחוט",
        title,
        re.IGNORECASE,
    ):
        terms.update(search_terms("wireless"))

    return terms
def create_app(password, serpapi_key=""):
    app = FastAPI()

    @app.get("/price-history")
    def price_history(offer_id: int = 1):
        with psycopg.connect(
            host="localhost", port=5432, dbname="buywise",
            user="postgres", password=password,
        ) as conn:
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
    @app.get("/")
    def home():
        return FileResponse(
            Path(__file__).with_name("buywise_ui.html"),
            media_type="text/html",
        )

    @app.get("/search")
    def search_products(q: str):
        words = search_terms(q)
        if not words:
            return []

        with psycopg.connect(
            host="localhost", port=5432, dbname="buywise",
            user="postgres", password=password,
        ) as conn:
            products = conn.execute(
                "SELECT id, title, image_url, category FROM products"
            ).fetchall()

        index = {}
        catalog = {}

        for product_id, title, image_url, category in products:
            catalog[product_id] = {
                "id": product_id,
                "title": title,
                "image_url": image_url,
            }
            for word in product_search_terms(title,category):
                index.setdefault(word, set()).add(product_id)

        matches = index.get(words[0], set())
        for word in words[1:]:
            matches = matches & index.get(word, set())

        return [catalog[product_id] for product_id in sorted(matches)]
    @app.get("/products/{product_id}/similar")
    def similar_products(product_id: int):
        with psycopg.connect(
            host="localhost", port=5432, dbname="buywise",
            user="postgres", password=password,
        ) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.title, p.image_url, e.embedding
                FROM products AS p
                JOIN product_image_embeddings AS e ON e.product_id = p.id
                WHERE e.model_name = %s
                  AND e.source_url = p.image_url
                  AND p.category = (
                      SELECT category FROM products WHERE id = %s
                  )
                """,
                ("openai/clip-vit-base-patch32", product_id),
            ).fetchall()

        vectors = {row[0]: row[3] for row in rows}
        reference = vectors.get(product_id)

        if reference is None:
            raise HTTPException(status_code=404, detail="Image embedding not available")

        reference_norm = sqrt(sum(x * x for x in reference))
        results = []

        for other_id, title, image_url, vector in rows:
            if other_id == product_id:
                continue

            vector_norm = sqrt(sum(x * x for x in vector))
            if not reference_norm or not vector_norm:
                continue

            dot_product = sum(x * y for x, y in zip(reference, vector))
            score = dot_product / (reference_norm * vector_norm)

            results.append({
                "id": other_id,
                "title": title,
                "image_url": image_url,
                "similarity": round(score, 4),
            })

        results.sort(key=lambda item: item["similarity"], reverse=True)
        return results[:3]
    @app.get("/products/{product_id}/offers")
    def product_offers(product_id: int):
        with psycopg.connect(
            host="localhost", port=5432, dbname="buywise",
            user="postgres", password=password,
        ) as conn:
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
    def discover(background_tasks: BackgroundTasks, q: str, category: str):
        try:
            rows = discover_products(q, category, password, api_key=serpapi_key)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        if rows:
            product_ids = [row[0] for row in rows]
            background_tasks.add_task(index_missing_images, password, product_ids)

        return {"count": len(rows)}

    return app


if __name__ == "__main__":
    password = getpass("PostgreSQL password: ")
    serpapi_key = getpass("SerpApi key (optional): ").strip()
    uvicorn.run(create_app(password, serpapi_key), host="127.0.0.1", port=8001)