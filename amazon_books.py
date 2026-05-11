import os
import re
from urllib.parse import urlencode

import certifi
import scrapy

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())


# Business & Money category -> Amazon browse-node id
_CATEGORIES = {
    "Accounting": "266117",
    "Biography & History": "2538",
    "Bitcoin & Cryptocurrencies": "203569026011",
    "Business Culture": "2558",
    "Business Development & Entrepreneurship": "2741",
    "Economics": "2581",
    "Education & Reference": "2732",
    "Finance": "2604",
    "Human Resources": "10020683011",
    "Industries": "2624",
    "Insurance": "2638",
    "International": "2657",
    "Investing": "2665",
    "Job Hunting & Careers": "2572",
    "Management & Leadership": "2675",
    "Marketing & Sales": "2698",
    "Personal Finance": "2717",
    "Processes & Infrastructure": "355561011",
    "Real Estate": "2650",
    "Skills": "355562011",
    "Taxation": "7743000011",
    "Women & Business": "355578011",
}

_BASE_PARAMS = {
    "i": "stripbooks",
    "s": "relevancerank",
    "dc": "",
    "Adv-Srch-Books-Submit.x": "18",
    "Adv-Srch-Books-Submit.y": "6",
    "p_45": "4",
    "p_46": "During",
    "p_47": "2026",
    "qid": "1778474786",
    "rnid": "3",
    "unfiltered": "1",
}


def _build_search_url(node_id, idx):
    rh = (
        f"n:283155,n:3,n:{node_id},"
        "p_n_condition-type:1294423011,"
        "p_n_feature_browse-bin:2656022011,"
        "p_20:English"
    )
    params = {"rh": rh, **_BASE_PARAMS, "ref": f"sr_nr_n_{idx}"}
    return "https://www.amazon.com/s?" + urlencode(params, safe=":,")


_START_URLS = [
    _build_search_url(node_id, idx)
    for idx, node_id in enumerate(_CATEGORIES.values(), start=1)
]


class AmazonBooksSpider(scrapy.Spider):
    name = "amazon_books"
    start_urls = _START_URLS

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "RETRY_TIMES": 5,
        "DOWNLOAD_DELAY": 0,
        "CONCURRENT_REQUESTS": 16,

        # Zyte API Settings
        "ZYTE_API_KEY": "5163bb40486e43db95564765f0dab788",
        "ZYTE_API_TRANSPARENT_MODE": True,
        "ZYTE_API_EXPERIMENTAL_COOKIES_ENABLED": True,

        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_zyte_api.ScrapyZyteAPIDownloadHandler",
            "https": "scrapy_zyte_api.ScrapyZyteAPIDownloadHandler",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy_zyte_api.ScrapyZyteAPIDownloaderMiddleware": 1000,
            "scrapy_poet.InjectionMiddleware": 543,
        },
        "REQUEST_FINGERPRINTER_CLASS": "scrapy_zyte_api.ScrapyZyteAPIRequestFingerprinter",
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    }

    # ---- Precompiled regex patterns (class-level, compiled once) ----
    DATE_REGEX = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}",
        re.IGNORECASE,
    )
    _UNICODE_CLEAN_REGEX = re.compile(r"[‎​‍‪‬‭‏]")
    _ASIN_URL_REGEX = re.compile(r"(?:/dp/|/gp/product/|/aw/d/|/product/)([A-Z0-9]{10})")
    _ASIN_PRODUCT_REGEX = re.compile(r"(?:/dp/|/gp/product/|/aw/d/)[A-Z0-9]{10}")
    _BRAND_STORE_REGEX = re.compile(r"/stores/[^/]+/page/")
    _DIGITS_REGEX = re.compile(r"\D+")
    _FORMAT_SPLIT_REGEX = re.compile(r"[-–—]")

    BASE_HEADERS = {
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    DETAIL_HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "cross-site",
    }
    ZYTE_META = {"zyte_api_automap": {"geolocation": "US"}}

    # ------------------ HELPERS ------------------

    @classmethod
    def clean_text(cls, text):
        if not text:
            return None
        return cls._UNICODE_CLEAN_REGEX.sub("", text).strip() or None

    @classmethod
    def is_product_url(cls, url):
        if not url:
            return False
        if "/stores/" in url and "/author/" in url:
            return False
        if "/seller/" in url or "/shops/" in url:
            return False
        if cls._BRAND_STORE_REGEX.search(url):
            return False
        return bool(cls._ASIN_PRODUCT_REGEX.search(url))

    # ------------------ ROBUST EXTRACTORS ------------------

    def extract_asin(self, response):
        m = self._ASIN_URL_REGEX.search(response.url)
        if m:
            return m.group(1)

        canonical = response.xpath('//link[@rel="canonical"]/@href').get()
        if canonical:
            m = self._ASIN_URL_REGEX.search(canonical)
            if m:
                return m.group(1)

        val = response.xpath(
            '//input[@name="ASIN" or @id="ASIN" or @name="ASIN.0"]/@value'
        ).get()
        if val:
            val = val.strip()
            if len(val) == 10:
                return val

        val = response.xpath(
            '//*[@id="dp" or @id="ppd"]/@data-asin | '
            '//div[@data-asin and string-length(@data-asin)=10]/@data-asin'
        ).get()
        if val and len(val.strip()) == 10:
            return val.strip()

        val = self.clean_text(response.xpath(
            '//span[contains(@class, "a-text-bold") and contains(normalize-space(.), "ASIN")]'
            '/following-sibling::span/text() | '
            '//th[contains(normalize-space(.), "ASIN")]/following-sibling::td/text()'
        ).get())
        if val and len(val) == 10:
            return val

        return None

    def extract_publication_date(self, response):
        candidates = response.xpath(
            '//span[contains(@class, "a-text-bold") and contains(normalize-space(.), "Publication date")]'
            '/following-sibling::span/text() | '
            '//th[contains(normalize-space(.), "Publication date")]/following-sibling::td/text() | '
            '//div[contains(@id, "rpi-attribute-book_details-publication_date")]'
            '//div[contains(@class, "rpi-attribute-value")]//span/text() | '
            '//span[@id="productSubtitle"]/text()'
        ).getall()
        for val in candidates:
            cleaned = self.clean_text(val)
            if cleaned:
                m = self.DATE_REGEX.search(cleaned)
                if m:
                    return m.group(0)

        detail_text = " ".join(response.xpath(
            '//div[@id="detailBullets_feature_div"]//text() | '
            '//div[@id="detailBulletsWrapper_feature_div"]//text() | '
            '//div[@id="productDetails_detailBullets_sections1"]//text() | '
            '//table[@id="productDetails_techSpec_section_1"]//text()'
        ).getall())
        detail_text = self.clean_text(detail_text)
        if detail_text:
            m = self.DATE_REGEX.search(detail_text)
            if m:
                return m.group(0)

        return None

    def extract_format(self, response):
        subtitle = self.clean_text(response.xpath('//span[@id="productSubtitle"]/text()').get())
        if not subtitle:
            return None
        parts = self._FORMAT_SPLIT_REGEX.split(subtitle, maxsplit=1)
        if len(parts) > 1:
            return parts[0].strip()
        no_date = self.DATE_REGEX.sub("", subtitle).strip(" -–—,")
        return no_date or subtitle

    # ------------------ REQUEST FLOW ------------------

    def start_requests(self):
        for url in self.start_urls:
            self.logger.info(f"Starting crawl for URL: {url}")
            yield scrapy.Request(url=url, headers=self.BASE_HEADERS, callback=self.parse)

    def parse(self, response):
        for product in response.xpath('//div[@data-component-type="s-search-result"]'):
            relative_url = product.xpath(
                './/div[@data-cy="title-recipe"]/a/@href | '
                './/a[contains(@class, "a-link-normal s-underline-text")]/@href'
            ).get()
            if not relative_url:
                continue

            if not self.is_product_url(relative_url):
                self.logger.info(f"⏭️ SKIPPING non-product link: {relative_url[:120]}")
                continue

            yield scrapy.Request(
                url=response.urljoin(relative_url),
                headers=self.DETAIL_HEADERS,
                callback=self.parse_detail,
                meta=self.ZYTE_META,
            )

        next_page = response.xpath('//a[contains(@class, "s-pagination-next")]/@href').get()
        if next_page:
            next_page_url = response.urljoin(next_page)
            self.logger.info(f"Navigating to next page: {next_page_url}")
            yield scrapy.Request(
                url=next_page_url,
                headers=self.BASE_HEADERS,
                callback=self.parse,
                meta=self.ZYTE_META,
            )

    def parse_detail(self, response):
        try:
            if not self.is_product_url(response.url):
                self.logger.info(f"⏭️ SKIPPING non-product page (post-redirect): {response.url}")
                return

            if not response.xpath('//span[@id="productTitle"]').get():
                self.logger.info(f"⏭️ SKIPPING page with no productTitle: {response.url}")
                return

            raw_reviews = response.xpath('//span[@id="acrCustomerReviewText"]/text()').get(default="")
            clean_reviews = self._DIGITS_REGEX.sub("", raw_reviews) if raw_reviews else ""
            total_reviews = int(clean_reviews) if clean_reviews else 0

            if total_reviews > 5:
                self.logger.info(f"⏭️ SKIPPING (reviews={total_reviews}): {response.url.split('?')[0]}")
                return

            asin = self.extract_asin(response)
            product_url = (
                f"https://www.amazon.com/dp/{asin}"
                if asin
                else response.url.split("?")[0].split("/ref=")[0]
            )
            if not asin:
                self.logger.warning(f"⚠️ ASIN MISSING: {product_url}")

            pub_date = self.extract_publication_date(response)
            if not pub_date:
                self.logger.warning(f"⚠️ PUBLICATION DATE MISSING: {product_url}")

            if not asin or not pub_date:
                self.logger.error(f"❌ DROPPING incomplete item: {response.url}")
                return

            author = response.xpath(
                '//span[contains(@class, "author")]/a/text() | '
                '//a[contains(@class, "contributorNameID")]/text() | '
                '//span[contains(@class, "author")]//a[@class="a-link-normal"]/text()'
            ).get()

            publisher = response.xpath(
                '//span[contains(@class, "a-text-bold") and contains(normalize-space(.), "Publisher")]'
                '/following-sibling::span/text() | '
                '//th[contains(normalize-space(.), "Publisher")]/following-sibling::td/text()'
            ).get()

            yield {
                "ASIN": asin,
                "Product Url": product_url,
                "Title": self.clean_text(response.xpath('//span[@id="productTitle"]/text()').get()),
                "Author": self.clean_text(author),
                "Format": self.extract_format(response),
                "Publication Date": pub_date,
                "Publisher": self.clean_text(publisher),
                "Total Reviews": total_reviews,
            }

        except Exception as e:
            self.logger.error(f"❌ Error parsing {response.url}: {e}")
