"""On-demand CPU inference for rug photos missing from the visual index."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.request import urlopen

from PIL import Image

from buywise_db import connect_db
from buywise_image_index import download_image
from buywise_retrieval import MODEL, ONLINE_REVISION, REVISION

MODEL_URL = (
    "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/"
    + ONLINE_REVISION + "/onnx/vision_model_quantized.onnx"
)
MODEL_DIGEST = "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"
index_lock = Lock()


@lru_cache(maxsize=1)
def encoder():
    import onnxruntime as ort

    directory = Path.home() / ".cache" / "buywise" / ONLINE_REVISION
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "vision.onnx"
    if not path.is_file():
        with NamedTemporaryFile(dir=directory, delete=False) as target:
            temporary = Path(target.name)
        try:
            digest = hashlib.sha256()
            with urlopen(MODEL_URL, timeout=45) as source, temporary.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != MODEL_DIGEST:
                raise ValueError("Unexpected image model checksum")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def image_embedding(image):
    import numpy as np

    width, height = image.size
    # Resize the short side to 224, then center crop as in the CLIP processor.
    size = (224 * width // min(width, height), 224 * height // min(width, height))
    image = image.resize(size, Image.Resampling.BICUBIC)
    left, top = (size[0] - 224) // 2, (size[1] - 224) // 2
    pixels = np.asarray(image.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255
    pixels -= np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    pixels /= np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    batch = pixels.transpose(2, 0, 1)[None]
    values = encoder().run(["image_embeds"], {"pixel_values": batch})[0][0]
    if values.shape != (512,) or not np.isfinite(values).all() or not values.any():
        raise ValueError("Invalid image embedding")
    return values.tolist()


def index_selected_rug(password, product_id):
    with index_lock:
        with connect_db(password) as conn:
            row = conn.execute("""
                SELECT p.image_url, e.product_id FROM products p
                LEFT JOIN product_image_embeddings e ON e.product_id=p.id
                  AND e.source_url=p.image_url AND e.model_name=%s
                  AND e.model_revision IN (%s,%s)
                WHERE p.id=%s AND p.category='rugs' AND starts_with(p.model_key,'google:')
            """, (MODEL, REVISION, ONLINE_REVISION, product_id)).fetchone()
        if not row or row[1] is not None:
            return
        url = row[0]
        image, digest = download_image(url)
        vector = image_embedding(image)
        with connect_db(password) as conn:
            conn.execute("""
                INSERT INTO product_image_embeddings
                    (product_id, model_name, model_revision, source_url, image_sha256, embedding)
                SELECT id,%s,%s,image_url,%s,%s FROM products WHERE id=%s AND image_url=%s
                ON CONFLICT (product_id) DO UPDATE SET model_name=EXCLUDED.model_name,
                    model_revision=EXCLUDED.model_revision, source_url=EXCLUDED.source_url,
                    image_sha256=EXCLUDED.image_sha256, embedding=EXCLUDED.embedding
                WHERE product_image_embeddings.source_url IS DISTINCT FROM EXCLUDED.source_url
                   OR product_image_embeddings.model_revision IS DISTINCT FROM %s
            """, (MODEL, ONLINE_REVISION, digest, vector, product_id, url, REVISION))
