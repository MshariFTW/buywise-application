"""BuyWise Plus: adds a shopping workspace to the existing local BuyWise app.

Place this file beside buywise_api.py and run: python buywise_plus.py
This launcher uses the existing catalog, search, image index and price history.
"""
from decimal import Decimal, InvalidOperation
from getpass import getpass
from threading import Lock

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from buywise_api import create_app
from buywise_discover import connect_db
from buywise_probe import fetch_offers


PORT = 8002
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS buywise_saved_items (
        product_id BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
        target_price NUMERIC(12, 2) CHECK (target_price > 0),
        note TEXT NOT NULL DEFAULT '',
        saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS buywise_offer_scans (
        product_id BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        saved_count INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL
    )
    """,
    """
    ALTER TABLE offers ADD COLUMN IF NOT EXISTS advertised_title TEXT
    """,
)


class SavedItem(BaseModel):
    target_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    note: str = Field(default="", max_length=500)


def install_schema(password):
    with connect_db(password) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


def choose_offers(candidates):
    """Keep a listing only when the provider gives it one usable price."""
    grouped = {}
    for offer in candidates:
        fields = tuple(offer.get(key) for key in ("store", "seller", "listing", "currency"))
        if not all(fields) or fields[-1] != "SAR":
            continue
        try:
            price = Decimal(str(offer.get("price")))
        except InvalidOperation:
            continue
        if not price.is_finite() or not 0 < price < Decimal("10000000000"):
            continue
        grouped.setdefault(fields, {})[price] = offer
    return [(next(iter(prices.values())), next(iter(prices)))
            for prices in grouped.values() if len(prices) == 1], sum(
                len(prices) != 1 for prices in grouped.values()
            )


def iso(value):
    return value.isoformat() if value is not None else None


def money(value):
    return str(value) if value is not None else None


def build_app(password, api_key):
    app = create_app(password, api_key)
    if not isinstance(app, FastAPI):
        raise RuntimeError("buywise_api.create_app() must return the FastAPI app.")
    scan_lock = Lock()

    @app.middleware("http")
    async def check_write_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected_origins = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin not in expected_origins
            ):
                return JSONResponse({"detail": "الطلب لازم يجي من صفحة BuyWise المحلية."}, 403)
        return await call_next(request)

    @app.get("/shop", response_class=HTMLResponse, include_in_schema=False)
    def shop():
        return PAGE

    @app.get("/shop/api/product-info")
    def product_info(ids: list[int] = Query()):
        if len(ids) > 200 or any(item < 1 for item in ids):
            raise HTTPException(400, "أرقام المنتجات غير صالحة.")
        with connect_db(password) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.category, NULLIF(p.discovery_token, '') IS NOT NULL,
                       s.product_id IS NOT NULL
                FROM products AS p
                LEFT JOIN buywise_saved_items AS s ON s.product_id = p.id
                WHERE p.id = ANY(%s)
                """, (ids,),
            ).fetchall()
        return [
            {"id": item_id, "category": category, "can_scan": can_scan, "saved": saved}
            for item_id, category, can_scan, saved in rows
        ]

    @app.get("/shop/api/products/{product_id}/offers")
    def offers_for(product_id: int):
        with connect_db(password) as conn:
            if not conn.execute("SELECT id FROM products WHERE id = %s", (product_id,)).fetchone():
                raise HTTPException(404, "المنتج غير موجود.")
            rows = conn.execute(
                """
                SELECT o.id, o.store, o.seller, o.currency, o.url, o.advertised_title,
                       last_reading.price, last_reading.observed_at
                FROM offers AS o
                LEFT JOIN LATERAL (
                    SELECT price, observed_at
                    FROM price_observations
                    WHERE offer_id = o.id
                    ORDER BY observed_at DESC
                    LIMIT 1
                ) AS last_reading ON TRUE
                WHERE o.product_id = %s
                ORDER BY o.currency, last_reading.price ASC NULLS LAST, o.id
                """, (product_id,),
            ).fetchall()
        return [
            {"id": item_id, "store": store, "seller": seller, "currency": currency,
             "url": url, "advertised_title": title, "price": money(price),
             "observed_at": iso(observed_at)}
            for item_id, store, seller, currency, url, title, price, observed_at in rows
        ]

    @app.post("/shop/api/products/{product_id}/scan-offers")
    def scan_offers(product_id: int):
        with scan_lock:
            with connect_db(password) as conn:
                item = conn.execute(
                    "SELECT discovery_token FROM products WHERE id = %s", (product_id,)
                ).fetchone()
                if item is None:
                    raise HTTPException(404, "المنتج غير موجود.")
                cached = conn.execute(
                    """
                    SELECT saved_count, skipped_count, checked_at
                    FROM buywise_offer_scans
                    WHERE product_id = %s AND checked_at > NOW() - INTERVAL '1 day'
                    """, (product_id,),
                ).fetchone()
            if cached is not None:
                return {"saved": cached[0], "skipped": cached[1],
                        "checked_at": iso(cached[2]), "cached": True}
            if not item[0]:
                raise HTTPException(409, "هذا المنتج ما له بيانات جلب. تقدر تشوف عروضه المحفوظة.")
            if not api_key:
                raise HTTPException(503, "الجلب الجديد يحتاج مفتاح SerpApi عند تشغيل السيرفر.")
            try:
                candidates, _ = fetch_offers(api_key, item[0])
            except (ValueError, OSError) as exc:
                raise HTTPException(502, "تعذّر جلب العروض الآن. راجع اتصال SerpApi والمفتاح.") from exc

            selected, skipped = choose_offers(candidates)
            saved = 0
            with connect_db(password) as conn:
                for offer, price in selected:
                    result = conn.execute(
                        """
                        INSERT INTO offers
                            (product_id, store, seller, listing_key, url, page_token,
                             currency, advertised_title)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (store, seller, listing_key, currency) DO UPDATE
                        SET url = EXCLUDED.url, page_token = EXCLUDED.page_token,
                            advertised_title = EXCLUDED.advertised_title
                        WHERE offers.product_id = EXCLUDED.product_id
                        RETURNING id
                        """,
                        (product_id, offer["store"], offer["seller"], offer["listing"],
                         offer["url"], item[0], offer["currency"], offer.get("title")),
                    ).fetchone()
                    if result is None:
                        skipped += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO price_observations (offer_id, observed_at, price)
                        VALUES (%s, NOW(), %s)
                        ON CONFLICT (offer_id, observed_at) DO NOTHING
                        """, (result[0], price),
                    )
                    saved += 1
                checked_at = conn.execute(
                    """
                    INSERT INTO buywise_offer_scans (product_id, saved_count, skipped_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (product_id) DO UPDATE
                    SET checked_at = NOW(), saved_count = EXCLUDED.saved_count,
                        skipped_count = EXCLUDED.skipped_count
                    RETURNING checked_at
                    """, (product_id, saved, skipped),
                ).fetchone()[0]
            return {"saved": saved, "skipped": skipped,
                    "checked_at": iso(checked_at), "cached": False}

    @app.get("/shop/api/list")
    def list_items():
        with connect_db(password) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.title, p.image_url, p.category,
                       NULLIF(p.discovery_token, '') IS NOT NULL,
                       s.target_price, s.note, best.price, best.store, best.observed_at
                FROM buywise_saved_items AS s
                JOIN products AS p ON p.id = s.product_id
                LEFT JOIN LATERAL (
                    SELECT last_reading.price, o.store, last_reading.observed_at
                    FROM offers AS o
                    CROSS JOIN LATERAL (
                        SELECT price, observed_at
                        FROM price_observations
                        WHERE offer_id = o.id
                        ORDER BY observed_at DESC
                        LIMIT 1
                    ) AS last_reading
                    WHERE o.product_id = p.id AND o.currency = 'SAR'
                    ORDER BY last_reading.price, last_reading.observed_at DESC, o.id
                    LIMIT 1
                ) AS best ON TRUE
                ORDER BY s.saved_at DESC, p.id
                """
            ).fetchall()
        return [
            {"id": item_id, "title": title, "image_url": image, "category": category,
             "can_scan": can_scan, "saved": True, "target_price": money(target),
             "note": note, "latest_price": money(price), "store": store,
             "observed_at": iso(observed_at)}
            for item_id, title, image, category, can_scan, target, note, price, store, observed_at
            in rows
        ]

    @app.post("/shop/api/list/{product_id}")
    def save_item(product_id: int):
        with connect_db(password) as conn:
            if not conn.execute("SELECT id FROM products WHERE id = %s", (product_id,)).fetchone():
                raise HTTPException(404, "المنتج غير موجود.")
            conn.execute(
                """
                INSERT INTO buywise_saved_items (product_id) VALUES (%s)
                ON CONFLICT (product_id) DO NOTHING
                """, (product_id,),
            )
        return {"saved": True}

    @app.put("/shop/api/list/{product_id}")
    def edit_item(product_id: int, item: SavedItem):
        with connect_db(password) as conn:
            cursor = conn.execute(
                """
                UPDATE buywise_saved_items SET target_price = %s, note = %s
                WHERE product_id = %s
                """, (item.target_price, item.note.strip(), product_id),
            )
            if not cursor.rowcount:
                raise HTTPException(404, "المنتج غير موجود في القائمة.")
        return {"saved": True}

    @app.delete("/shop/api/list/{product_id}")
    def remove_item(product_id: int):
        with connect_db(password) as conn:
            conn.execute("DELETE FROM buywise_saved_items WHERE product_id = %s", (product_id,))
        return {"saved": False}

    return app


def main():
    password = getpass("PostgreSQL password: ")
    api_key = getpass("SerpApi key (optional): ").strip()
    app = build_app(password, api_key)
    install_schema(password)
    print(f"BuyWise Plus: http://127.0.0.1:{PORT}/shop", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT)


PAGE = r'''
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuyWise Plus</title>
<style>
:root{color-scheme:light;--ink:#152929;--muted:#556e69;--line:#dbe6e3;--bg:#f3f7f5;--white:#fff;--primary:#12624e;--soft:#e7f3ed;--amber:#fff0d9}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Segoe UI",Arial,sans-serif}
main{width:min(1160px,100%);margin:auto;padding:24px 24px 56px}
header{background:linear-gradient(122deg,#133c35,#1b594a);color:#fff;border-radius:24px;padding:27px 30px;display:flex;flex-wrap:wrap;gap:22px;align-items:center;justify-content:space-between}
.brand{font-size:34px;font-weight:760;letter-spacing:-.04em;line-height:1.1;direction:ltr;text-align:right}
header p{margin:.5em 0 0;color:#d7ede3}
nav{display:flex;flex-wrap:wrap;gap:8px}
button,input,select,textarea{font:inherit}
button{cursor:pointer;background:var(--white);border:1px solid #c3d7cf;border-radius:10px;padding:8px 12px;color:var(--primary);font-weight:650}
button:hover{background:var(--soft)}
button:disabled{opacity:.55;cursor:wait}
button.primary,nav button[aria-pressed="true"]{background:var(--primary);border-color:var(--primary);color:white}
nav button[aria-pressed="true"]{background:#bce7d5;color:#123e34;border-color:#bce7d5}
nav button{border-color:#a6c9bc}
button.ghost{border-color:transparent;background:transparent;color:var(--muted)}
input,select,textarea{max-width:100%;background:white;border:1px solid #c3d7cf;color:var(--ink);border-radius:10px;padding:8px 11px}
:focus-visible{outline:3px solid #369f87;outline-offset:3px}
.panel{margin-top:18px;background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}
.toolbar,.actions,.search-row,.budget-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.search-row input{flex:1;min-width:160px}
.toolbar{margin-top:12px}
h1,h2,h3,h4,p{margin:0}
h2{font-size:22px}
h3{font-size:15px;line-height:1.45;overflow-wrap:anywhere}
h4{font-size:15px}
.hint,.status,.small{color:var(--muted);font-size:13px}
.status:empty{display:none}
.status{padding:6px 0}
.section-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin:17px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:12px}
.card{padding:14px;background:#fff;border:1px solid var(--line);border-radius:15px;display:grid;align-content:start;gap:10px;min-width:0}
.image{height:125px;border-radius:12px;background:#f7faf8;display:grid;place-items:center;overflow:hidden}
.image img{width:100%;height:125px;object-fit:contain;mix-blend-mode:multiply}
.placeholder{color:var(--muted)}
.badge{display:inline-flex;align-items:center;border-radius:999px;background:var(--soft);color:#164d40;font-size:12px;padding:3px 9px;max-width:max-content}
.score{font-size:13px;color:var(--muted)}
.cards-title{display:grid;gap:5px}
.card .actions button{font-size:13px;padding:5px 9px}
.summary{padding:12px;background:var(--soft);border-radius:12px}
.saved-fields{display:grid;gap:8px}
.saved-fields label{display:grid;gap:3px;font-size:13px}
textarea{resize:vertical;min-height:65px}
.offer{padding:12px;border:1px solid var(--line);border-radius:12px;display:grid;gap:5px}
.offer+.offer{margin-top:7px}
.price{color:var(--primary);font-weight:750}
.budget-row input{width:125px}
#budget-status{margin-top:5px}
a{color:var(--primary)}
.history{display:grid;gap:4px;padding:6px 0}
.history p{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.notice{background:var(--amber);padding:9px 12px;border-radius:9px;margin:8px 0}
.compare-pick[aria-pressed="true"]{background:var(--soft);border-color:var(--primary)}
.table-scroll{overflow-x:auto}
.compare-table{width:100%;border-collapse:collapse;text-align:start}
.compare-table th,.compare-table td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:start;vertical-align:top;overflow-wrap:anywhere}
.compare-table th[scope="row"]{min-width:120px;color:var(--muted);font-size:13px}
.compare-table thead th{min-width:150px;background:#f7faf8}
.compare-table .best{background:var(--soft);color:var(--primary);font-weight:700}
[hidden]{display:none!important}
@media(max-width:520px){main{padding:10px}header{padding:20px;border-radius:15px}.panel{padding:12px}.grid{grid-template-columns:1fr}.brand{font-size:29px}}
</style>
</head>
<body>
<main>
  <header>
    <div><h1 class="brand">BuyWise Plus</h1><p>اكتشف خياراتك، وقارن الأسعار، واحفظ ما يناسب ميزانيتك.</p></div>
    <nav aria-label="أقسام BuyWise">
      <button type="button" id="search-tab" aria-pressed="true">اكتشف المنتجات</button>
      <button type="button" id="list-tab" aria-pressed="false">قائمة مشترياتي</button>
    </nav>
  </header>

  <section id="search-view">
    <section class="panel" aria-label="البحث">
      <form id="search-form" class="search-row">
        <input id="q" aria-label="عبارة البحث" placeholder="مثل wireless keyboard" value="headphones" dir="auto" maxlength="120" required>
        <button type="submit" class="primary">بحث</button>
      </form>
      <div class="toolbar">
        <label for="category">فئة الجلب:</label>
        <select id="category">
          <option value="headphones">سماعات</option>
          <option value="keyboards">كيبوردات</option>
          <option value="mice">ماوسات</option>
        </select>
        <button type="button" id="discover">جلب منتجات إضافية</button>
        <span class="hint">البحث العادي يستخدم الكتالوج المحفوظ.</span>
      </div>
      <p id="discover-status" class="status" role="status"></p>
    </section>
    <div class="section-head"><h2>نتائج البحث</h2><p id="search-status" class="status" role="status"></p></div>
    <div id="results" class="grid"></div>
  </section>

  <section id="list-view" hidden>
    <section class="panel">
      <div class="budget-row">
        <label for="budget">ميزانية القائمة:</label>
        <input id="budget" type="number" min="0" step="0.01" placeholder="مثال: 300" dir="ltr">
        <span>ر.س</span>
      </div>
      <p id="budget-status" class="status" role="status"></p>
    </section>
    <div class="section-head"><h2>قائمة مشترياتي</h2><p id="list-status" class="status" role="status"></p></div>
    <div class="toolbar">
      <button type="button" id="compare-button" class="primary" aria-controls="comparison" aria-expanded="false" disabled>قارن المنتجات المحددة</button>
      <span id="compare-status" class="status" role="status">اختر منتجين أو ثلاثة من قائمتك.</span>
    </div>
    <section id="comparison" class="panel" aria-labelledby="comparison-title" hidden>
      <div class="section-head"><h3 id="comparison-title">مقارنة المنتجات</h3><button type="button" id="close-comparison" class="ghost">إغلاق المقارنة</button></div>
      <p class="hint">المقارنة تستخدم آخر الأسعار المحفوظة لكل متجر.</p>
      <div id="compare-content" class="table-scroll" tabindex="0" role="region" aria-label="جدول مقارنة المنتجات"></div>
    </section>
    <div id="saved-items" class="grid"></div>
  </section>

  <section id="detail" class="panel" hidden>
    <div class="section-head"><h2 id="detail-title"></h2><button type="button" id="close-detail" class="ghost">إغلاق</button></div>
    <p id="detail-status" class="status" role="status"></p>
    <div id="detail-action" class="actions"></div>
    <div id="detail-content"></div>
  </section>
</main>

<script>
const el = (selector) => document.querySelector(selector);
const query = el("#q");
const budget = el("#budget");
const labels = {headphones:"سماعات", keyboards:"كيبوردات", mice:"ماوسات"};
const dateFormat = new Intl.DateTimeFormat("ar-SA", {
  timeZone:"Asia/Riyadh", calendar:"gregory", dateStyle:"medium", timeStyle:"short"
});
let currentTab = "search", searchVersion = 0, detailVersion = 0, listVersion = 0;
let savedItems = [];
const compareIds = new Set();
const comparePicks = new Map();
const maxCompare = 3;

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function button(text, action, className) {
  const item = node("button", text, className);
  item.type = "button";
  item.addEventListener("click", () => action(item));
  return item;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

function amount(value, currency = "SAR") {
  return value == null ? "—" : Number(value).toFixed(2) + (currency === "SAR" ? " ر.س" : " " + currency);
}

async function api(path, method = "GET", body) {
  const options = {method};
  if (body !== undefined) {
    options.headers = {"Content-Type":"application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "تعذّر إكمال الطلب.");
  }
  return data;
}

async function withProductInfo(rows) {
  const params = new URLSearchParams();
  for (const row of rows) params.append("ids", row.id);
  if (!rows.length) return rows;
  const info = await api("/shop/api/product-info?" + params);
  const byId = new Map(info.map(item => [item.id, item]));
  return rows.map(row => ({...row, ...byId.get(row.id)}));
}

function imageFor(product) {
  const frame = node("div", undefined, "image");
  const url = safeUrl(product.image_url);
  if (!url) {
    frame.append(node("span","لا توجد صورة","placeholder"));
    return frame;
  }
  const image = node("img");
  image.src = url;
  image.alt = "";
  image.loading = "lazy";
  image.referrerPolicy = "no-referrer";
  image.addEventListener("error", () => frame.replaceChildren(node("span","الصورة غير متاحة","placeholder")));
  frame.append(image);
  return frame;
}

async function save(product, actionButton) {
  actionButton.disabled = true;
  try {
    await api("/shop/api/list/" + product.id, "POST");
    product.saved = true;
    actionButton.textContent = "تم الحفظ";
  } catch (error) {
    actionButton.textContent = "إعادة محاولة الحفظ";
    actionButton.title = error.message;
    actionButton.disabled = false;
  }
}

function productCard(product, kind) {
  const card = node("article", undefined, "card");
  const title = node("h3", product.title);
  title.dir = "auto";
  const titleArea = node("div", undefined, "cards-title");
  titleArea.append(title);
  if (labels[product.category]) titleArea.append(node("span", labels[product.category], "badge"));
  card.append(imageFor(product), titleArea);
  if (typeof product.similarity === "number") {
    card.append(node("p", "درجة التشابه: " + product.similarity.toFixed(4), "score"));
  }
  if (kind === "saved") {
    const text = product.latest_price == null ? "لا يوجد سعر محفوظ" :
      "أقل سعر محفوظ: " + amount(product.latest_price) + " · " + product.store;
    card.append(node("p", text, "summary"));
    if (product.observed_at) {
      card.append(node("p", "وقت القراءة: " + dateFormat.format(new Date(product.observed_at)), "small"));
    }
    if (product.target_price && product.latest_price != null &&
        Number(product.latest_price) <= Number(product.target_price)) {
      card.append(node("span", "آخر سعر عند هدفك أو أقل", "badge"));
    }
  }
  const actions = node("div", undefined, "actions");
  if (kind !== "similar") actions.append(button("بدائل مشابهة", () => showSimilar(product)));
  actions.append(button("عروض الأسعار", () => showOffers(product)));
  if (kind === "saved") {
    const pick = button("حدد للمقارنة", () => toggleComparison(product.id), "compare-pick");
    comparePicks.set(product.id, pick);
    actions.append(pick);
  } else {
    const add = button(product.saved ? "محفوظ" : "احفظ", (target) => save(product, target), "primary");
    if (product.saved) add.disabled = true;
    actions.append(add);
  }
  card.append(actions);
  if (kind === "saved") card.append(savedForm(product));
  return card;
}

function savedForm(product) {
  const form = node("form", undefined, "saved-fields");
  const targetLabel = node("label", "سعر مستهدف (ر.س)");
  const target = node("input");
  target.type = "number"; target.min = "0.01"; target.step = "0.01";
  target.max = "9999999999.99"; target.value = product.target_price || "";
  target.dir = "ltr";
  targetLabel.append(target);
  const noteLabel = node("label", "ملاحظتك");
  const note = node("textarea");
  note.maxLength = 500; note.value = product.note || "";
  noteLabel.append(note);
  const state = node("p", undefined, "status");
  state.setAttribute("role","status");
  const actions = node("div", undefined, "actions");
  const submit = node("button","حفظ التعديلات","primary");
  submit.type = "submit";
  const remove = button("إزالة", async (actionButton) => {
    actionButton.disabled = true;
    try {
      await api("/shop/api/list/" + product.id, "DELETE");
      await loadList();
    } catch (error) {
      state.textContent = error.message;
      actionButton.disabled = false;
    }
  }, "ghost");
  actions.append(submit, remove);
  form.append(targetLabel, noteLabel, actions, state);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      await api("/shop/api/list/" + product.id, "PUT", {
        target_price: target.value || null, note: note.value
      });
      await loadList();
      el("#list-status").textContent = "تم حفظ التعديلات.";
    } catch (error) { state.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  return form;
}

function openDetail(title) {
  const version = ++detailVersion;
  el("#detail-title").textContent = title;
  el("#detail-status").textContent = "جاري التحميل...";
  el("#detail-action").replaceChildren();
  el("#detail-content").replaceChildren();
  el("#detail-content").className = "";
  el("#detail").hidden = false;
  el("#detail").scrollIntoView({block:"start", behavior:"smooth"});
  return version;
}

function closeDetail() {
  ++detailVersion;
  el("#detail").hidden = true;
}
el("#close-detail").addEventListener("click", closeDetail);

async function showSimilar(product) {
  const version = openDetail("بدائل لـ " + product.title);
  try {
    const rows = await withProductInfo(await api("/products/" + product.id + "/similar"));
    if (version !== detailVersion) return;
    const content = el("#detail-content");
    content.className = "grid";
    content.replaceChildren(...rows.map(row => productCard(row,"similar")));
    el("#detail-status").textContent = rows.length ? "" : "ما فيه بدائل مفهرسة من نفس الفئة.";
  } catch {
    if (version === detailVersion) el("#detail-status").textContent =
      "البدائل غير متاحة الآن. إذا جلبت المنتج للتو، انتظر تجهيز صورته وجرّب مرة ثانية.";
  }
}

function offerCard(offer) {
  const card = node("article", undefined, "offer");
  card.append(node("h4", offer.store), node("p", "آخر سعر محفوظ: " + amount(offer.price,offer.currency), "price"));
  if (offer.advertised_title) {
    const title = node("p", offer.advertised_title, "small");
    title.dir = "auto";
    card.append(title);
  }
  if (offer.observed_at) {
    card.append(node("p", "وقت القراءة: " + dateFormat.format(new Date(offer.observed_at)), "small"));
  }
  const row = node("div", undefined, "actions");
  const url = safeUrl(offer.url);
  if (url) {
    const link = node("a","صفحة المتجر");
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    row.append(link);
  }
  const history = node("div", undefined, "history");
  history.hidden = true;
  const historyButton = button("سجل السعر", async (target) => {
    if (!history.hidden) { history.hidden = true; return; }
    history.hidden = false;
    history.textContent = "جاري تحميل السجل...";
    target.disabled = true;
    try {
      const data = await api("/price-history?offer_id=" + offer.id);
      const readings = data.readings.slice(-15).reverse();
      history.replaceChildren();
      if (!readings.length) history.textContent = "لا توجد قراءات.";
      for (const reading of readings) {
        const line = node("p");
        line.append(node("strong",amount(reading.price,offer.currency)),
          node("span",dateFormat.format(new Date(reading.observed_at)),"small"));
        history.append(line);
      }
    } catch (error) { history.textContent = error.message; }
    finally { target.disabled = false; }
  });
  row.append(historyButton);
  card.append(row, history);
  return card;
}

async function showOffers(product) {
  const version = openDetail("عروض " + product.title);
  const detailStatus = el("#detail-status");
  const detailContent = el("#detail-content");
  async function loadOffers(message = "") {
    const rows = await api("/shop/api/products/" + product.id + "/offers");
    if (version !== detailVersion) return;
    detailContent.replaceChildren(...rows.map(offerCard));
    detailStatus.textContent = message || (rows.length
      ? "الأسعار من آخر قراءة محفوظة؛ راجع صفحة المتجر للون والنسخة والسعر الحالي."
      : "ما فيه عروض محفوظة لهذا المنتج.");
  }
  if (product.can_scan) {
    el("#detail-action").append(button("جلب عروض من المتاجر", async (target) => {
      target.disabled = true;
      detailStatus.textContent = "جاري جلب العروض...";
      try {
        const data = await api("/shop/api/products/" + product.id + "/scan-offers", "POST");
        const message = data.cached ? "استخدمنا نتيجة الجلب المحفوظة."
          : data.saved ? "تم حفظ " + data.saved + " عرضًا. تأكد من النسخة في المتجر."
          : "لم يرجع المصدر عروضًا قابلة للحفظ من المتاجر المدعومة.";
        await loadOffers(message);
        if (currentTab === "list") await loadList();
      } catch (error) { if (version === detailVersion) detailStatus.textContent = error.message; }
      finally { target.disabled = false; }
    }, "primary"));
  }
  try { await loadOffers(); }
  catch (error) { if (version === detailVersion) detailStatus.textContent = error.message; }
}

function updateBudget() {
  const priced = savedItems.filter(item => item.latest_price != null);
  const total = priced.reduce((sum,item) => sum + Math.round(Number(item.latest_price)*100),0);
  const limit = budget.value === "" ? null : Math.round(Number(budget.value)*100);
  const missing = savedItems.length - priced.length;
  const text = !savedItems.length ? "احفظ منتجات لعرض المجموع." :
    !priced.length ? "ما فيه أسعار محفوظة لمنتجات القائمة." :
    "مجموع الأسعار المحفوظة: " + amount(total/100) +
    (missing ? " · " + missing + " بدون سعر" : "") +
    (limit !== null && limit >= 0 ? " · " + (total <= limit ? "متبقّي " : "فوق الميزانية بـ ") +
      amount(Math.abs(limit-total)/100) + (missing ? " حسب الأسعار المتاحة" : "") : "");
  el("#budget-status").textContent = text;
}

function closeComparison() {
  el("#comparison").hidden = true;
  el("#compare-button").setAttribute("aria-expanded", "false");
}

function renderComparison() {
  const rows = savedItems.filter(item => compareIds.has(item.id));
  if (rows.length < 2) {
    closeComparison();
    return;
  }

  const known = rows.filter(item => item.latest_price != null);
  const lowest = known.length >= 2 ? Math.min(...known.map(item => Number(item.latest_price))) : null;
  const table = node("table", undefined, "compare-table");
  const head = node("thead");
  const header = node("tr");
  const feature = node("th", "التفصيل");
  feature.scope = "col";
  header.append(feature);
  for (const item of rows) {
    const title = node("th", item.title);
    title.scope = "col";
    title.dir = "auto";
    header.append(title);
  }
  head.append(header);
  table.append(head);

  const body = node("tbody");
  const fields = [
    ["أقل سعر محفوظ", item => item.latest_price == null ? "لا يوجد سعر محفوظ" : amount(item.latest_price)],
    ["المتجر", item => item.store || "—"],
    ["وقت القراءة", item => item.observed_at ? dateFormat.format(new Date(item.observed_at)) : "—"],
    ["السعر المستهدف", item => amount(item.target_price)],
    ["ملاحظتك", item => item.note || "—"],
  ];
  for (const [label, value] of fields) {
    const tr = node("tr");
    const name = node("th", label);
    name.scope = "row";
    tr.append(name);
    for (const item of rows) {
      const cell = node("td", value(item));
      if (label === "أقل سعر محفوظ" && lowest !== null && item.latest_price != null &&
          Number(item.latest_price) === lowest) {
        cell.className = "best";
      }
      tr.append(cell);
    }
    body.append(tr);
  }
  table.append(body);
  el("#compare-content").replaceChildren(table);
}

function syncComparison() {
  const count = compareIds.size;
  for (const [id, pick] of comparePicks) {
    const selected = compareIds.has(id);
    pick.textContent = selected ? "محدد للمقارنة" : "حدد للمقارنة";
    pick.setAttribute("aria-pressed", String(selected));
    pick.disabled = count === maxCompare && !selected;
  }
  el("#compare-button").disabled = count < 2;
  el("#compare-status").textContent = count === 0
    ? "اختر منتجين أو ثلاثة من قائمتك."
    : "تم تحديد " + count + " من " + maxCompare;
  if (!el("#comparison").hidden) renderComparison();
}

function toggleComparison(id) {
  if (compareIds.has(id)) compareIds.delete(id);
  else if (comparePicks.has(id) && compareIds.size < maxCompare) compareIds.add(id);
  syncComparison();
}

el("#compare-button").addEventListener("click", () => {
  if (compareIds.size < 2) return;
  closeDetail();
  renderComparison();
  el("#comparison").hidden = false;
  el("#compare-button").setAttribute("aria-expanded", "true");
  el("#compare-content").focus({preventScroll:true});
  el("#comparison").scrollIntoView({block:"start",behavior:"smooth"});
});
el("#close-comparison").addEventListener("click", () => {
  closeComparison();
  el("#compare-button").focus();
});

async function loadList() {
  const version = ++listVersion;
  const state = el("#list-status");
  state.textContent = "جاري تحميل القائمة...";
  try {
    const rows = await api("/shop/api/list");
    if (version !== listVersion) return;
    savedItems = rows;
    const savedIds = new Set(rows.map(item => item.id));
    for (const id of compareIds) if (!savedIds.has(id)) compareIds.delete(id);
    comparePicks.clear();
    el("#saved-items").replaceChildren(...rows.map(row => productCard(row,"saved")));
    state.textContent = rows.length ? rows.length + " منتجًا محفوظًا" : "قائمتك فاضية. احفظ منتجًا من نتائج البحث.";
    updateBudget();
    syncComparison();
  } catch (error) { if (version === listVersion) state.textContent = error.message; }
}

async function runSearch() {
  const version = ++searchVersion;
  const text = query.value.trim();
  const state = el("#search-status");
  closeDetail();
  el("#results").replaceChildren();
  state.textContent = "جاري البحث...";
  try {
    const rows = await api("/search?q=" + encodeURIComponent(text));
    const visible = await withProductInfo(rows.slice(0,80));
    if (version !== searchVersion) return;
    el("#results").replaceChildren(...visible.map(row => productCard(row,"search")));
    state.textContent = rows.length
      ? "نتائج البحث: " + rows.length + (rows.length > 80 ? " · أول 80 نتيجة معروضة" : "")
      : "ما فيه نتائج محفوظة. جرّب عبارة ثانية أو اجلب منتجات إضافية.";
  } catch (error) { if (version === searchVersion) state.textContent = error.message; }
}

function switchTab(name) {
  currentTab = name;
  closeDetail();
  for (const tab of ["search","list"]) {
    el("#" + tab + "-view").hidden = tab !== name;
    el("#" + tab + "-tab").setAttribute("aria-pressed", String(tab === name));
  }
  if (name === "list") loadList();
  else runSearch();
}

el("#search-tab").addEventListener("click", () => switchTab("search"));
el("#list-tab").addEventListener("click", () => switchTab("list"));
el("#search-form").addEventListener("submit", (event) => { event.preventDefault(); runSearch(); });
el("#discover").addEventListener("click", async (event) => {
  const target = event.currentTarget;
  const state = el("#discover-status");
  const text = query.value.trim();
  if (text.length < 2) { state.textContent = "اكتب عبارة بحث أول."; return; }
  target.disabled = true;
  state.textContent = "جاري جلب المنتجات...";
  try {
    const params = new URLSearchParams({q:text,category:el("#category").value});
    const data = await api("/discover?" + params, "POST");
    state.textContent = "دفعة «" + text + "»: " + data.count +
      " منتجًا. الصور الجديدة تُجهّز في الخلفية.";
    if (currentTab === "search" && query.value.trim() === text) await runSearch();
  } catch (error) { state.textContent = error.message; }
  finally { target.disabled = false; }
});
try { budget.value = localStorage.getItem("buywise-budget-sar") || ""; } catch {}
budget.addEventListener("input", () => {
  try { localStorage.setItem("buywise-budget-sar",budget.value); } catch {}
  updateBudget();
});
runSearch();
</script>
</body>
</html>
'''

if __name__ == "__main__":
    main()
