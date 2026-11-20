"""Import the portable rug catalog into a new or existing local BuyWise database."""

import json
import math
from decimal import Decimal
from pathlib import Path
from buywise_db import connect_db, create_database, migrate, password_prompt
from buywise_retrieval import MODEL, REVISION
from buywise_image_index import approved_image

SNAPSHOT = Path(__file__).with_name("rug_catalog.json")


def restore(snapshot, password, *, create=True):
    if snapshot.get("format") != 2:
        raise ValueError("Expected a version 2 rug snapshot")
    if create:
        create_database(password)
    with connect_db(password) as conn:
        migrate(conn)
        for item in snapshot["rugs"]:
            if not approved_image(item["image_url"]):
                raise ValueError("Unsupported catalog image")
            row = conn.execute(
                """
                INSERT INTO products (model_key,title,image_url,category,design_key,size_label)
                VALUES (%s,%s,%s,'rugs',%s,%s)
                ON CONFLICT (model_key) DO NOTHING RETURNING id
            """,
                (
                    item["key"],
                    item["title"],
                    item["image_url"],
                    item["design"],
                    item["size"],
                ),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id FROM products WHERE model_key=%s AND category='rugs' AND image_url=%s AND title=%s",
                    (item["key"], item["image_url"], item["title"]),
                ).fetchone()
            if not row:
                continue
            pid = row[0]
            emb = item.get("embedding")
            if emb:
                vector = emb["vector"]
                if (
                    emb["model"] != MODEL
                    or emb["revision"] != REVISION
                    or len(vector) != 512
                    or not all(math.isfinite(x) for x in vector)
                    or not math.hypot(*vector)
                ):
                    raise ValueError("Invalid catalog embedding")
                conn.execute(
                    """
                    INSERT INTO product_image_embeddings(product_id,model_name,model_revision,source_url,image_sha256,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (product_id) DO NOTHING
                """,
                    (
                        pid,
                        MODEL,
                        REVISION,
                        item["image_url"],
                        emb["image_sha256"],
                        vector,
                    ),
                )
            for offer in item["offers"]:
                if offer["currency"] != "SAR":
                    raise ValueError("Expected a SAR offer")
                fields = (offer["store"], offer["seller"], offer["listing"], "SAR")
                conn.execute(
                    """
                    INSERT INTO offers(product_id,store,seller,listing_key,currency,url,page_token,advertised_title,size_label,color_label)
                    VALUES (%s,%s,%s,%s,%s,%s,'',%s,%s,%s) ON CONFLICT DO NOTHING
                """,
                    (
                        pid,
                        *fields,
                        offer["url"],
                        offer["title"],
                        offer["size"],
                        offer["color"],
                    ),
                )
                row = conn.execute(
                    """SELECT id FROM offers WHERE product_id=%s AND store=%s AND seller=%s
                    AND listing_key=%s AND currency=%s AND size_label IS NOT DISTINCT FROM %s
                    AND color_label IS NOT DISTINCT FROM %s AND advertised_title IS NOT DISTINCT FROM %s""",
                    (pid, *fields, offer["size"], offer["color"], offer["title"]),
                ).fetchone()
                if row:
                    for at, price in offer["history"]:
                        conn.execute(
                            "INSERT INTO price_observations(offer_id,observed_at,price) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (row[0], at, Decimal(price)),
                        )
    return len(snapshot["rugs"])


if __name__ == "__main__":
    print(
        "Processed",
        restore(json.loads(SNAPSHOT.read_text(encoding="utf-8")), password_prompt()),
        "rug records.",
    )
