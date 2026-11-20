"""Export portable rug data and observation history without credentials or discovery tokens."""

import argparse
import json
from pathlib import Path
from buywise_db import connect_db, password_prompt
from buywise_retrieval import MODEL, REVISION


def export(password):
    with connect_db(password) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        products = conn.execute("""SELECT id, model_key, title, image_url, COALESCE(design_key,image_url), size_label
                                   FROM products WHERE category='rugs' ORDER BY model_key""").fetchall()
        items = []
        for pid, key, title, image, design, size in products:
            item = dict(
                key=key,
                title=title,
                image_url=image,
                design=design,
                size=size,
                offers=[],
            )
            row = conn.execute(
                """SELECT embedding,image_sha256 FROM product_image_embeddings WHERE product_id=%s
                     AND model_name=%s AND model_revision=%s AND source_url=%s""",
                (pid, MODEL, REVISION, image),
            ).fetchone()
            if row:
                item["embedding"] = dict(
                    model=MODEL, revision=REVISION, vector=row[0], image_sha256=row[1]
                )
            for oid, store, seller, listing, url, ad, size_label, color in conn.execute(
                """
                SELECT id,store,seller,listing_key,url,advertised_title,size_label,color_label
                FROM offers WHERE product_id=%s AND currency='SAR' ORDER BY id""",
                (pid,),
            ):
                history = [
                    [at.isoformat(), str(price)]
                    for at, price in conn.execute(
                        "SELECT observed_at,price FROM price_observations WHERE offer_id=%s ORDER BY observed_at",
                        (oid,),
                    )
                ]
                item["offers"].append(
                    dict(
                        store=store,
                        seller=seller,
                        listing=listing,
                        url=url,
                        title=ad,
                        size=size_label,
                        color=color,
                        currency="SAR",
                        history=history,
                    )
                )
            items.append(item)
    return {"format": 2, "rugs": items}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(
            export(password_prompt()), f, ensure_ascii=False, indent=1, allow_nan=False
        )
