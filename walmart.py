import scrapy
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

# ── ScrapeOps configuration ─────────────────────────────────────────────
# Replace with your real ScrapeOps key, or set SCRAPEOPS_API_KEY in the
# env and read it via os.environ.
SCRAPEOPS_API_KEY = 'YOUR_SCRAPEOPS_API_KEY'

# ScrapeOps Proxy Aggregator API endpoint. Docs:
# https://scrapeops.io/docs/proxy-aggregator/getting-started/making-requests/
SCRAPEOPS_ENDPOINT = 'https://proxy.scrapeops.io/v1/'

# Default ScrapeOps params. Extra params can be merged per-request via
# `meta['scrapeops_params']`.
SCRAPEOPS_DEFAULT_PARAMS = {
    'api_key':   SCRAPEOPS_API_KEY,
    'country':   'us',
    'render_js': 'true',
    # Walmart aggressively fingerprints — ask ScrapeOps to maintain a
    # consistent residential session so anti-bot cookies stick.
    'residential': 'true',
}


def scrapeops_url(target_url: str, extra_params: dict | None = None) -> str:
    """Wrap a target URL in the ScrapeOps proxy aggregator endpoint."""
    params = dict(SCRAPEOPS_DEFAULT_PARAMS)
    if extra_params:
        params.update(extra_params)
    params['url'] = target_url
    return f'{SCRAPEOPS_ENDPOINT}?{urlencode(params)}'


class WalmartSpider(scrapy.Spider):
    name = "walmart"

    custom_settings = {
        'ROBOTSTXT_OBEY': False,

        # ── Concurrency / throttling ────────────────────────────────────
        # ScrapeOps free tier allows 1 concurrent request, hobby 5,
        # startup 10, business 25. Bump this up to match your plan.
        'CONCURRENT_REQUESTS': 5,
        'DOWNLOAD_DELAY': 1,
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 1,
        'AUTOTHROTTLE_MAX_DELAY': 30,
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 2.0,

        # ── Retries ─────────────────────────────────────────────────────
        # ScrapeOps returns 500 when the target site blocks/fails, and
        # 429 when you exceed your concurrency. Both need to be retried.
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 5,
        'RETRY_HTTP_CODES': [408, 429, 500, 502, 503, 504, 522, 524],

        # render_js=true can take 30–60s. Default 180 is enough.
        'DOWNLOAD_TIMEOUT': 180,

        'FEED_EXPORT_ENCODING': 'utf-8-sig',
    }

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'max-age=0',
        'priority': 'u=0, i',
        'referer': 'https://www.walmart.com/',
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

    # Search queries to scrape. Override on the CLI with:
    #   scrapy runspider walmart.py -a queries="laptop,headphones"
    DEFAULT_QUERIES = [
        'laptop',
        'headphones',
        'coffee maker',
        'office chair',
        'air fryer',
    ]

    # How many search-result pages to crawl per query (Walmart caps at 25).
    MAX_PAGES_PER_QUERY = 5

    def __init__(self, queries: str | None = None, max_pages: str | None = None,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        if queries:
            self.queries = [q.strip() for q in queries.split(',') if q.strip()]
        else:
            self.queries = list(self.DEFAULT_QUERIES)

        if max_pages:
            try:
                self.max_pages = max(1, int(max_pages))
            except ValueError:
                self.max_pages = self.MAX_PAGES_PER_QUERY
        else:
            self.max_pages = self.MAX_PAGES_PER_QUERY

        self.query_items: dict[str, dict[str, dict]] = {}

    async def start(self):
        for query in self.queries:
            search_url = (
                'https://www.walmart.com/search?'
                + urlencode({'q': query, 'sort': 'best_match', 'page': 1})
            )
            self.logger.info(f"Queuing search → {query} (page 1)")
            yield scrapy.Request(
                url=scrapeops_url(search_url),
                headers=self.headers,
                callback=self.parse_search,
                meta={
                    'query': query,
                    'page':  1,
                },
                dont_filter=True,
            )

    # ──────────────────────────────────────────────────────────────────
    # Search results page
    # ──────────────────────────────────────────────────────────────────
    def parse_search(self, response):
        query = response.meta.get('query')
        page  = response.meta.get('page', 1)

        next_data = self._extract_next_data(response)
        product_links: list[str] = []

        if next_data:
            try:
                items = (
                    next_data['props']['pageProps']
                    ['initialData']['searchResult']
                    ['itemStacks'][0]['items']
                )
            except (KeyError, IndexError, TypeError):
                items = []

            for it in items:
                # Skip ads, banners, and other non-product tiles.
                if it.get('__typename') and it.get('__typename') != 'Product':
                    continue
                product_url = it.get('canonicalUrl') or it.get('productPageUrl')
                if not product_url:
                    item_id = it.get('usItemId') or it.get('id')
                    if item_id:
                        product_url = f'/ip/{item_id}'
                if product_url:
                    product_links.append(product_url)

        # Fallback: pull product anchors from the rendered HTML.
        if not product_links:
            product_links = response.xpath(
                '//a[contains(@link-identifier, "linkProductTitle") '
                'or contains(@href, "/ip/")]/@href'
            ).getall()

        # Deduplicate while preserving order.
        seen = set()
        unique_links = []
        for link in product_links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        self.logger.info(
            f"Found {len(unique_links)} products → {query} (page {page})"
        )

        for link in unique_links:
            absolute = urljoin('https://www.walmart.com', link)
            yield scrapy.Request(
                url=scrapeops_url(absolute),
                headers=self.headers,
                callback=self.parse_product,
                meta={'query': query},
            )

        # Paginate.
        if page < self.max_pages and unique_links:
            next_page = page + 1
            next_url = (
                'https://www.walmart.com/search?'
                + urlencode({'q': query, 'sort': 'best_match', 'page': next_page})
            )
            self.logger.info(f"Following next page → {query} (page {next_page})")
            yield scrapy.Request(
                url=scrapeops_url(next_url),
                headers=self.headers,
                callback=self.parse_search,
                meta={'query': query, 'page': next_page},
                dont_filter=True,
            )

    # ──────────────────────────────────────────────────────────────────
    # Product detail page
    # ──────────────────────────────────────────────────────────────────
    def parse_product(self, response):
        query = response.meta.get('query')

        next_data = self._extract_next_data(response)
        if not next_data:
            self.logger.warning(f"No __NEXT_DATA__ found on {response.url}")
            return

        try:
            product = next_data['props']['pageProps']['initialData']['data']['product']
        except (KeyError, TypeError):
            product = None

        if not product:
            self.logger.warning(f"No product node on {response.url}")
            return

        # Walmart strips the proxy wrapper from response.url; rebuild the
        # canonical product URL from the payload when possible.
        canonical_url = product.get('canonicalUrl')
        if canonical_url and canonical_url.startswith('/'):
            canonical_url = urljoin('https://www.walmart.com', canonical_url)
        product_url = canonical_url or response.url

        title = product.get('name')
        brand = product.get('brand')

        # Categorisation comes from the breadcrumb trail.
        breadcrumbs = product.get('breadcrumbs') or []
        category = breadcrumbs[0]['name'] if breadcrumbs else None
        sub_category = breadcrumbs[-1]['name'] if breadcrumbs else None

        image_urls: list[str] = []
        seen_urls: set[str] = set()
        primary_img = (product.get('imageInfo') or {}).get('thumbnailUrl')
        if primary_img:
            seen_urls.add(primary_img)
            image_urls.append(primary_img)
        for img in (product.get('imageInfo') or {}).get('allImages', []) or []:
            url = img.get('url')
            if url and url not in seen_urls:
                seen_urls.add(url)
                image_urls.append(url)

        price_info = product.get('priceInfo') or {}
        current_price = (price_info.get('currentPrice') or {})
        try:
            price = float(current_price.get('price')) if current_price.get('price') is not None else None
        except (TypeError, ValueError):
            price = None
        currency = current_price.get('currencyUnit') or 'USD'

        was_price_node = price_info.get('wasPrice') or {}
        try:
            was_price = float(was_price_node.get('price')) if was_price_node.get('price') is not None else None
        except (TypeError, ValueError):
            was_price = None

        availability = (product.get('availabilityStatus') or '').upper()
        in_stock = availability == 'IN_STOCK'

        rating_info = product.get('averageRating')
        try:
            rating = float(rating_info) if rating_info is not None else None
        except (TypeError, ValueError):
            rating = None
        review_count = product.get('numberOfReviews')

        seller_info = product.get('sellerName') or product.get('sellerDisplayName')

        item_id = product.get('usItemId') or product.get('id')

        description = (
            product.get('shortDescription')
            or product.get('longDescription')
            or ''
        )
        # Strip simple HTML tags from the description text.
        description = re.sub(r'<[^>]+>', ' ', description).strip()

        scraped_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        item = {
            'query':         query,
            'item_id':       item_id,
            'title':         title,
            'brand':         brand,
            'category':      category,
            'sub_category':  sub_category,
            'product_url':   product_url,
            'image_url':     image_urls[0] if image_urls else None,
            'image_urls':    ' | '.join(image_urls),
            'price':         price,
            'was_price':     was_price,
            'currency':      currency,
            'in_stock':      in_stock,
            'availability':  availability or None,
            'rating':        rating,
            'review_count':  review_count,
            'seller':        seller_info,
            'description':   description,
            'source':        'walmart',
            'scraped_at':    scraped_at,
        }

        bucket = self.query_items.setdefault(query or '_default', {})
        if product_url not in bucket:
            bucket[product_url] = item
            self.logger.info(f"New product saved: {product_url}")
        else:
            self.logger.debug(f"Duplicate skipped: {product_url}")

        yield item

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_next_data(response) -> dict | None:
        raw = response.text
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            raw,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    # ──────────────────────────────────────────────────────────────────
    # Excel export, one workbook per query.
    # ──────────────────────────────────────────────────────────────────
    def closed(self, reason):
        try:
            import openpyxl
        except ImportError:
            self.logger.error("openpyxl not installed — run: pip install openpyxl")
            return

        if not self.query_items:
            self.logger.info("No items collected, skipping Excel export.")
            return

        COLUMNS = [
            'query', 'item_id', 'title', 'brand', 'category', 'sub_category',
            'product_url', 'image_url', 'image_urls', 'price', 'was_price',
            'currency', 'in_stock', 'availability', 'rating', 'review_count',
            'seller', 'description', 'source', 'scraped_at',
        ]

        for query, url_item_map in self.query_items.items():
            items = list(url_item_map.values())
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', query) or 'walmart'
            filename = f"walmart_{safe_name}.xlsx"

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = safe_name[:31]

            ws.append(COLUMNS)
            for row in items:
                ws.append([row.get(col, '') for col in COLUMNS])

            wb.save(filename)
            self.logger.info(f"Saved {len(items)} unique rows → {filename}")
