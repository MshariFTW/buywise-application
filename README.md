# BuyWise — Purchase Planning and Product Retrieval

CSC484 Information Retrieval project. This release focuses on computer
peripherals: headphones, keyboards, and mice when catalog data is available.
It brings product search, image-based alternatives, saved products, budget,
comparison, and observed store prices into one local workflow.

## Run the demo on a fresh setup

Start PostgreSQL on localhost:5432 with a `postgres` account. In the unpacked
project folder, using a Python environment compatible with the source machine:

```powershell
python -m pip install -r requirements.txt
python ./restore_demo.py
python ./buywise_plus.py
```

Enter the local PostgreSQL password when prompted. Leave the SerpApi key
blank for the saved demo. Open http://127.0.0.1:8002/shop. If BuyWise is
already installed with data, skip `restore_demo.py` and use the existing
database. The restore script does not overwrite a populated catalog.

## Demo flow

1. Search for `wireless keyboard`. Compare with `keyboard` to see the effect
   of the additional term and attribute normalization.
2. On a product with a stored embedding, open similar alternatives. The
   system compares CLIP image embeddings with cosine similarity inside the
   product category.
3. Save products and compare two or three in the purchase list. Show the
   target price, budget, notes, and the latest recorded prices where available.
4. Search for `Sony WH-CH520` (if present) and open its offers and price history.

## Snapshot and evaluation

The sample contains 23 products, 23
image embeddings, 2 offers, and 8 price
observations. These are saved observations, not current quotes. The archive
omits API keys, discovery and refresh tokens, and personal shopping lists.
Images are linked rather than embedded and may require internet access.

Text retrieval uses tokenization, English stemming, attribute enrichment,
and Boolean AND. Image retrieval uses the stored embeddings. To run the
evaluation, start `python ./buywise_ir_eval.py` and open
http://127.0.0.1:8003/. Mark the results and export the English report.
No measured evaluation report is bundled. Run the evaluator and complete the relevance judgments.

## Scope

Live discovery requires a user-provided SerpApi key. The saved demo uses
existing embeddings; indexing newly discovered images additionally requires
PyTorch, torchvision, and transformers. Product review retrieval and
Telegram alerts are outside this release. No automatic price scheduler is
enabled. The project does not claim broad market coverage or live price
accuracy.
