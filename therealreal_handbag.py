import scrapy
import json
import re
from datetime import datetime, timezone

# ── ScraperAPI configuration ────────────────────────────────────────────
# Replace with your real ScraperAPI key, or set SCRAPERAPI_KEY in the env
# and read it via os.environ.
SCRAPERAPI_KEY = 'YOUR_SCRAPERAPI_KEY'

# ScraperAPI feature flags are embedded in the proxy username:
#   scraperapi.<flag>=<value>.<flag>=<value>...
# Docs: https://docs.scraperapi.com/python/making-requests/proxy-mode
SCRAPERAPI_PROXY = (
    f'http://scraperapi.render=true.country_code=us'
    f':{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001'
)


class TheRealRealSpider(scrapy.Spider):
    name = "therealreal_handbag"

    custom_settings = {
        'ROBOTSTXT_OBEY': False,

        # ── Concurrency / throttling ────────────────────────────────────
        # ScraperAPI free tier allows 5 concurrent requests; starter 10.
        # Bump this up to match your plan.
        'CONCURRENT_REQUESTS': 5,
        'DOWNLOAD_DELAY': 1,
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 1,
        'AUTOTHROTTLE_MAX_DELAY': 30,
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 2.0,

        # ── Retries ─────────────────────────────────────────────────────
        # ScraperAPI returns 500 when the target site fails, and 429 when
        # you hit your concurrency limit. Both need to be retried.
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 5,
        'RETRY_HTTP_CODES': [408, 429, 500, 502, 503, 504, 522, 524],

        # render=true can take 30-60s. Default 180 is enough, keep it.
        'DOWNLOAD_TIMEOUT': 180,

        'FEED_EXPORT_ENCODING': 'utf-8-sig',

        # HttpProxyMiddleware is enabled by default — leave it ON so the
        # per-request `proxy` meta is honoured. No custom middlewares
        # needed for ScraperAPI in proxy mode.
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
                            'proxy': SCRAPERAPI_PROXY,
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
                    'proxy':          SCRAPERAPI_PROXY,
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
                    'proxy':          SCRAPERAPI_PROXY,
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
