# BuyWise release validation

**20 November 2026 · Saudi Arabia pilot**

## Free hosting release

All 22 tests passed with PostgreSQL. The hosted checks covered per-browser shortlist isolation, same-origin writes, cached provider results and server-side request limits. The startup command served the catalog on its assigned port using a pre-provisioned PostgreSQL database and a user without permission to create databases. A restart preserved all 90 listings and did not duplicate the seed import. These checks used a local database and a SerpApi fixture.

## Result-aware widening and retailer contact

Across all 90 listings, each available expansion preserved the current ranked prefix and added at least one design. ELSYSTEM returned 2, 3, 8 and 13 designs. TELEGRAFLINJE returned 0 and 3. Three listings had just one distinct result set. The shop integration check also exercised dialog dismissal, focus restoration, in-place widening, English/Arabic retailer form validation, prepared Gmail fields and copying the contact details. Gmail delivery and a Windows visual inspection were outside this test.

## Bilingual shopping interface

The PostgreSQL suite passed 21 tests. It covers the seven visually curated groups, their representative photos and counts, the manually selected similarity levels, strict API compatibility, online batch product IDs, and configuration that exposes only the merchant address and key-presence flag. All 90 bundled listings have at least one visual tag. Legacy headphone saves are excluded from the shortlist. Optional cleanup was tested on a disposable headphone with a price observation and a saved entry, alongside the full rug catalog.

An additional interface test exercised the shop JavaScript against the running FastAPI service and PostgreSQL. It checked English and Arabic text direction, all seven pattern filters, the three broad matches for TELEGRAFLINJE, the no-key online-search message, shortlist edits, comparison, budget and price history. Paid provider responses were exercised with fixtures. A live account search still requires the owner’s SerpApi key. The frozen evaluation measures a fixed cutoff independently of manual widening in the shopper interface.

## Expanded release · 20 November 2026

| Store | Pictured, priced and indexed rug listings |
|---|---:|
| IKEA Saudi Arabia | 11 |
| Home Centre Saudi Arabia | 14 |
| ABYAT Saudi Arabia | 24 |
| Weft Rug | 24 |
| Zara Home Saudi Arabia | 12 |
| CB2 Saudi Arabia | 5 |
| Total | 90 |

The expanded snapshot contains 89 design groups and 115 timestamped price readings. All 65 additions have a real retailer photograph and a 512-dimensional CLIP vector computed with the original model revision. ABYAT photos with transparent backgrounds are composited on white. Zara Home uses product gallery images showing the overall pattern. The new stores provide dated offers with exact sizes and product links. Once the snapshot is imported, their prices and photos are available offline.

The expanded worksheet ID is `e720eb87a19e48c2a702`. Three of the eight original reference queries returned no designs at the strict shop cutoff of 0.90. The expanded worksheet contains 71 pooled pairs, with assessor-reported mean Precision@5 of 0.275 for titles and 0.400 for images. The completed 25-listing pilot report and original worksheet are included separately.

All 16 Python tests passed with PostgreSQL integration enabled. The four-store test checked every new listing through the search, local photo and offer endpoints and confirmed membership in the visual index. A repeated seed import preserved counts. A DOM-based shop check exercised the real HTTP server, displayed all 90 results, changed the threshold and exercised saving, budgeting, comparison, history and the new worksheet. The tests ran on Linux. The Windows launcher was inspected and its entry point was exercised on Linux.

## Original 25-listing pilot · catalog and acquisition

| Check | Observed result |
|---|---:|
| Live Saudi retailer listing pages parsed | 25 |
| IKEA listings / Home Centre listings | 11 / 14 |
| Unique design groups | 24 |
| Retailer photographs encoded with pretrained CLIP | 25 |
| Stored image features per listing | 512 |
| Offer records with explicit SAR price, size, seller and source | 25 |
| Initial price observations / separate successful refreshes | 25 / 25 |
| Offers whose price changed on the second check | 0 |

The two reads occurred between 08:24 and 08:39 Riyadh time on the same day. The history demonstrates repeated observations over that interval. It cannot establish a price trend. The CSV and snapshot contain exact per-offer times. We selected full-pattern views from retailer galleries and grouped the two Mason sizes after comparing the repeated floral arrangement and border. Other seed products retain separate design keys.

The first release uses the retailer-page acquisition route. Paid Saudi Google Shopping and Immersive Product calls were not run. Provider parsing, query caching, offer scans, wrong-size rejection, and offline behavior were exercised using labeled test fixtures. Real search coverage, token availability, merchant coverage and quotas for a specific SerpApi account remain unmeasured.

## Threshold pilot

The four development references span ornamented, geometric and printed designs. For this pilot we chose the highest tested cutoff that kept at least one alternative for each development query. This rule balances returned-list size against coverage; it does not estimate relevance or optimize Precision@5. The four references were excluded from the eight final evaluation queries.

| Pilot reference | >0.80 | >0.86 | >0.88 | >0.90 | >0.92 | >0.94 |
|---|---:|---:|---:|---:|---:|---:|
| IKEA JÄRNVÄG | 15 | 10 | 6 | 3 | 2 | 1 |
| Home Centre Genelle | 17 | 12 | 12 | 8 | 7 | 2 |
| IKEA ELSYSTEM | 9 | 2 | 1 | 1 | 0 | 0 |
| Home Centre Mason Printed | 17 | 12 | 11 | 6 | 2 | 0 |

At the selected **0.90**, the shop returned 8 Genelle alternatives. JÄRNVÄG's nearest result was ONSEVIG (cosine approximately 0.949). A less common geometry can produce no matches. The catalog is small and photography differs by retailer, so transfer to new stock requires fresh calibration.

## Frozen evaluation, before human assessment

| Reference | Candidates to judge | Designs above 0.90 |
|---|---:|---:|
| ONSEVIG | 7 | 3 |
| VÄNDPLATS | 8 | 0 |
| STOCKHOLM 2025 | 6 | 0 |
| TELEGRAFLINJE | 8 | 0 |
| Helena | 8 | 5 |
| Veluna Printed | 8 | 7 |
| Jolie | 9 | 0 |
| Noctis Calista | 8 | 1 |

Snapshot identifier: `0b5986990bef8956ad13`. The shop produced no qualifying alternative on **4/8 queries (50%)**. Both research methods still supplied their unthresholded ranked candidates. This yielded 62 pooled reference–candidate pairs. The denominator for each method's Precision@5 is five, even when positions are unfilled. The subsequently completed baseline report records mean Precision@5 of 0.275 for title and 0.375 for image. See BuyWise_IR_results_baseline.md.

## Original pilot engineering checks

| Check | Result |
|---|---|
| Automated Python suite | 15 passed with the PostgreSQL integration group enabled |
| Fresh setup and additive legacy migration | Existing accessory product survived. Rug lookup excluded accessories. Repeated restore did not duplicate offers or observations |
| Retrieval contract | Distinct stemmed overlap, diacritic handling, deterministic ranking, variant exclusion, stale-source suppression, strict unrounded cutoff, more than three matches and a true empty result passed |
| Index reproducibility | Removed two embeddings in a disposable database, downloaded the real source photos again, encoded them via the application indexer with the pinned CLIP weights, and compared every component. Maximum absolute difference was 0.0 for both. A second index pass wrote zero rows |
| Offer safety | A changed size, seller, currency, listing ID, an ambiguous price, and a failed fetch were rejected. Relevant integration checks confirmed history stayed intact |
| Live retailer refresh | 25/25 direct offers were checked again successfully. Exact SKU, title, size, colour and retailer matched |
| HTTP and interface interaction | The actual FastAPI server and PostgreSQL were exercised from the shop JavaScript in jsdom. Search, eight Genelle alternatives, saving, editing, budgeting, selecting two rugs, comparing dimensions and prices, and viewing history passed |
| Evaluation worksheet | Tested completion guard, pooling, label entry and score generation with disposable synthetic clicks. These labels were discarded |
| Release restore | Source links and model metadata survived export and restore. The complete project includes local thumbnails for offline inspection |

The test host ran Linux, Python 3.12, PostgreSQL 16.2 and CPU PyTorch 2.6.0. A normal Chromium process could not start on this host, so the UI interaction check used a DOM implementation. Physical Windows startup and browser layout were not visually verified here.
