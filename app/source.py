"""Metacritic adapter. Fail closed on changed/blocked listing markup."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = "https://www.metacritic.com"
SLUG = re.compile(r"^/game/([^/]+)/?(?:\?.*)?$")


class SourceError(RuntimeError):
    pass


@dataclass
class GameData:
    slug: str
    title: str
    source_url: str
    cover: str | None = None
    developer: str | None = None
    description: str = ""
    video: str | None = None
    genres: list = field(default_factory=list)
    platforms: list = field(default_factory=list)
    reviews: dict = field(default_factory=dict)


def soup_for(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text().lower() if soup.title else ""
    if any(x in title for x in ("access denied", "just a moment", "captcha")):
        raise SourceError("Metacritic blocked this request")
    return soup


def links_in(node):
    result = []
    for a in node.select("a[href]"):
        path = urlparse(urljoin(ROOT, a["href"])).path
        match = SLUG.match(path)
        if match and match[1] not in result:
            result.append(match[1])
    return result


def parse_listing(html, home=False):
    soup = soup_for(html)
    if home:
        heading = soup.find(
            lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True).lower() == "new releases"
        )
        if heading is None:
            raise SourceError("New Releases section not found; parser needs updating")
        node = heading.parent
        for _ in range(5):
            found = links_in(node)
            if found:
                return found[:20]
            node = node.parent
        raise SourceError("New Releases contains no game links")
    cards = soup.select('.c-finderProductCard, [data-testid="product-card"], [data-testid="filter-results"]')
    if cards:
        return list(dict.fromkeys(slug for card in cards for slug in links_in(card)))
    main = soup.select_one(".c-productListings, .c-finderProducts")
    if main:
        return links_in(main)
    if soup.find(string=re.compile(r"no results found", re.I)):
        return []
    raise SourceError("Browse listing not found; parser needs updating")


def json_objects(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
            yield from walk(payload)
        except (ValueError, TypeError):
            continue


def card_image(soup, slug):
    """Read Nuxt's JSON reference table without evaluating any JavaScript."""
    script = soup.find("script", id="__NUXT_DATA__")
    if not script:
        return None
    try:
        table = json.loads(script.get_text())
        if not isinstance(table, list):
            return None

        def ref(index):
            return table[index] if type(index) is int and 0 <= index < len(table) else None

        for node in table:
            if not isinstance(node, dict) or ref(node.get("slug")) != slug or "images" not in node:
                continue
            for index in ref(node["images"]) or []:
                image = ref(index)
                if not isinstance(image, dict) or ref(image.get("typeName")) != "cardImage":
                    continue
                path = ref(image.get("bucketPath"))
                if (
                    ref(image.get("bucketType")) == "catalog"
                    and isinstance(path, str)
                    and path.startswith("/provider/")
                    and ".." not in path.split("/")
                ):
                    return ROOT + "/a/img/catalog" + path
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    return None


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def text_of(soup, selector):
    node = soup.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def score(text, maximum):
    try:
        value = float(text.strip())
        return value if 0 <= value <= maximum else None
    except (ValueError, AttributeError, TypeError):
        return None


def platform_rows(soup):
    rows = []
    for row in soup.select(".product-score-card--platform"):
        icon = row.select_one(".game-platform-logo__icon[title]")
        if icon:
            rows.append(
                {
                    "name": icon["title"],
                    "metascore": score(text_of(row, ".c-siteReviewScore span"), 100),
                    "userscore": None,
                }
            )
    for row in soup.select(".c-productScoreInfo, .c-gamePlatform, [data-platform]"):
        name = text_of(row, '.c-productScoreInfo_platform, .c-gamePlatform_name, [data-testid="platform"]')
        name = name or row.get("data-platform")
        if not name:
            continue
        meta = text_of(
            row, ".c-productScoreInfo_scoreNumber, .c-siteReviewScore_background-critic_medium span"
        )
        user = text_of(row, ".c-siteReviewScore_background-user span, .c-productScoreInfo_userScore")
        rows.append({"name": name, "metascore": score(meta, 100), "userscore": score(user, 10)})
    return rows


def platform_identifiers(soup):
    result = {}
    for row in soup.select(".product-score-card--platform"):
        icon = row.select_one(".game-platform-logo__icon[title]")
        url = row.get("href") or row.get("to") or ""
        slug = parse_qs(urlparse(url).query).get("platform", [])
        if icon and slug:
            result[icon["title"]] = slug[0]
    return result


def aggregate_score(soup, kind):
    overview = soup.select_one('[data-testid="score-card-overview"]')
    if overview:
        return score(
            text_of(overview, ".score-card-left__score-number .c-siteReviewScore span"),
            10 if kind == "users" else 100,
        )
    selector = (
        ".c-siteReviewScore_background-user span"
        if kind == "users"
        else (".c-siteReviewScore_background-critic_medium span")
    )
    return score(text_of(soup, ".c-productScoreInfo " + selector), 10 if kind == "users" else 100)


def parse_game(html, slug):
    soup = soup_for(html)
    structured = next(
        (x for x in json_objects(soup) if x.get("@type") in ("VideoGame", "Product", "SoftwareApplication")),
        {},
    )
    title = structured.get("name") or text_of(soup, "h1")
    if not title:
        raise SourceError(f"Missing game title: {slug}")
    image = structured.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    og = soup.select_one('meta[property="og:image"]')
    developer = structured.get("author") or structured.get("developer")
    if isinstance(developer, dict):
        developer = developer.get("name")
    if isinstance(developer, list):
        developer = ", ".join(x.get("name", "") if isinstance(x, dict) else str(x) for x in developer)
    developer = developer or text_of(soup, ".c-gameDetails_Developer a, .c-gameDetails_developer a")
    if not developer:
        label = soup.find(string=re.compile(r"^\s*Developer\s*:?\s*$", re.I))
        if label and label.parent.parent:
            developer = label.parent.parent.get_text(" ", strip=True).replace("Developer", "").strip(" :")
    genres = structured.get("genre") or [x.get_text(strip=True) for x in soup.select(".c-genreList a")]
    if isinstance(genres, str):
        genres = [genres]
    platforms = platform_rows(soup)
    raw_platforms = structured.get("gamePlatform", [])
    if isinstance(raw_platforms, str):
        raw_platforms = [raw_platforms]
    for name in raw_platforms:
        if isinstance(name, dict):
            name = name.get("name")
        if name and not any(p["name"].casefold() == name.casefold() for p in platforms):
            platforms.append({"name": name, "metascore": None, "userscore": None})
    video = structured.get("trailer") or structured.get("video") or {}
    if isinstance(video, list):
        video = video[0] if video else {}
    if isinstance(video, dict):
        video = video.get("embedUrl") or video.get("contentUrl") or video.get("url")
    if not video:
        link = soup.select_one('a[href*="/videos/"], video source[src]')
        video = urljoin(ROOT, link.get("href") or link.get("src")) if link else None
    return GameData(
        slug=slug,
        title=title,
        source_url=f"{ROOT}/game/{slug}/",
        cover=card_image(soup, slug) or image or (og.get("content") if og else None),
        developer=developer or None,
        description=structured.get("description") or text_of(soup, ".c-pageProduct_description"),
        video=video,
        genres=genres,
        platforms=platforms,
    )


def parse_reviews(html, kind):
    soup = soup_for(html)
    result = []
    selector = '[data-testid="review-card"], .c-siteReview'
    if kind == "critics":
        selector += ", .c-criticReview"
    for i, row in enumerate(soup.select(selector)):
        body = text_of(
            row,
            '[data-testid="review-quote-text"], .c-siteReview_quote, .c-siteReview_review, .c-criticReview_text',
        )
        if not body:
            continue
        a = row.select_one('[data-testid="review-full-review-link"]') or row.select_one(
            'a[href*="/user/"], a[href*="/publication/"], a[href^="http"]'
        )
        author = text_of(row, '[data-testid="review-card-header"], .c-siteReviewHeader_username')
        author = re.sub(r"^(?:\d+(?:\.\d+)?|tbd)\s+", "", author, flags=re.I)
        result.append(
            {
                "text": body[:6000],
                "author": author or (a.get_text(strip=True) if a else "unknown"),
                "platform": text_of(row, '[data-testid="review-platform"]'),
                "source_url": urljoin(ROOT, a["href"]) if a else None,
                "score": score(text_of(row, ".c-siteReviewScore span"), 10 if kind == "users" else 100),
            }
        )
    if not result and not soup.select_one(".c-pageProductReviews, .c-productReviews, .c-siteReview"):
        if not re.search(
            r"no (user |critic )?reviews|not enough reviews|showing\s+0\s+(user |critic )?reviews|reviews are not available",
            soup.get_text(" ", strip=True),
            re.I,
        ):
            raise SourceError(f"{kind} review markup not recognized")
    return result


class Metacritic:
    def __init__(self, config):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=40, follow_redirects=True, headers={"User-Agent": "GamePulse/0.1 (game review research)"}
        )
        self.last_request = 0.0

    async def close(self):
        await self.client.aclose()

    async def get(self, path):
        loop = asyncio.get_running_loop()
        for attempt in range(3):
            await asyncio.sleep(max(0, self.config.request_delay_seconds - (loop.time() - self.last_request)))
            self.last_request = loop.time()
            response = await self.client.get(urljoin(ROOT, path))
            if response.status_code in (403, 401):
                raise SourceError(f"Metacritic access denied ({response.status_code}); no bypass attempted")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
            response.raise_for_status()
            return response.text
        raise SourceError("Source unavailable")

    async def listing(self, home=False, page=1):
        path = "/game/" if home else f"/browse/game/all/all/all-time/new/?page={page}"
        return parse_listing(await self.get(path), home)

    async def game(self, slug):
        game_html = await self.get(f"/game/{slug}/")
        data = parse_game(game_html, slug)
        if data.cover:
            from app.assets import cache_cover

            try:
                await asyncio.sleep(self.config.request_delay_seconds)
                data.cover = await cache_cover(data.cover, self.client, self.config.cover_directory)
                self.last_request = asyncio.get_running_loop().time()
            except (httpx.HTTPError, ValueError, OSError) as exc:
                data.reviews.setdefault("warnings", []).append("Cover cache: " + type(exc).__name__)
        identifiers = platform_identifiers(soup_for(game_html))
        # Review pages are platform-specific. Retrieve each platform without mixing scores.
        for platform in data.platforms:
            platform_slug = identifiers.get(platform["name"]) or re.sub(
                r"[^a-z0-9]+", "-", platform["name"].lower()
            ).strip("-")
            for kind, path in (("critics", "critic-reviews"), ("users", "user-reviews")):
                reviews = []
                try:
                    for page in range(1, self.config.review_pages + 1):
                        url = f"/game/{slug}/{path}/?platform={platform_slug}&page={page}"
                        html = await self.get(url)
                        page_soup = soup_for(html)
                        value = aggregate_score(page_soup, kind)
                        if value is not None:
                            platform["userscore" if kind == "users" else "metascore"] = value
                        chunk = [
                            r
                            for r in parse_reviews(html, kind)
                            if not r.get("platform") or r["platform"] == platform["name"]
                        ]
                        for review in chunk:
                            review.update(platform=platform["name"], page_url=ROOT + url)
                        reviews.extend(chunk)
                        if not chunk:
                            break
                        next_page = page_soup.select_one('[data-testid="pagination-arrow-next"]')
                        if not next_page or any("disabled" in c for c in next_page.get("class", [])):
                            break
                except (httpx.HTTPError, SourceError) as exc:
                    data.reviews.setdefault("warnings", []).append(f"{platform['name']}/{kind}: {exc}")
                data.reviews.setdefault(kind, []).extend(reviews)
        # Some game pages omit platforms from JSON-LD. Don't invent platform ratings.
        if not data.platforms:
            raise SourceError(f"No platforms parsed for {slug}; refusing incomplete import")
        for kind in ("critics", "users"):
            unique = {}
            for review in data.reviews.get(kind, []):
                key = (review["text"], review["author"], review["platform"])
                unique[key] = review
            data.reviews[kind] = [dict(review, id=f"{kind}-{i}") for i, review in enumerate(unique.values())]
        return data
