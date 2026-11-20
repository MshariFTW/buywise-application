"""Read a single explicitly identified Saudi retail listing and its current SAR price."""

import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from buywise_retrieval import dimensions


def retailer_key(url):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port
        or p.query
        or p.fragment
    ):
        raise ValueError("Unsupported retailer URL")
    host = p.hostname
    if host == "www.ikea.com":
        m = re.fullmatch(r"/sa/en/p/[a-z0-9-]+-(\d{8})/?", p.path)
        store = "IKEA"
    elif host == "www.homecentre.com":
        m = re.fullmatch(r"/sa/en/[^/]+/p/(\d{9})/?", p.path)
        store = "Home Centre"
    else:
        raise ValueError("Unsupported retailer")
    if not m:
        raise ValueError("No unambiguous Saudi SKU")
    return store, m[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.ld = []
        self.buffer = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("property", "")] = attrs.get("content")
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.buffer = []

    def handle_data(self, data):
        if self.buffer is not None:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.buffer is not None:
            self.ld.append(json.loads("".join(self.buffer)))
            self.buffer = None


class SameRetailer(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if retailer_key(newurl) != retailer_key(req.full_url):
            raise ValueError("Retailer redirected to another listing")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url):
    retailer_key(url)
    with build_opener(SameRetailer()).open(
        Request(url, headers={"User-Agent": "BuyWise/1.0"}), timeout=25
    ) as r:
        data = r.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("Unexpected retailer response size")
    return data.decode("utf-8")


def read_listing(url, html):
    store, sku = retailer_key(url)
    page = Page()
    page.feed(html)
    products = [
        p for p in page.ld if isinstance(p, dict) and p.get("@type") == "Product"
    ]
    if len(products) != 1:
        raise ValueError("No unique product in retailer page")
    p = products[0]
    offer = p["offers"]
    if not isinstance(offer, dict):
        raise ValueError("Expected one SKU-specific offer")
    if (
        retailer_key(offer["url"]) != (store, sku)
        or offer.get("priceCurrency") != "SAR"
    ):
        raise ValueError("Listing identity or currency changed")
    if offer.get("availability", "").split("/")[-1] not in {
        "InStock",
        "LimitedAvailability",
    }:
        raise ValueError("Listing is not available")
    if store == "IKEA" and (
        p.get("sku", "").replace(".", "") != sku
        or offer.get("seller", {}).get("name") != "IKEA"
    ):
        raise ValueError("IKEA article or seller mismatch")
    if store == "Home Centre" and (
        p.get("brand", {}).get("name") != store
        or offer.get("seller", {}).get("name", store) != store
    ):
        raise ValueError("Unexpected Home Centre brand")
    price = Decimal(str(page.meta.get("product:price:sale_price") or offer["price"]))
    if (
        not price.is_finite()
        or not 0 < price < 10**10
        or price != price.quantize(Decimal(".01"))
    ):
        raise ValueError("Invalid SAR price")
    size = dimensions(p["name"])
    if not size:
        raise ValueError("Listing size is unavailable")
    return {
        "key": f"{store}:{sku}",
        "store": store,
        "sku": sku,
        "title": p["name"],
        "size": size,
        "color": p.get("color", ""),
        "price": str(price),
        "url": url,
    }
