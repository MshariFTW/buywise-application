"""Local, read-only evaluator for BuyWise's text and image retrieval.

Place beside buywise_api.py and run: python buywise_ir_eval.py
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from getpass import getpass
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from buywise_api import create_app
from buywise_discover import connect_db


K = 3
MODEL = "openai/clip-vit-base-patch32"
CATEGORIES = {"headphones": "سماعات", "keyboards": "كيبوردات", "mice": "ماوسات"}
TEXT_CASES = (
    ("wireless keyboard", "keyboards",
     "مناسب إذا كان كيبوردًا يدعم اللاسلكي أو البلوتوث أو اتصال 2.4 GHz لاسلكيًا. "
     "الكيبورد ضمن طقم مقبول إذا كان يدعم اللاسلكي. السلكي فقط والملحقات غير مناسبة."),
    ("headphone", "headphones",
     "مناسب إذا كان سماعة تُلبس على الأذن أو فوقها. الحافظات وقطع الغيار غير مناسبة."),
    ("wired keyboard", "keyboards",
     "مناسب إذا كان كيبوردًا يدعم اتصالًا سلكيًا. USB للشحن فقط أو لمستقبل لاسلكي لا يكفي."),
    ("wireless mouse", "mice",
     "مناسب إذا كان ماوسًا يدعم الاتصال اللاسلكي. السلكي فقط ولوحة الماوس غير مناسبة."),
)
PREFERRED_IMAGE_REFS = {
    "headphones": "wh-ch520", "keyboards": "f75 max", "mice": "",
}


def allowed_image(url):
    if not isinstance(url, str):
        return None
    try:
        value = urlsplit(url)
    except ValueError:
        return None
    return url if value.scheme == "https" and value.hostname else None


def read_catalog(password):
    with connect_db(password) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            """
            SELECT p.id, p.title, p.category, p.image_url, e.product_id IS NOT NULL
            FROM products AS p
            LEFT JOIN product_image_embeddings AS e
              ON e.product_id = p.id
              AND e.model_name = %s
              AND e.source_url = p.image_url
            ORDER BY p.id
            """, (MODEL,),
        ).fetchall()
    return {
        item_id: {"id": item_id, "title": title, "category": category,
                  "image_url": allowed_image(image_url), "indexed": indexed}
        for item_id, title, category, image_url, indexed in rows
    }


def literal_words(text):
    return set(re.findall(r"\w+", text.lower()))


def literal_search(catalog, query):
    query_words = literal_words(query)
    return [item_id for item_id, item in sorted(catalog.items())
            if query_words and query_words.issubset(literal_words(item["title"]))]


def result_ids(client, path, catalog, params=None):
    response = client.get(path, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"BuyWise returned {response.status_code} for {path}.")
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"Expected a result list from {path}.")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or any(item_id not in catalog for item_id in ids):
        raise RuntimeError(f"Invalid results from {path}, or the catalog changed. Run again.")
    return ids


def evaluation_case(case_id, kind, query, rule, baseline, buywise, catalog, ref=None):
    methods = {"baseline": baseline[:K], "buywise": buywise[:K]}
    pool = set(methods["baseline"]) | set(methods["buywise"])
    ordered_pool = sorted(pool, key=lambda item_id: sha256(
        f"{case_id}:{item_id}".encode("utf-8")
    ).digest())
    return {
        "id": case_id,
        "kind": kind,
        "query": query,
        "rule": rule,
        "reference": catalog.get(ref),
        "methods": methods,
        "retrieved": {"baseline": len(baseline), "buywise": len(buywise)},
        "pool": [catalog[item_id] for item_id in ordered_pool],
    }


def collect(password):
    from fastapi.testclient import TestClient

    catalog = read_catalog(password)
    categories = Counter(item["category"] or "uncategorized" for item in catalog.values())
    cases = []
    app = create_app(password, "")
    if not isinstance(app, FastAPI):
        raise RuntimeError("buywise_api.create_app() did not return a FastAPI app.")

    # Invoke the actual retrieval routes in-process. No discovery or price route runs.
    with TestClient(app) as client:
        for query, category, rule in TEXT_CASES:
            if not categories.get(category):
                continue
            baseline = literal_search(catalog, query)
            actual = result_ids(client, "/search", catalog, {"q": query})
            cases.append(evaluation_case("text:" + query, "text", query, rule,
                                         baseline, actual, catalog))

        visual_count = 0
        for category, preferred in PREFERRED_IMAGE_REFS.items():
            indexed = [item for item in catalog.values()
                       if item["category"] == category and item["indexed"]]
            if len(indexed) < 2:
                continue
            ref = next((item for item in indexed if preferred and
                        preferred in item["title"].lower()), indexed[0])
            baseline = [item["id"] for item in indexed if item["id"] != ref["id"]]
            actual = result_ids(client, f"/products/{ref['id']}/similar", catalog)
            if not set(actual).issubset(baseline):
                raise RuntimeError("Image retrieval returned the reference or a result outside its category.")
            cases.append(evaluation_case(
                f"image:{ref['id']}", "image", ref["title"],
                "قارن الصورة المرجعية بصور النتائج. مناسب إذا كان الشكل العام متقاربًا "
                "في النوع والبنية، مثل شكل سماعة الرأس أو حجم وتخطيط الكيبورد. "
                "اختلاف اللون وحده لا يكفي للرفض. اترك الصورة غير الواضحة دون حكم.",
                baseline, actual, catalog, ref["id"],
            ))
            visual_count += 1
            if visual_count == 2:
                break

    snapshot = {
        "size": len(catalog),
        "indexed_count": sum(item["indexed"] for item in catalog.values()),
        "categories": dict(sorted(categories.items())),
        "cases": cases,
        "k": K,
    }
    fingerprint = sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
                         .encode("utf-8")).hexdigest()[:20]
    return {**snapshot, "id": fingerprint,
            "timestamp": datetime.now(timezone.utc).isoformat()}


def make_eval_app(snapshot):
    app = FastAPI(title="BuyWise IR Evaluation", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    def home():
        return PAGE

    @app.get("/data")
    def data():
        return snapshot

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    password = getpass("PostgreSQL password: ")
    print("Reading your catalog and running local search evaluations...", flush=True)
    snapshot = collect(password)
    print(f"{snapshot['size']} products, {len(snapshot['cases'])} evaluation cases.", flush=True)
    print(f"Open http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run(make_eval_app(snapshot), host="127.0.0.1", port=args.port)


PAGE = r'''
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuyWise · تقييم البحث</title>
<style>
:root{--ink:#173334;--muted:#526b66;--green:#175e4c;--line:#d9e5df;--paper:#fff;--bg:#f3f7f5;--soft:#e8f3ec}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Segoe UI",Arial,sans-serif}
main{max-width:1060px;margin:auto;padding:24px 22px 55px}
header{padding:27px;border-radius:22px;background:linear-gradient(125deg,#123b34,#1b594b);color:white}
h1,h2,h3,p{margin:0}h1{font-size:29px}h2{font-size:20px}h3{font-size:16px}
header p{margin-top:5px;color:#d4ebe1}
section{background:var(--paper);margin-top:15px;border:1px solid var(--line);border-radius:16px;padding:16px}
.controls,.badges,.head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.head{justify-content:space-between}
.badges{margin:9px 0}.badge{padding:3px 10px;background:var(--soft);color:#164d3f;border-radius:999px;font-size:13px}
.hint,.status{color:var(--muted);font-size:13px}.status{padding:5px 0}
button{cursor:pointer;font:inherit;padding:6px 10px;border-radius:9px;border:1px solid #bfd4ca;background:white;color:var(--green);font-weight:600}
button:hover{background:var(--soft)}button:focus-visible,a:focus-visible{outline:3px solid #4d9c82;outline-offset:2px}
button.primary,button[aria-pressed="true"]{background:var(--green);border-color:var(--green);color:white}
button:disabled{opacity:.55;cursor:default}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:9px;margin:10px 0}
.card,.reference{padding:10px;border:1px solid var(--line);border-radius:12px;background:#fcfefd}
.card p,.reference p{margin:4px 0;overflow-wrap:anywhere}
.card .controls button{font-size:13px}
.reference{margin:10px 0;display:flex;align-items:center;gap:10px}
.photo{display:block;width:100%;height:105px;object-fit:contain;background:#f4f8f5;border-radius:8px}
.reference .photo{width:105px}
table{width:100%;border-collapse:collapse;margin-top:5px}
th,td{text-align:start;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto}
[hidden]{display:none!important}
@media(max-width:540px){main{padding:10px}header{padding:17px}.grid{grid-template-columns:1fr 1fr}.card .controls{gap:4px}.card .controls button{padding:4px 6px}}
</style>
</head>
<body>
<main>
<header>
  <h1>BuyWise · تقييم البحث</h1>
  <p>قيّم جودة النتائج النصية والبصرية على كتالوجك المحفوظ.</p>
</header>
<section aria-label="النتائج والتقرير">
  <h2>ملخص التقييم</h2>
  <div class="badges" id="catalog"></div>
  <p class="hint">اختر «مناسب» أو «غير مناسب» حسب معيار كل حالة. اترك المنتج غير الواضح بلا حكم.</p>
  <p class="status" id="progress" role="status">جاري قراءة التقييم...</p>
  <div class="scroll" id="summary"></div>
  <div class="controls">
    <button id="download" type="button" class="primary" disabled>تنزيل التقرير بالإنجليزي</button>
    <button id="download-data" type="button" disabled>تنزيل البيانات والأحكام</button>
  </div>
</section>
<div id="cases"></div>
</main>
<script>
const el = selector => document.querySelector(selector);
const labels = {headphones:"سماعات", keyboards:"كيبوردات", mice:"ماوسات", uncategorized:"بلا فئة"};
const groupLabels = {text:"البحث النصي", image:"البدائل بالصور"};
const panels = new Map();
let snapshot;
let judgments = {};
let storageKey;

function node(tag,text,style) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (style) item.className = style;
  return item;
}

function button(text, action) {
  const item = node("button",text);
  item.type = "button";
  item.addEventListener("click",action);
  return item;
}

function tableFor(headers, rows) {
  const table = node("table");
  const head = node("thead");
  const header = node("tr");
  for (const title of headers) {
    const cell = node("th",title);
    cell.scope = "col";
    header.append(cell);
  }
  head.append(header);
  const body = node("tbody");
  for (const row of rows) {
    const tr = node("tr");
    for (const value of row) tr.append(node("td",value));
    body.append(tr);
  }
  table.append(head,body);
  return table;
}

function picture(product) {
  if (!product.image_url) return node("p","الصورة غير متاحة.","hint");
  const img = node("img",undefined,"photo");
  img.alt = product.title;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  const link = node("a");
  link.href = product.image_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  img.addEventListener("error",() => link.replaceChildren(node("span","الصورة غير متاحة.","hint")));
  img.src = product.image_url;
  link.append(img);
  return link;
}

function caseJudgments(test) {
  return judgments[test.id] || {};
}

function score(test) {
  const votes = caseJudgments(test);
  if (!test.pool.every(item => votes[item.id] === 0 || votes[item.id] === 1)) return null;
  const values = {};
  for (const method of ["baseline","buywise"]) {
    const ids = test.methods[method];
    values[method] = {
      p1: ids.length ? votes[ids[0]] : 0,
      p3: ids.reduce((total,id) => total + votes[id], 0) / snapshot.k,
    };
  }
  return values;
}

function groupedScores() {
  const output = {};
  for (const kind of ["text","image"]) {
    const cases = snapshot.cases.filter(test => test.kind === kind);
    const scored = cases.map(score).filter(Boolean);
    const totals = {baseline:{p1:0,p3:0},buywise:{p1:0,p3:0}};
    for (const result of scored) {
      for (const method of ["baseline","buywise"]) {
        totals[method].p1 += result[method].p1 / scored.length;
        totals[method].p3 += result[method].p3 / scored.length;
      }
    }
    output[kind] = {total:cases.length,complete:scored.length,
      averages:scored.length ? totals : null};
  }
  return output;
}

const percent = value => (value*100).toFixed(1) + "%";

function renderScores() {
  let complete = 0;
  for (const test of snapshot.cases) {
    const panel = panels.get(test.id);
    const votes = caseJudgments(test);
    let marked = 0;
    for (const item of test.pool) {
      const vote = votes[item.id];
      if (vote === 0 || vote === 1) marked++;
      const controls = panel.controls.get(item.id);
      controls.yes.setAttribute("aria-pressed",String(vote===1));
      controls.no.setAttribute("aria-pressed",String(vote===0));
      controls.clear.hidden = vote !== 0 && vote !== 1;
    }
    panel.progress.textContent = "تم تقييم " + marked + " من " + test.pool.length;
    const result = score(test);
    panel.result.replaceChildren();
    if (!result) continue;
    complete++;
    const methods = test.kind === "text"
      ? ["مطابقة حرفية","بحث BuyWise"]
      : ["ترتيب ثابت داخل الفئة","تشابه الصور في BuyWise"];
    panel.result.append(tableFor(["الطريقة","Precision@1","Precision@3"],
      ["baseline","buywise"].map((m,i) => [methods[i],percent(result[m].p1),percent(result[m].p3)])));
  }

  const groups = groupedScores();
  const summaries = Object.entries(groups).filter(([, group])=>group.total).map(([kind,group])=>[
    groupLabels[kind], group.complete + " / " + group.total,
    group.averages ? percent(group.averages.baseline.p3) : "—",
    group.averages ? percent(group.averages.buywise.p3) : "—",
  ]);
  el("#summary").replaceChildren();
  if (summaries.length) el("#summary").append(tableFor(["القسم","حالات مكتملة","الأساس P@3","BuyWise P@3"],summaries));
  el("#progress").textContent = snapshot.cases.length
    ? "اكتمل " + complete + " من " + snapshot.cases.length + " حالات."
    : "ما فيه حالات قابلة للتقييم في الكتالوج الحالي.";
  el("#download").disabled = complete === 0;
  el("#download-data").disabled = false;
}

function mark(test,id,value) {
  if (!judgments[test.id]) judgments[test.id] = {};
  if (value == null) delete judgments[test.id][id];
  else judgments[test.id][id] = value;
  try { localStorage.setItem(storageKey,JSON.stringify(judgments)); } catch {}
  renderScores();
}

function showCase(test, number) {
  const box = node("section");
  const heading = node("div",undefined,"head");
  const title = node("h3",number + ". " + test.query);
  title.dir = "auto";
  heading.append(title,node("span",groupLabels[test.kind],"badge"));
  box.append(heading,node("p",test.rule,"hint"));

  if (test.reference) {
    const ref = node("div",undefined,"reference");
    ref.append(picture(test.reference),node("p","الصورة المرجعية: " + test.reference.title));
    box.append(ref);
  } else {
    box.append(node("p","عدد النتائج قبل القصّ: المطابقة الحرفية " + test.retrieved.baseline +
      "، وBuyWise " + test.retrieved.buywise + ".","hint"));
  }

  const cards = node("div",undefined,"grid");
  const controls = new Map();
  for (const product of test.pool) {
    const card = node("article",undefined,"card");
    if (test.kind === "image") card.append(picture(product));
    const caption = node("p",product.title);
    caption.dir = "auto";
    const actions = node("div",undefined,"controls");
    const yes = button("مناسب",()=>mark(test,product.id,1));
    const no = button("غير مناسب",()=>mark(test,product.id,0));
    const clear = button("إلغاء",()=>mark(test,product.id,null));
    actions.append(yes,no,clear);
    card.append(caption,actions);
    cards.append(card);
    controls.set(product.id,{yes,no,clear});
  }
  if (!test.pool.length) cards.append(node("p","الطريقتان ما رجّعتا نتائج.","hint"));
  const progress = node("p",undefined,"status");
  progress.setAttribute("role","status");
  const result = node("div",undefined,"scroll");
  box.append(cards,progress,result);
  panels.set(test.id,{controls,progress,result});
  return box;
}

function asFile(filename,text,type) {
  const url = URL.createObjectURL(new Blob([text],{type}));
  const link = node("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),2000);
}

function markdown() {
  const cell = value => String(value).replaceAll("|","&#124;").replace(/[\r\n]+/g," ");
  const lines = [
    "# BuyWise — Information Retrieval Evaluation","",
    "- Catalog snapshot: " + snapshot.timestamp,
    "- Products: " + snapshot.size + "; products with usable stored embeddings: " + snapshot.indexed_count + ".",
    "- Categories: " + Object.entries(snapshot.categories).map(([key,count])=>key+" ("+count+")").join(", ") + ".",
    "- Snapshot ID: " + snapshot.id,"",
    "## Method","",
    "- Text baseline: lowercase literal token AND, in product-ID order; no stemming or attribute enrichment.",
    "- Text system: the actual BuyWise /search endpoint.",
    "- Image baseline: fixed product-ID order in the reference category, excluding the reference and using the same embedding-eligible products.",
    "- Image system: the actual BuyWise /similar endpoint using CLIP image embeddings and cosine similarity.",
    "- One human assessor judged the pooled top three results of both methods for each case. Candidates were shown in a fixed order independent of ranking.",
    "- Precision@3 is the number of relevant results in the first three positions divided by three. Unfilled positions count as zero. Precision@1 measures whether the first result is relevant.",
    "- Text judgments use the query intent and product title. Visual judgments use overall form and layout; color alone does not decide relevance.","",
    "## Results","",
    "| Case | Type | Baseline P@1 | BuyWise P@1 | Baseline P@3 | BuyWise P@3 |",
    "|---|---|---:|---:|---:|---:|",
  ];
  for (const test of snapshot.cases) {
    const result = score(test);
    const values = result ? [
      result.baseline.p1,result.buywise.p1,result.baseline.p3,result.buywise.p3
    ].map(percent) : ["Unjudged","Unjudged","Unjudged","Unjudged"];
    lines.push("| "+[cell(test.query),test.kind,...values].join(" | ")+" |");
  }
  lines.push("","| Type | Completed cases | Baseline mean P@3 | BuyWise mean P@3 |",
    "|---|---:|---:|---:|");
  for (const [kind,group] of Object.entries(groupedScores())) {
    if (group.total) lines.push("| "+[kind,group.complete+" / "+group.total,
      group.averages ? percent(group.averages.baseline.p3) : "—",
      group.averages ? percent(group.averages.buywise.p3) : "—"].join(" | ")+" |");
  }
  lines.push("","## Limitations","",
    "This is a small, catalog-specific evaluation. A single assessor judged only pooled results, so recall is not reported. Incomplete cases are excluded from the averages. Visual and textual tasks are summarized separately.","",
    "Metric reference: [Introduction to Information Retrieval, §8.4](https://nlp.stanford.edu/IR-book/pdf/08eval.pdf).","");
  return lines.join("\n");
}

el("#download").addEventListener("click",()=>asFile("buywise_ir_report.md",markdown(),"text/markdown;charset=utf-8"));
el("#download-data").addEventListener("click",()=>asFile(
  "buywise_ir_judgments.json",
  JSON.stringify({snapshot,judgments,summary:groupedScores()},null,2),"application/json;charset=utf-8"
));

async function start() {
  const response = await fetch("/data");
  if (!response.ok) throw new Error("تعذّر تحميل التقييم.");
  snapshot = await response.json();
  storageKey = "buywise-ir-evaluation:" + snapshot.id;
  try { judgments = JSON.parse(localStorage.getItem(storageKey) || "{}") || {}; } catch {}
  el("#catalog").append(node("span",snapshot.size + " منتجًا في الكتالوج","badge"),
    node("span",snapshot.indexed_count + " لها متجهات صور","badge"));
  for (const [category,count] of Object.entries(snapshot.categories)) {
    el("#catalog").append(node("span",(labels[category] || category) + ": " + count,"badge"));
  }
  el("#cases").replaceChildren(...snapshot.cases.map((test,i)=>showCase(test,i+1)));
  renderScores();
}
start().catch(error=>{ el("#progress").textContent = error.message; });
</script>
</body>
</html>
'''

if __name__ == "__main__":
    main()
