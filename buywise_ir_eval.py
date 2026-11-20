"""Build the paired, blinded rug evaluation from a frozen PostgreSQL snapshot."""

import argparse
import hashlib
import json
import random
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from buywise_api import photo
from buywise_db import connect_db, password_prompt
from buywise_retrieval import MODEL, REVISION, catalog, ranked, threshold

ROOT = Path(__file__).parent
PILOT = (
    "IKEA:20595698",
    "Home Centre:167945626",
    "IKEA:30580922",
    "Home Centre:168574057",
)
EVALUATION = (
    "IKEA:60497078",
    "IKEA:30600939",
    "IKEA:10593812",
    "IKEA:50555281",
    "Home Centre:165909719",
    "Home Centre:168494826",
    "Home Centre:167945644",
    "Home Centre:169881407",
)
K = 5


def photo_src(url, portable):
    filename = hashlib.sha256(url.encode()).hexdigest() + ".jpg"
    if portable:
        return "photos/" + filename if (ROOT / "photos" / filename).exists() else url
    return "/photo?url=" + quote(url, safe="")


def experiment(items, portable=False):
    by_key = {r.key: r for r in items.values()}
    missing = set(PILOT + EVALUATION) - by_key.keys()
    if missing:
        raise ValueError(
            "Restore and index the pilot first. Missing: " + ", ".join(sorted(missing))
        )
    pilot_designs = {by_key[k].design for k in PILOT}
    eval_designs = {by_key[k].design for k in EVALUATION}
    if pilot_designs & eval_designs or len(eval_designs) != len(EVALUATION):
        raise ValueError("Reference designs overlap")
    # Both methods receive exactly the same representative photograph/title per
    # candidate design. An explicit reference may use either verified size.
    candidates = {}
    for r in sorted(items.values(), key=lambda r: r.key):
        candidates.setdefault(r.design, r)
    candidates = {r.id: r for r in candidates.values()}
    cutoff = threshold()
    cases = []
    for key in EVALUATION:
        ref = by_key[key]
        methods = {m: list(ranked(candidates, ref, m))[:K] for m in ("title", "image")}
        pool = {r.key: r for results in methods.values() for r, _ in results}
        ids = sorted(pool)
        random.Random("BuyWise pooled presentation v1 " + key).shuffle(ids)
        # Measure the actual shop policy across all eligible variants.
        qualifying = sum(score > cutoff for _, score in ranked(items, ref, "image"))
        cases.append(
            dict(
                reference=key,
                title=ref.title,
                source=ref.image,
                photo=photo_src(ref.image, portable),
                pool=[
                    dict(
                        key=k,
                        title=pool[k].title,
                        source=pool[k].image,
                        photo=photo_src(pool[k].image, portable),
                    )
                    for k in ids
                ],
                top={m: [r.key for r, _ in results] for m, results in methods.items()},
                qualifying=qualifying,
            )
        )
    # Bind votes to the actual images/model, ranks, reference selection and cutoff.
    signature = [
        (
            r.key,
            r.design,
            r.image,
            r.title,
            hashlib.sha256(str(r.vector).encode()).hexdigest(),
        )
        for r in sorted(items.values(), key=lambda r: r.key)
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            [signature, EVALUATION, cutoff, K, [c["top"] for c in cases]]
        ).encode()
    ).hexdigest()[:20]
    return dict(
        id=fingerprint,
        k=K,
        cutoff=cutoff,
        model=MODEL,
        revision=REVISION,
        designs=len(candidates),
        listings=len(items),
        cases=cases,
    )


def render(items, portable=False):
    payload = json.dumps(experiment(items, portable), ensure_ascii=False).replace(
        "<", "\\u003c"
    )
    return (
        (ROOT / "evaluation_template.html")
        .read_text(encoding="utf-8")
        .replace("/*SNAPSHOT*/null", payload)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html",
        action="store_true",
        help="Write Judge_Rugs.html beside the bundled photos",
    )
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    with connect_db(password_prompt()) as conn:
        items = catalog(conn)
    if args.html:
        (ROOT / "Judge_Rugs.html").write_text(render(items, True), encoding="utf-8")
        return
    app = FastAPI()
    page = render(items)
    app.get("/", response_class=HTMLResponse)(lambda: page)
    app.get("/photo")(photo)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
