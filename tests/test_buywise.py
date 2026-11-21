"""Contract tests. The integration class only opens a database it creates itself."""

import json
import os
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg import sql
from buywise_db import SCHEMA, connect_db, password_prompt
from buywise_discover import discover_products
from buywise_export import export
from buywise_ir_eval import experiment, PILOT, EVALUATION
from buywise_plus import build_app, install_schema
from buywise_probe import identity, offer_rejection, parse_offers
from buywise_refresh_db import matched_price, refresh
from buywise_retrieval import Rug, catalog, ranked, terms
from restore_demo import restore, SNAPSHOT


class RetrievalTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(terms("RUGS Brönden"), terms("rug BRONDEN"))

    def test_rank_and_group(self):
        def rug(i, name, design, v):
            return Rug(i, str(i), design, name, "url" + str(i), v)

        ref = rug(1, "red red rug", "A", (1.0, 0.0))
        c = {
            r.id: r
            for r in [
                ref,
                rug(2, "red", "A", (0.99, 0.1)),
                rug(3, "red rug", "B", (0.9, 0.4)),
                rug(4, "red", "B", (1.0, 0.0)),
                rug(5, "blue", "C", (0.0, 1.0)),
            ]
        }
        self.assertEqual([(r.id, s) for r, s in ranked(c, ref, "title")], [(3, 2)])
        self.assertEqual([r.id for r, s in ranked(c, ref, "image")], [4, 5])

    def test_price_format(self):
        for label, n in [
            ("SAR 19 to 39", 19),
            ("SAR 19", 21),
            ("USD 19", 19),
            ("SAR 10–15", 10),
            ("SAR 5", float("nan")),
        ]:
            self.assertIsNotNone(offer_rejection(True, label, n))
        self.assertIsNone(offer_rejection(True, "SAR 1,099.00", 1099))

    def test_noon_identity(self):
        root = "https://www.noon.com/saudi-en/"
        left = identity(root + "red-rug/N12345A/p/?o=111&size=M&utm_source=abc")
        self.assertEqual(
            left, identity(root + "renamed/N12345A/p/?size=M&o=111&gclid=123")
        )
        for query in ("size=L&o=111", "size=M&o=112", "size=M", "size=M&o=111&store=B"):
            self.assertNotEqual(left, identity(root + "renamed/N12345A/p/?" + query))

    def test_refresh_guard(self):
        saved = (
            1,
            "Noon",
            "Vendor",
            "key",
            "SAR",
            "token",
            "url",
            "Rug 60x90 cm",
            "60 × 90 cm",
            None,
        )
        item = dict(
            store="Noon",
            seller="Vendor",
            listing="key",
            currency="SAR",
            title="Rug 60x90 cm",
            price="20",
        )
        self.assertEqual(matched_price([item], saved), Decimal(20))
        for change in (
            {"seller": "Other"},
            {"title": "Rug 80x90 cm"},
            {"listing": "key2"},
            {"currency": "USD"},
        ):
            self.assertIsNone(matched_price([{**item, **change}], saved))
        self.assertIsNone(matched_price([item, {**item, "price": "25"}], saved))


@unittest.skipUnless(
    os.getenv("BUYWISE_TEST_DB") == "1",
    "Set BUYWISE_TEST_DB=1 for disposable PostgreSQL integration tests",
)
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = password_prompt()
        cls.db = "buywise_test_" + uuid.uuid4().hex
        with connect_db(cls.password, "postgres", autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.db)))
        cls.env = patch.dict(os.environ, PGDATABASE=cls.db)
        cls.env.start()
        cls.addClassCleanup(cls.cleanup)
        # Reproduce an existing legacy catalog before the additive migrations.
        with connect_db(cls.password) as c:
            for statement in SCHEMA:
                c.execute(statement)
            c.execute(
                "INSERT INTO product_categories VALUES ('headphones','Headphones')"
            )
            c.execute(
                "INSERT INTO products(model_key,title,category) VALUES('legacy:x','Old headset','headphones')"
            )
        cls.seed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        restore(cls.seed, cls.password)
        install_schema(cls.password)
        cls.client = TestClient(build_app(cls.password, ""))
        with connect_db(cls.password) as c:
            cls.items = catalog(c)
        cls.count = len(cls.seed["rugs"])
        cls.design_count = len({r["design"] for r in cls.seed["rugs"]})
        cls.history_count = sum(len(o["history"]) for r in cls.seed["rugs"] for o in r["offers"])
        cls.ref = next(
            r for r in cls.items.values() if r.key == "Home Centre:167945626"
        )

    @classmethod
    def cleanup(cls):
        cls.env.stop()
        with connect_db(cls.password, "postgres", autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.db)))

    def test_restore_and_export(self):
        restore(self.seed, self.password)
        data = export(self.password)
        self.assertEqual(len(data["rugs"]), self.count)
        self.assertEqual(sum(len(o["history"]) for r in data["rugs"] for o in r["offers"]), self.history_count)
        with connect_db(self.password) as c:
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM products WHERE model_key='legacy:x'"
                ).fetchone()[0],
                1,
            )
        self.assertEqual(len({r.design for r in self.items.values()}), self.design_count)
        self.assertTrue(
            all("page_token" not in o for r in data["rugs"] for o in r["offers"])
        )
        self.assertEqual(
            {r["key"]: r for r in data["rugs"]}.keys(),
            {r["key"] for r in self.seed["rugs"]},
        )

    def test_search_and_photos(self):
        self.assertEqual(
            len(self.client.get("/search", params={"q": "rugs"}).json()), self.count
        )
        self.assertEqual(
            len(self.client.get("/search", params={"q": "bronden"}).json()), 1
        )
        self.assertEqual(
            self.client.get("/search", params={"q": "headphones"}).json(), []
        )
        self.assertEqual(
            self.client.get("/photo", params={"url": self.ref.image}).headers[
                "content-type"
            ],
            "image/jpeg",
        )

    def test_expanded_sources_in_shop(self):
        for store, query in (("ABYAT", "أبيات"), ("CB2", "CB2"),
                             ("Weft Rug", "ويفت"), ("Zara Home", "زارا هوم")):
            source = {r["image_url"]: r for r in self.seed["rugs"]
                      if any(o["store"] == store for o in r["offers"])}
            results = self.client.get("/search", params={"q": query}).json()
            self.assertTrue(source)
            self.assertEqual({r["image_url"] for r in results}, set(source))
            for row in results:
                url = row["image_url"]
                photo = self.client.get("/photo", params={"url": url}, follow_redirects=False)
                self.assertEqual((photo.status_code, photo.headers["content-type"]), (200, "image/jpeg"))
                self.assertIn(row["id"], self.items)
                offer = self.client.get(f"/shop/api/products/{row['id']}/offers").json()[0]
                expected = source[url]["offers"][0]
                self.assertEqual((offer["store"], offer["url"], offer["price"], offer["size"]),
                                 (store, expected["url"], expected["history"][-1][1], expected["size"]))

    def test_visual_pattern_browse(self):
        tiles = self.client.get("/shop/api/patterns").json()
        self.assertEqual(len(tiles), 7)
        for tile in tiles:
            results = self.client.get("/search", params={"q": "", "pattern": tile["key"]}).json()
            self.assertEqual(len(results), tile["count"])
            self.assertIn(tile["image_url"], {row["image_url"] for row in results})
            self.assertTrue(tile["ar"] and tile["en"])
        self.assertEqual(self.client.get("/search", params={"q": "", "pattern": "unknown"}).status_code, 422)

    def test_manual_similarity_levels(self):
        for query in ("TELEGRAFLINJE", "Genelle"):
            pid = self.client.get("/search", params={"q": query}).json()[0]["id"]
            path = f"/shop/api/products/{pid}/alternatives"
            ranges = []
            for cutoff in (0.90, 0.88, 0.86, 0.84):
                rows = self.client.get(f"/products/{pid}/similar", params={"minimum": cutoff}).json()
                if not ranges or rows != ranges[-1]:
                    ranges.append(rows)
            if query == "TELEGRAFLINJE":
                self.assertEqual([len(rows) for rows in ranges], [0, 3])
            for step, expected in enumerate(ranges):
                data = self.client.get(path, params={"step": step}).json()
                self.assertEqual(data["products"], expected)
                if step:
                    self.assertEqual(expected[:len(ranges[step - 1])], ranges[step - 1])
                    self.assertGreater(len(expected), len(ranges[step - 1]))
                extra = len(ranges[step + 1]) - len(expected) if step + 1 < len(ranges) else 0
                self.assertEqual(data["next_additions"], extra)
                self.assertEqual(data["can_widen"], extra > 0)
                self.assertEqual(data["broadened"], step > 0)
            self.assertEqual(self.client.get(path, params={"step": len(ranges)}).status_code, 422)

    def test_legacy_saved_item_is_excluded(self):
        with connect_db(self.password) as conn:
            pid = conn.execute("SELECT id FROM products WHERE model_key='legacy:x'").fetchone()[0]
            conn.execute("INSERT INTO buywise_saved_items(product_id) VALUES (%s)", (pid,))
        try:
            self.assertNotIn(pid, [r["id"] for r in self.client.get("/shop/api/list").json()])
            self.assertEqual(self.client.post(f"/shop/api/list/{pid}").status_code, 404)
        finally:
            self.client.delete(f"/shop/api/list/{pid}")

    def test_optional_headphone_cleanup(self):
        from remove_old_headphones import remove_selected

        with connect_db(self.password) as c, c.transaction(force_rollback=True):
            pid = c.execute("SELECT id FROM products WHERE model_key='legacy:x'").fetchone()[0]
            oid = c.execute("""
                INSERT INTO offers(product_id,store,seller,listing_key,url,page_token,currency)
                VALUES (%s,'test','test','legacy-test','','','SAR') RETURNING id
            """, (pid,)).fetchone()[0]
            c.execute("INSERT INTO price_observations VALUES (%s,now(),25)", (oid,))
            c.execute("INSERT INTO buywise_saved_items(product_id) VALUES (%s)", (pid,))
            c.execute("INSERT INTO discovery_cache(query_key,category,product_ids) VALUES ('cleanup','headphones',%s)",
                      ([pid, self.ref.id],))
            with self.assertRaises(ValueError):
                remove_selected(c, {self.ref.id: self.ref.title})
            self.assertEqual(remove_selected(c, {pid: "Old headset"}), 1)
            self.assertIsNone(c.execute("SELECT id FROM products WHERE id=%s", (pid,)).fetchone())
            self.assertEqual(c.execute("SELECT product_ids FROM discovery_cache WHERE query_key='cleanup'").fetchone()[0],
                             [self.ref.id])
            self.assertEqual(c.execute("SELECT count(*) FROM products WHERE category='rugs'").fetchone()[0], self.count)
            self.assertEqual(c.execute("SELECT count(*) FROM price_observations").fetchone()[0], self.history_count)

    def test_shop_settings_and_discovery_results(self):
        config = self.client.get("/shop/api/settings").json()
        self.assertFalse(config["online_enabled"])
        self.assertEqual(set(config), {"online_enabled", "merchant_email"})
        with patch("buywise_api.discover_products", return_value=[(self.ref.id, self.ref.title)]), patch("buywise_api.find_spec", return_value=None):
            batch = self.client.post("/discover", params={"q": "woven rug", "category": "rugs"}).json()
        self.assertEqual(batch["product_ids"], [self.ref.id])
        info = self.client.get("/shop/api/product-info", params={"ids": self.ref.id}).json()[0]
        self.assertEqual((info["title"], info["image_url"]), (self.ref.title, self.ref.image))

    def test_hosted_shortlist_and_online_budget(self):
        origin = "https://buywise.example"
        with patch.dict(os.environ, BUYWISE_SERP_PER_VISITOR="1", BUYWISE_SERP_DAILY="2"):
            app = build_app(self.password, "test-key", public_origin=origin)
        alice = TestClient(app, base_url=origin)
        bob = TestClient(app, base_url=origin)
        cara = TestClient(app, base_url=origin)
        try:
            cookie = alice.get("/shop").headers["set-cookie"]
            self.assertTrue({"httponly", "secure"} <= set(cookie.lower().split("; ")))
            self.assertEqual(alice.get("/healthz").json(), {"status": "ok"})
            self.assertTrue(alice.get("/shop/api/settings").json()["online_enabled"])
            item_path = f"/shop/api/list/{self.ref.id}"
            self.assertEqual(alice.post(item_path, headers={"Origin": "https://other.example"}).status_code, 403)
            self.assertEqual(alice.post(item_path, headers={"Origin": origin}).status_code, 200)
            alice.put(item_path, json={"note": "Private shortlist", "target_price": "100"})
            self.assertEqual(bob.get("/shop/api/list").json(), [])
            self.assertEqual(bob.put(item_path, json={"note": "Other client"}).status_code, 404)
            bob.delete(item_path)
            self.assertEqual(alice.get("/shop/api/list").json()[0]["note"], "Private shortlist")
            info = bob.get("/shop/api/product-info", params={"ids": self.ref.id})
            self.assertFalse(info.json()[0]["saved"])
            self.assertEqual(info.headers["cache-control"], "private, no-store")

            def discover(client, query):
                return client.post("/discover", params={"q": query, "category": "rugs"})

            with patch("buywise_discover.request", return_value={"shopping_results": []}) as provider:
                self.assertEqual(discover(alice, "test host cache").status_code, 200)
                self.assertEqual(discover(alice, "test host cache").status_code, 200)
                self.assertEqual(discover(alice, "test host other").status_code, 429)
                self.assertEqual(discover(bob, "test host second").status_code, 200)
                self.assertEqual(discover(cara, "test host third").status_code, 429)
                self.assertEqual(provider.call_count, 2)
                self.assertEqual(provider.call_args.args[0], "test-key")
        finally:
            alice.delete(f"/shop/api/list/{self.ref.id}")
            with connect_db(self.password) as c:
                c.execute("DELETE FROM discovery_cache WHERE query_key LIKE 'test host %'")
                c.execute("DELETE FROM buywise_provider_usage")

    def test_strict_threshold(self):
        ref = self.ref
        url = f"/products/{ref.id}/similar"
        result = self.client.get(url).json()
        scores = [r["similarity"] for r in result]
        self.assertGreater(len(scores), 3)
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(s > 0.90 for s in scores))
        self.assertEqual(self.client.get(url, params={"minimum": 1}).json(), [])
        boundary = self.client.get(url, params={"minimum": scores[0]}).json()
        self.assertNotIn(result[0]["id"], [p["id"] for p in boundary])
        self.assertEqual(self.client.get(url, params={"minimum": 1.1}).status_code, 422)

    def test_variant_exclusion(self):
        mason = next(r for r in self.items.values() if r.key == "Home Centre:168574057")
        out = self.client.get(
            f"/products/{mason.id}/similar", params={"minimum": -1}
        ).json()
        self.assertEqual(len(out), self.design_count - 1)
        self.assertTrue(
            all(self.items[row["id"]].design != mason.design for row in out)
        )

    def test_shop_flow(self):
        pid = self.ref.id
        path = "/shop/api/list/" + str(pid)
        self.assertEqual(self.client.post(path).status_code, 200)
        self.assertEqual(
            self.client.put(
                path, json={"target_price": "300", "note": "Study rug"}
            ).status_code,
            200,
        )
        item = self.client.get("/shop/api/list").json()[0]
        self.assertEqual(
            (item["size"], item["note"], item["latest_price"]),
            ("160 × 230 cm", "Study rug", "279.00"),
        )
        self.assertEqual(
            self.client.get("/shop/api/product-info", params={"ids": pid}).json()[0][
                "store"
            ],
            "Home Centre",
        )
        offer = self.client.get(f"/shop/api/products/{pid}/offers").json()[0]
        self.assertTrue(offer["observed_at"])
        self.assertEqual(
            len(
                self.client.get(
                    "/price-history", params={"offer_id": offer["id"]}
                ).json()["readings"]
            ),
            2,
        )
        self.assertEqual(self.client.delete(path).status_code, 200)

    def test_failure_keeps_history(self):
        with patch("buywise_refresh_db.fetch_page", side_effect=OSError("offline")):
            self.assertEqual(
                refresh(self.password, product_id=self.ref.id), {"failed": 1}
            )
        with (
            patch("buywise_refresh_db.fetch_page", return_value=""),
            patch(
                "buywise_refresh_db.read_listing",
                return_value={
                    "key": self.ref.key,
                    "store": "Home Centre",
                    "title": self.ref.title,
                    "size": "200 × 290 cm",
                    "color": "Grey",
                    "price": "1",
                },
            ),
        ):
            self.assertEqual(
                refresh(self.password, product_id=self.ref.id), {"failed": 1}
            )
        with connect_db(self.password) as c:
            self.assertEqual(
                c.execute("SELECT count(*) FROM price_observations").fetchone()[0], self.history_count
            )

    def test_discovery_cache_and_scope(self):
        image = "https://example.gstatic.com/rug.jpg"
        payload = {
            "shopping_results": [
                dict(
                    product_id="test123", title="Oriental Rug 60x90 cm", thumbnail=image
                ),
                dict(product_id="test124", title="Rug pad", thumbnail=image),
            ]
        }
        with patch("buywise_discover.request", return_value=payload) as mock:
            rows = discover_products("oriental", "rugs", self.password, "offline-test")
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                discover_products("oriental", "rugs", self.password, ""), rows
            )
            self.assertEqual(mock.call_count, 1)
            self.assertEqual(mock.call_args.kwargs["gl"], "sa")
        with connect_db(self.password) as c:
            c.execute("DELETE FROM discovery_cache WHERE query_key='oriental'")
            c.execute("DELETE FROM products WHERE model_key='google:test123'")
        with self.assertRaises(ValueError):
            discover_products("rug", "mice", self.password, "")

    def test_online_rug_is_indexed_on_first_comparison(self):
        from buywise_retrieval import ONLINE_REVISION

        with connect_db(self.password) as conn:
            pid = conn.execute("""
                INSERT INTO products(model_key, title, category, image_url)
                VALUES ('google:comparison-test', 'Test rug', 'rugs',
                        'https://gstatic.com/test.jpg') RETURNING id
            """).fetchone()[0]
        path = f"/shop/api/products/{pid}/alternatives"
        try:
            # Use an opposite embedding so the default range is empty.
            vector = [-x for x in self.ref.vector]
            with patch("buywise_online_image.download_image", return_value=(object(), "fixture")), \
                 patch("buywise_online_image.image_embedding", return_value=vector) as encode:
                first = self.client.get(path).json()
                wider = self.client.get(path, params={"step": 1}).json()
                self.assertEqual(first["products"], [])
                self.assertTrue(first["can_widen"])
                self.assertTrue(wider["closest_available"])
                self.assertEqual(len(wider["products"]), 3)
                encode.assert_called_once()
            with connect_db(self.password) as conn:
                self.assertNotIn(pid, catalog(conn))
                self.assertEqual(conn.execute(
                    "SELECT model_revision FROM product_image_embeddings WHERE product_id=%s",
                    (pid,),
                ).fetchone()[0], ONLINE_REVISION)
        finally:
            with connect_db(self.password) as conn:
                conn.execute("DELETE FROM product_image_embeddings WHERE product_id=%s", (pid,))
                conn.execute("DELETE FROM products WHERE id=%s", (pid,))

    def test_stale_embedding_excluded(self):
        with connect_db(self.password) as conn:
            conn.execute(
                "UPDATE products SET image_url=%s WHERE id=%s",
                ("https://www.ikea.com/new.jpg", self.ref.id),
            )
        try:
            self.assertEqual(
                self.client.get(f"/products/{self.ref.id}/similar").status_code, 404
            )
        finally:
            with connect_db(self.password) as conn:
                conn.execute(
                    "UPDATE products SET image_url=%s WHERE id=%s",
                    (self.ref.image, self.ref.id),
                )

    def test_offer_scan_fixture(self):
        title = "Test rug 60x90 cm"
        with connect_db(self.password) as conn:
            pid = conn.execute(
                """INSERT INTO products(model_key,title,category,size_label,discovery_token)
                VALUES ('test:scan',%s,'rugs','60 × 90 cm','fixture') RETURNING id""",
                (title,),
            ).fetchone()[0]
        client = TestClient(build_app(self.password, "unused-fixture-key"))
        path = f"/shop/api/products/{pid}/scan-offers"
        payload = {
            "product_results": {
                "stores": [
                    {
                        "link": "https://www.ikea.com/sa/en/p/test-rug-12345678/",
                        "name": "IKEA",
                        "price": "SAR 65",
                        "extracted_price": 65,
                        "title": title,
                    },
                    {
                        "link": "https://www.ikea.com/sa/en/p/test-rug-12345679/",
                        "name": "IKEA",
                        "price": "SAR 75",
                        "extracted_price": 75,
                        "title": "Test rug 80x90 cm",
                    },
                ]
            }
        }
        try:
            with patch(
                "buywise_plus.fetch_offers", return_value=parse_offers(payload)
            ) as mock:
                data = client.post(path).json()
                self.assertEqual((data["saved"], data["skipped"]), (1, 1))
                self.assertTrue(client.post(path).json()["cached"])
                self.assertEqual(mock.call_count, 1)
            self.assertEqual(
                client.get(f"/shop/api/products/{pid}/offers").json()[0]["price"],
                "65.00",
            )
        finally:
            with connect_db(self.password) as conn:
                conn.execute(
                    "DELETE FROM price_observations WHERE offer_id IN (SELECT id FROM offers WHERE product_id=%s)",
                    (pid,),
                )
                conn.execute("DELETE FROM offers WHERE product_id=%s", (pid,))
                conn.execute("DELETE FROM products WHERE id=%s", (pid,))

    def test_pooled_evaluation(self):
        data = experiment(self.items)
        self.assertFalse(set(PILOT) & set(EVALUATION))
        self.assertEqual(len(data["cases"]), 8)
        for c in data["cases"]:
            self.assertEqual(
                {i["key"] for i in c["pool"]},
                set(c["top"]["title"]) | set(c["top"]["image"]),
            )
            self.assertNotIn(c["reference"], [i["key"] for i in c["pool"]])
            self.assertTrue(all(len(v) <= 5 for v in c["top"].values()))
