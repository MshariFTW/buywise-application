from functools import lru_cache
from getpass import getpass
from io import BytesIO
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from PIL import Image

from buywise_discover import connect_db


MODEL_NAME = "openai/clip-vit-base-patch32"
MAX_IMAGE_BYTES = 5_000_000
index_lock = Lock()


def approved_image(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host in {"serpapi.com", "gstatic.com"} or host.endswith(".gstatic.com")
    )


class ImageRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not approved_image(newurl):
            raise ValueError("Unsupported image host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


image_http = build_opener(ImageRedirects())


def download_image(url):
    if not approved_image(url):
        raise ValueError("Unsupported image host.")

    with image_http.open(url, timeout=20) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large.")

    with Image.open(BytesIO(data)) as image:
        return image.convert("RGB")


@lru_cache(maxsize=1)
def load_clip():
    import torch
    from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

    processor = CLIPImageProcessor.from_pretrained(MODEL_NAME)
    model = CLIPVisionModelWithProjection.from_pretrained(MODEL_NAME).eval()
    return torch, processor, model


def index_missing_images(password, product_ids=None):
    with index_lock:
        sql = """
            SELECT p.id, p.image_url
            FROM products AS p
            LEFT JOIN product_image_embeddings AS e
              ON e.product_id = p.id
              AND e.model_name = %s
              AND e.source_url = p.image_url
            WHERE e.product_id IS NULL
              AND p.image_url IS NOT NULL AND p.image_url <> ''
        """
        params = [MODEL_NAME]
        if product_ids is not None:
            sql += " AND p.id = ANY(%s)"
            params.append(product_ids)

        with connect_db(password) as conn:
            pending = conn.execute(sql + " ORDER BY p.id", params).fetchall()

        if not pending:
            print("Image index: nothing to update.")
            return 0

        torch, processor, model = load_clip()
        saved = 0

        for product_id, url in pending:
            try:
                image = download_image(url)
            except (OSError, ValueError) as error:
                print(f"Skipped image for product {product_id}: {error}")
                continue

            inputs = processor(images=image, return_tensors="pt")
            with torch.inference_mode():
                vector = model(**inputs).image_embeds[0].tolist()

            with connect_db(password) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO product_image_embeddings
                        (product_id, model_name, source_url, embedding)
                    SELECT id, %s, image_url, %s
                    FROM products
                    WHERE id = %s AND image_url = %s
                    ON CONFLICT (product_id) DO UPDATE
                    SET model_name = EXCLUDED.model_name,
                        source_url = EXCLUDED.source_url,
                        embedding = EXCLUDED.embedding
                    """,
                    (MODEL_NAME, vector, product_id, url),
                )
                saved += cursor.rowcount

        print(f"Image index: saved {saved} of {len(pending)} pending embeddings.")
        return saved


if __name__ == "__main__":
    index_missing_images(getpass("PostgreSQL password: "))