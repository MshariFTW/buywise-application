# BuyWise

Find a patterned rug by example. Pick a catalog photograph, open **بدائل مشابهة**, and compare designs sold in Saudi Arabia by their dated SAR prices and dimensions.

The release includes 90 retailer listings, 89 design groups, 90 photographs, 90 precomputed CLIP vectors and 115 dated price observations. It runs locally with FastAPI and PostgreSQL. Ordinary search, visual alternatives, the saved list, price history, comparison and the evaluation worksheet work offline after setup.

## Publish on Render

[DEPLOY_AR.md](DEPLOY_AR.md) explains how the included Blueprint runs the FastAPI service on Render and connects it to hosted Neon PostgreSQL.

## Run on Windows

1. Extract the entire ZIP. Keep the files and the `photos` directory together.
2. Start PostgreSQL. The defaults are `localhost:5432`, user `postgres`, database `buywise`. Use Python **3.12** with the Windows Python launcher.
3. Double-click **Start_BuyWise.cmd**. It creates a local virtual environment, installs the core dependencies, then prompts privately for your PostgreSQL password. It imports the rug snapshot on first use. Existing products and shopping data are preserved. Repeated imports reuse the same observations.
4. Open **http://127.0.0.1:8002/shop** while the terminal is running. Press Ctrl+C in that terminal to stop.

Equivalent PowerShell commands from the extracted `BuyWise` folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe buywise_plus.py
```

If the service uses a different host, port, user or database, set `PGHOST`, `PGPORT`, `PGUSER` or `PGDATABASE` before starting. For example, set `$env:PGDATABASE = "buywise_rugs"` to create a separate project database. The account needs permission to create the database on first setup, or access to an existing database and its tables. The launcher targets the local machine. No external account or API key is needed for the bundled catalog.

## Explore the shop

- English opens by default. The language button switches between English and Arabic and remembers the selection on this browser.
- **Keyword search** accepts product titles and retailer names. **Browse by pattern** shows seven illustrated groups with counts. Tags are curated from the bundled product photos and keyed by stable listing IDs in `rug_patterns.json`. A rug may belong to several groups. New online finds enter keyword search immediately and can be tagged later.
- Search for **TELEGRAFLINJE** and select **Similar rugs**. The dialog opens at the closest range. Select **Broaden matches** to advance one level at a time. For this rug one click shows Ferra, Denali and Sahla. **Reset to closest** restores the starting range.
- Save two or three rugs to **My shortlist** to compare their sizes, shops and recorded prices. You can also save a budget, target price or note. **Price offers** links to the retailer and opens price history.
- **For retailers** opens from the top navigation beside **My shortlist**. The retailer form prepares a draft addressed to `buywise.rugs@gmail.com`. Review and send it in Gmail, or copy the request for another email service. Configure the recipient in `shop_settings.json`.

### Older headphone records

The shortlist displays saved rugs only. To permanently remove old database records after starting the updated shop once, open **Remove_Old_Headphones.cmd**. Enter the PostgreSQL password and inspect the list of headphone IDs and titles. Enter one ID or **ALL** to select the headphones shown, then type **DELETE** to confirm. This deletes the selected products, their saved notes, embeddings, offers and price observations in one transaction. Press Enter at either prompt to cancel.

## Retrieval

The shopper's keyword search applies a Boolean AND over normalized, accent-folded, Porter-stemmed title and retailer-name tokens plus general rug/category synonyms. The evaluation title baseline counts distinct shared processed title terms and excludes zero-overlap candidates. The two text methods serve different purposes.

The image encoder is pretrained OpenAI CLIP ViT-B/32, pinned to revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`. Each vector contains 512 image features. PostgreSQL stores the catalog ID, model, revision, source image URL, source-byte SHA-256 and vector. The shop reads eligible stored vectors, normalizes them, computes cosine similarity and ranks at full precision. It excludes the reference design, collapses verified variants and skips exact image duplicates. Ties use the stable product key. Source URL or model revision mismatches are excluded until reindexed. A retailer can also replace an image at an unchanged URL. The source-byte hash documents which image was encoded.

The shop keeps **every design with cosine strictly greater than the cutoff**, using the highest-ranked eligible variant as its representative. The score measures visual resemblance. To use a different threshold on a server restart:

```powershell
$env:BUYWISE_SIMILARITY = "0.88"
.\.venv\Scripts\python.exe buywise_plus.py
```

The default 0.90 and its pilot counts are documented in `VALIDATION.md`. Each click on **Broaden matches** moves to the next cutoff that adds designs from the ranked list. Repeated results at 0.88, 0.86 or 0.84 are skipped. The button shows the number of additions and disappears at the final distinct result set. The fixed-cutoff evaluation and the strict API retain their original policy. A single API call can override the default using `GET /products/{id}/similar?minimum=0.88`.

## Collection, provenance and updates

`rug_catalog.json` holds the exact seed records and vectors. `catalog_sources.csv` provides a spreadsheet-readable inventory of source URLs, selected retailer photographs, sizes, prices and timestamps. We collected structured product information directly from Saudi retailer pages. For Home Centre, the page's explicit sale price takes precedence over its regular price. The IKEA and Home Centre refreshers check the saved listing identity before recording a new reading. These observations can change after collection.

The four added Saudi storefronts contribute 65 products. ABYAT adds 24, Weft Rug adds 24, Zara Home adds 12 and CB2 adds 5. Every entry has a product URL, a dated SAR price for its selected size, a local photo and a CLIP embedding. Weft’s Arabic titles are preserved. Weft, Zara Home and CB2 entries were selected from in-stock variants when their pages were collected. ABYAT entries use the existing Saudi product observations. Stock and prices can change. The new retailers use stored observations and link to their product pages.

The two Mason listings show the same floral arrangement at different sizes and are grouped as one design. Their offer identities and histories stay separate. The gallery photographs show the rug pattern; several include furniture or part of the room. Each thumbnail links back to the full source via the catalog. For newly discovered designs, exact image matches are suppressed; other cross-retailer duplicates require verification before assigning a common `products.design_key`.

For one on-demand pass over supported, directly identified rug offers:

```powershell
.\.venv\Scripts\python.exe buywise_refresh_db.py
```

The refresh appends a reading only when the saved listing matches. A failed check leaves all earlier readings intact. The release starts no scheduler. The shopping workspace has no paid calls on startup or during ordinary search.

### Optional Google Shopping discovery

1. Install the indexing dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements-index.txt`. The first index build downloads the pinned CLIP weights from Hugging Face. Existing vectors work without PyTorch or a model download.
2. Close the running server and open **Start_BuyWise_Online.cmd**. Enter your SerpApi key at the hidden prompt. The launcher keeps it in process memory. Alternatively set `SERPAPI_KEY` locally before starting. The bundled catalog needs only the regular launcher.
3. In the shop, type **vintage patterned rug 160x230 cm** and use **Search online**. This is an explicit quota-consuming action. The Saudi Google Shopping request uses `gl=sa` and `hl=en`. Cached queries are reused for one day. New eligible images can be indexed in the background. A returned shopping item may lack a usable image, a store-detail token or a qualifying exact-variant offer.
4. For a token-backed rug, open its offers and explicitly request a store scan. Strict title, size, listing, seller, currency and price checks may leave the product without a price. The provider coverage still requires a live check with your key.

The equivalent commands for a one-query local check and indexing are:

```powershell
.\.venv\Scripts\python.exe buywise_discover.py "traditional patterned rug" rugs
.\.venv\Scripts\python.exe buywise_image_index.py
```

A cached token can expire. A changed or ambiguous seller, size or currency must not be merged into a prior history. `buywise_refresh_db.py --serpapi` explicitly opts into provider calls for stored token-backed offers.

## Academic evaluation

After extracting the ZIP, **open Judge_Rugs.html** in a browser. It contains the frozen experiment and loads its images from `photos`. No server is required. The expanded catalog produces 71 pooled pairs for eight references. The previous worksheet and your completed report are preserved in Judge_Rugs_baseline.html and BuyWise_IR_results_baseline.md. The four threshold-selection references are separate.

Mark each candidate by motif and layout, with colour secondary. The worksheet hides method and rank during judging. **Save judgments** exports a backup you can resume with the same snapshot. Once all eight references are complete, **Download scored report** exports both mean Precision@5 values, per-reference results, the empty-result frequency, and judged examples for discussing successes and failures. Each missing rank contributes zero. The included baseline report records title 0.275 and image 0.375 on the original 25 listings. The portable expanded worksheet starts blank. The completed expanded pilot reported by the assessor scored title 0.275 and image 0.400.

To regenerate the worksheet after changing the catalog or cutoff, run `python buywise_ir_eval.py --html` using the project environment. Running it without `--html` opens the evaluator service at port 8003. The released and regenerated evaluations use one stable catalog representative per candidate design for both methods, pool the top five before thresholding, and use the shopper's full variant-aware ranking for empty-result counts.

## Reproduce, export and package

- `python -m unittest discover -s tests -v` runs the pure tests. Set `$env:BUYWISE_TEST_DB = "1"` to include PostgreSQL integration tests. They create and remove their own uniquely named test database. See `VALIDATION.md` for the executed checks.
- `python restore_demo.py` merges the bundled rug snapshot. The original filename is retained for convenience. Existing IDs are resolved through stable product and offer keys.
- `python buywise_export.py my_rugs.json` exports rug records, eligible vectors and SAR observations. It omits account details, discovery tokens, notes and saved purchases.
- `python package_release.py BuyWise_Rugs.zip` builds a release using an explicit file list. Use a new output filename. The standard package includes the bundled retailer thumbnails. `--links-only` produces a smaller package using the original image URLs. The repository ignores local environments, secrets, price-probe output, judgments and local exports. The hosting source includes the bundled catalog thumbnails. Retailers are credited per listing; the code and retailer photography have separate provenance.

Model: https://huggingface.co/openai/clip-vit-base-patch32/tree/3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268  
Provider formats: https://serpapi.com/shopping-results and https://serpapi.com/google-immersive-product-api
