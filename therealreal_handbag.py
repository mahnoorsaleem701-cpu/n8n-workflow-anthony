import scrapy
import json
import re
from datetime import datetime, timezone

class TheRealRealSpider(scrapy.Spider):
    name = "therealreal_handbag"

    custom_settings = {
        'ROBOTSTXT_OBEY': False,

        # ── ScrapeOps proxy ─────────────────────────────────────────────
        # Keep proxy-side settings in ONE place. Either use
        # SCRAPEOPS_PROXY_SETTINGS globally, OR per-request `sops_*` meta
        # keys — mixing both duplicates query params and triggers 500s.
        'SCRAPEOPS_API_KEY': '55ec0ebf-c5a2-4e71-8275-5eb5225eb6ff',
        'SCRAPEOPS_PROXY_ENABLED': True,
        'SCRAPEOPS_PROXY_SETTINGS': {
            'country': 'us',
            'render_js': True,
        },

        # ── Concurrency / throttling ────────────────────────────────────
        # ScrapeOps free / starter plans cap concurrency at 1. Going higher
        # returns 500/429 from the proxy endpoint. Raise this only if your
        # plan allows it.
        'CONCURRENT_REQUESTS': 1,
        'DOWNLOAD_DELAY': 1,
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 1,
        'AUTOTHROTTLE_MAX_DELAY': 30,
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 1.0,

        # ── Retries ─────────────────────────────────────────────────────
        # Default RETRY_HTTP_CODES does NOT include 500, so transient
        # ScrapeOps 5xx responses fail immediately. Add them here.
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 5,
        'RETRY_HTTP_CODES': [408, 429, 500, 502, 503, 504, 522, 524],
        'DOWNLOAD_TIMEOUT': 180,

        'FEED_EXPORT_ENCODING': 'utf-8-sig',

        # ── Middlewares ─────────────────────────────────────────────────
        # The ScrapeOps proxy SDK rewrites requests to proxy.scrapeops.io,
        # so HttpProxyMiddleware must stay disabled. The ScrapeOps retry
        # middleware replaces Scrapy's default so it can classify proxy
        # errors correctly — do not enable both.
        'DOWNLOADER_MIDDLEWARES': {
            'scrapeops_scrapy_proxy_sdk.scrapeops_scrapy_proxy_sdk.ScrapeOpsScrapyProxySdk': 725,
            'scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware': None,
            'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,
            'scrapeops_scrapy.middleware.retry.RetryMiddleware': 550,
        },

        'EXTENSIONS': {
            'scrapeops_scrapy.extension.ScrapeOpsMonitor': 500,
        },
    }

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'max-age=0',
        'priority': 'u=0, i',
        'referer': 'https://www.therealreal.com/shop/women/handbags',
        'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
    }

    DESIGNER_MAP = {
        '937':   'Miu Miu',
        '941':   'Moncler',
        '952':   'Moschino',
        '963':   'Mulberry',
        '6680':  'Off-White',
        '1080':  'Prada',
        '1122':  'Rebecca Minkoff',
        '1593':  'Saint Laurent',
        '1204':  'Salvatore Ferragamo',
        '1266':  'Stella Mccartney',
    }

    CATEGORIES = {
        '548':  'Backpacks',
        '1473': 'Bucket Bags',
        '539':  'Clutches',
        '542':  'Crossbody Bags',
        '547':  'Evening Bags',
        '545':  'Handle Bags',
        '543':  'Hobos',
        '1472': 'Luggage and Travel',
        '1471': 'Mini Bags',
        '546':  'Satchels',
        '537':  'Shoulder Bags',
        '538':  'Totes',
        '1601': 'Waist Bags',
    }

    # NOTE: The IDs 30560 / 655545 / 2988045 in the previous version were
    # invalid on TheRealReal and caused the backend to 500 (relayed as a
    # ScrapeOps 500). Condition IDs on TRR are a contiguous sequence.
    CONDITIONS = {
        '18': 'Pristine',
        '19': 'Excellent',
        '20': 'Very Good',
        '21': 'Good',
        '22': 'Fair',
        '23': 'As Is',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.brand_items: dict[str, dict[str, dict]] = {}

    async def start(self):
        for designer_id, brand_name in self.DESIGNER_MAP.items():
            for taxon_id, category_name in self.CATEGORIES.items():
                for condition_id, condition_name in self.CONDITIONS.items():
                    url = (
                        f'https://www.therealreal.com/shop/women/handbags'
                        f'?taxons%5B%5D={taxon_id}'
                        f'&designer%5B%5D={designer_id}'
                        f'&condition%5B%5D={condition_id}'
                    )
                    self.logger.info(
                        f"Queuing → {brand_name} | {category_name} | {condition_name}"
                    )
                    yield scrapy.Request(
                        url=url,
                        headers=self.headers,
                        callback=self.parse,
                        meta={
                            'designer_id': designer_id,
                            'brand_name': brand_name,
                            'category_name': category_name,
                            'condition_name': condition_name,
                        },
                    )

    def parse(self, response):
        designer_id    = response.meta.get('designer_id')
        brand_name     = response.meta.get('brand_name')
        category_name  = response.meta.get('category_name')
        condition_name = response.meta.get('condition_name')

        product_links = response.xpath(
            '//a[@data-testid="product-card/description"]/@href'
        ).getall()

        self.logger.info(
            f"Found {len(product_links)} products on page → "
            f"{brand_name} | {category_name} | {condition_name}"
        )

        for link in product_links:
            yield response.follow(
                link,
                callback=self.parse_product_detail,
                headers=self.headers,
                meta={
                    'designer_id':    designer_id,
                    'brand_name':     brand_name,
                    'category_name':  category_name,
                    'condition_name': condition_name,
                },
            )

        next_page = response.xpath(
            '//a[contains(@aria-label, "Go to Next Page")]/@href'
        ).get()

        if next_page:
            self.logger.info(
                f"Following next page → {brand_name} | {category_name} | {condition_name}"
            )
            yield response.follow(
                next_page,
                callback=self.parse,
                meta={
                    'designer_id':    designer_id,
                    'brand_name':     brand_name,
                    'category_name':  category_name,
                    'condition_name': condition_name,
                },
            )
        else:
            self.logger.info(
                f"No next page → {brand_name} | {category_name} | {condition_name}"
            )

    def parse_product_detail(self, response):
        designer_id    = response.meta.get('designer_id')
        brand_name     = response.meta.get('brand_name')
        category_name  = response.meta.get('category_name')
        condition_name = response.meta.get('condition_name')

        raw_json = response.body.decode('utf-8')
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            raw_json,
            re.DOTALL,
        )
        if not match:
            self.logger.warning(f"No __NEXT_DATA__ found on {response.url}")
            return

        data = json.loads(match.group(1))
        product = data['props']['pageProps']['product']

        brand = (
            product.get('brand', {}).get('name')
            or product.get('designer', {}).get('name')
            or product.get('brandUnion', {}).get('name')
        )

        title = product.get('name')

        taxons = product.get('taxons', [])
        if taxons:
            root_taxon = min(taxons, key=lambda t: len(t.get('permalink', '')))
            category = root_taxon.get('name')
            leaf_taxon = max(taxons, key=lambda t: len(t.get('permalink', '')))
            sub_category = leaf_taxon.get('name')
        else:
            category = None
            sub_category = None

        product_url = product.get('url') or response.url

        images = product.get('images', [])
        seen_urls = set()
        image_urls = []
        for img in images:
            url = img.get('url')
            if url and url not in seen_urls:
                seen_urls.add(url)
                image_urls.append(url)

        condition = product.get('condition')

        price_data  = product.get('price', {})
        final_price = price_data.get('final', {})
        try:
            price = float(final_price.get('unformatted', 0))
        except (ValueError, TypeError):
            price = None
        currency = 'USD'

        source     = 'therealreal'
        scraped_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        size = None
        for attr in product.get('attributes', []):
            if attr.get('type') == 'size':
                values = attr.get('values', [])
                size = values[0] if values else None
                break

        description = product.get('description')

        gender = None
        for attr in product.get('attributes', []):
            if attr.get('type') == 'gender':
                values = attr.get('values', [])
                gender = values[0] if values else None
                break

        availability = product.get('availability', '')
        sold = availability != 'AVAILABLE'

        item = {
            'brand':           brand,
            'title':           title,
            'category':        category,
            'sub_category':    sub_category,
            'filter_category': category_name,
            'product_url':     product_url,
            'image_url':       image_urls[0] if image_urls else None,
            'image_urls':      ' | '.join(image_urls),
            'condition':       condition,
            'price':           price,
            'currency':        currency,
            'source':          source,
            'scraped_at':      scraped_at,
            'size':            size,
            'description':     description,
            'gender':          gender,
            'sold':            sold,
        }

        brand_key = self.DESIGNER_MAP.get(designer_id) or (brand or f'designer_{designer_id}').strip()
        if brand_key not in self.brand_items:
            self.brand_items[brand_key] = {}

        if product_url not in self.brand_items[brand_key]:
            self.brand_items[brand_key][product_url] = item
            self.logger.info(f"New product saved: {product_url}")
        else:
            self.logger.debug(f"Duplicate skipped: {product_url}")

        yield item

    def closed(self, reason):
        try:
            import openpyxl
        except ImportError:
            self.logger.error("openpyxl not installed — run: pip install openpyxl")
            return

        if not self.brand_items:
            self.logger.info("No items collected, skipping Excel export.")
            return

        COLUMNS = [
            'brand', 'title', 'category', 'sub_category', 'filter_category',
            'product_url', 'image_url', 'image_urls', 'condition', 'price',
            'currency', 'source', 'scraped_at', 'size', 'description',
            'gender', 'sold',
        ]

        for brand_name, url_item_map in self.brand_items.items():
            items = list(url_item_map.values())
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', brand_name)
            filename  = f"{safe_name}.xlsx"

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = safe_name[:31]

            ws.append(COLUMNS)

            for row in items:
                ws.append([row.get(col, '') for col in COLUMNS])

            wb.save(filename)
            self.logger.info(f"Saved {len(items)} unique rows → {filename}")
