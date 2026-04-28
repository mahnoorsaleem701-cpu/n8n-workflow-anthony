import scrapy
import json
import re
from datetime import datetime, timezone


class TheRealRealSpider(scrapy.Spider):
    name = "therealreal_handbag"

    custom_settings = {
        # ScrapeOps SDK config — replaces manual URL wrapping
        "SCRAPEOPS_API_KEY": "55ec0ebf-c5a2-4e71-8275-5eb5225eb6ff",
        "SCRAPEOPS_PROXY_ENABLED": True,
        "SCRAPEOPS_PROXY_SETTINGS": {
            "render_js": True,
            "residential": True,
            "premium": "level_2",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "scrapeops_scrapy_proxy_sdk.scrapeops_scrapy_proxy_sdk.ScrapeOpsScrapyProxySdk": 725,
        },

        # Scrapy tuning for a JS-rendering proxy
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 4,
        "DOWNLOAD_DELAY": 0,
        "DOWNLOAD_TIMEOUT": 180,
        "RETRY_TIMES": 4,
        "RETRY_HTTP_CODES": [403, 408, 425, 429, 500, 502, 503, 504, 522, 524],
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1,
        "AUTOTHROTTLE_MAX_DELAY": 10,
        "FEED_EXPORT_ENCODING": "utf-8-sig",

        # Stops Scrapy from re-encoding the URL the proxy passes through
        "URLLENGTH_LIMIT": 10000,
    }

    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
    }

    DESIGNER_MAP = {"259": "Celine"}

    CATEGORIES = {
        "548": "Backpacks", "1473": "Bucket Bags", "539": "Clutches",
        "542": "Crossbody Bags", "547": "Evening Bags", "545": "Handle Bags",
        "543": "Hobos", "1472": "Luggage and Travel", "1471": "Mini Bags",
        "546": "Satchels", "537": "Shoulder Bags", "538": "Totes",
        "1601": "Waist Bags",
    }

    CONDITIONS = {
        "18": "Pristine", "19": "Excellent", "20": "Very Good",
        "30560": "Good", "655545": "Fair", "2988045": "As Is",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.brand_items = {}

    def start_requests(self):
        for designer_id, brand_name in self.DESIGNER_MAP.items():
            for taxon_id, category_name in self.CATEGORIES.items():
                for condition_id, condition_name in self.CONDITIONS.items():
                    url = (
                        "https://www.therealreal.com/shop/women/handbags"
                        f"?taxons[]={taxon_id}"
                        f"&designer[]={designer_id}"
                        f"&condition[]={condition_id}"
                    )
                    yield scrapy.Request(
                        url=url,
                        headers=self.headers,
                        callback=self.parse,
                        meta={
                            "designer_id": designer_id,
                            "brand_name": brand_name,
                            "category_name": category_name,
                            "condition_name": condition_name,
                        },
                        dont_filter=True,
                    )

    def parse(self, response):
        meta = response.meta
        if response.status >= 400:
            self.logger.warning(f"Bad response {response.status}: {response.url}")
            return

        product_links = response.xpath(
            '//a[@data-testid="product-card/description"]/@href'
        ).getall()

        self.logger.info(
            f"Found {len(product_links)} → "
            f"{meta['brand_name']} | {meta['category_name']} | {meta['condition_name']}"
        )

        for link in product_links:
            yield scrapy.Request(
                url=response.urljoin(link),
                headers=self.headers,
                callback=self.parse_product_detail,
                meta={
                    "designer_id": meta["designer_id"],
                    "brand_name": meta["brand_name"],
                    "category_name": meta["category_name"],
                    "condition_name": meta["condition_name"],
                },
                dont_filter=True,
            )

        next_page = response.xpath(
            '//a[contains(@aria-label, "Go to Next Page")]/@href'
        ).get()

        if next_page:
            yield scrapy.Request(
                url=response.urljoin(next_page),
                headers=self.headers,
                callback=self.parse,
                meta=meta,
                dont_filter=True,
            )

    def parse_product_detail(self, response):
        designer_id = response.meta.get("designer_id")
        category_name = response.meta.get("category_name")

        if response.status >= 400:
            self.logger.warning(f"Bad product {response.status}: {response.url}")
            return

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        if not match:
            self.logger.warning(f"No __NEXT_DATA__: {response.url}")
            return

        try:
            data = json.loads(match.group(1))
            product = data["props"]["pageProps"]["product"]
        except Exception as e:
            self.logger.warning(f"JSON parse failed: {response.url} | {e}")
            return

        brand = (
            (product.get("brand") or {}).get("name")
            or (product.get("designer") or {}).get("name")
            or (product.get("brandUnion") or {}).get("name")
        )

        title = product.get("name")
        product_url = product.get("url") or response.url

        taxons = product.get("taxons", []) or []
        category = sub_category = None
        if taxons:
            root_taxon = min(taxons, key=lambda t: len(t.get("permalink", "")))
            leaf_taxon = max(taxons, key=lambda t: len(t.get("permalink", "")))
            category = root_taxon.get("name")
            sub_category = leaf_taxon.get("name")

        seen, image_urls = set(), []
        for img in product.get("images", []) or []:
            u = img.get("url")
            if u and u not in seen:
                seen.add(u)
                image_urls.append(u)

        price = None
        try:
            price = float(
                ((product.get("price") or {}).get("final") or {}).get("unformatted")
            )
        except Exception:
            pass

        size = gender = None
        for attr in product.get("attributes", []) or []:
            values = attr.get("values", [])
            if attr.get("type") == "size" and values:
                size = values[0]
            if attr.get("type") == "gender" and values:
                gender = values[0]

        sold = product.get("availability", "") != "AVAILABLE"

        item = {
            "brand": brand,
            "title": title,
            "category": category,
            "sub_category": sub_category,
            "filter_category": category_name,
            "product_url": product_url,
            "image_url": image_urls[0] if image_urls else None,
            "image_urls": " | ".join(image_urls),
            "condition": product.get("condition"),
            "price": price,
            "currency": "USD",
            "source": "therealreal",
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": size,
            "description": product.get("description"),
            "gender": gender,
            "sold": sold,
        }

        brand_key = self.DESIGNER_MAP.get(designer_id) or brand or f"designer_{designer_id}"
        self.brand_items.setdefault(brand_key, {})
        if product_url not in self.brand_items[brand_key]:
            self.brand_items[brand_key][product_url] = item
            self.logger.info(f"Saved: {product_url}")

        yield item

    def closed(self, reason):
        try:
            import openpyxl
        except ImportError:
            self.logger.error("openpyxl missing. pip install openpyxl")
            return

        if not self.brand_items:
            self.logger.info("No items collected.")
            return

        columns = [
            "brand", "title", "category", "sub_category", "filter_category",
            "product_url", "image_url", "image_urls", "condition", "price",
            "currency", "source", "scraped_at", "size", "description",
            "gender", "sold",
        ]

        for brand_name, url_item_map in self.brand_items.items():
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", brand_name)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = safe_name[:31]
            ws.append(columns)
            for it in url_item_map.values():
                ws.append([it.get(c, "") for c in columns])
            wb.save(f"{safe_name}.xlsx")
            self.logger.info(f"Saved {len(url_item_map)} → {safe_name}.xlsx")
