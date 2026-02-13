"""Motley Fool transcript scraper — free, no API key required.

Fetches earnings call transcripts from fool.com by discovering the
transcript URL (date varies per company) and parsing the HTML.
"""

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus, unquote, urlparse

import httpx

from backend.config import settings

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Next.js server action used by Motley Fool quote pages for
# "View More <TICKER> Earnings Transcripts".
NEXT_ACTION_LOAD_MORE_TRANSCRIPTS = "602be9d1346910a3fe1d11d24bb39769fe2c41372a"
NEXT_ROUTER_STATE_TREE = (
    '["",{"children":["(site)",{"children":["quote",{"children":["[exchange]",'
    '{"children":["[symbol]",{"children":["__PAGE__",{}]}]}]}]},null,null]},'
    "null,null,true]"
)

# Mapping: ticker -> list of URL slug variants to try
COMPANY_SLUGS = {
    "AAPL": ["apple-aapl"],
    "MSFT": ["microsoft-msft"],
    "GOOGL": ["alphabet-googl"],
    "AMZN": ["amazon-amzn", "amazoncom-amzn"],
    "TSLA": ["tesla-tsla"],
    "NVDA": ["nvidia-nvda"],
    "META": ["meta-platforms-meta"],
    "JPM": ["jpmorgan-chase-jpm", "j-p-morgan-chase-jpm", "jpmorgan-jpm"],
    "JNJ": ["johnson-johnson-jnj", "johnson-and-johnson-jnj"],
    "WMT": ["walmart-wmt"],
}

# Companies with non-calendar fiscal years.
# Motley Fool uses the FISCAL year label, not calendar year.
# Key = ticker, Value = fiscal_year_end_month
# NVDA: FY ends Jan (FY2026 covers Feb 2025 - Jan 2026, calendar Q3 2025 = fiscal Q3 FY2026)
# WMT: FY ends Jan (FY2026 covers Feb 2025 - Jan 2026, calendar Q3 2025 = fiscal Q3 FY2026)
# AAPL: FY ends Sep (FY2025 covers Oct 2024 - Sep 2025, calendar Q3 2025 = fiscal Q3 FY2025)
# MSFT: FY ends Jun (FY2026 covers Jul 2025 - Jun 2026, calendar Q3 2025 = fiscal Q1 FY2026)
FISCAL_YEAR_OFFSETS = {
    "NVDA": 1,   # Calendar year + 1 = fiscal year
    "WMT": 1,    # Calendar year + 1 = fiscal year
}

# When we look for transcripts, we search several months.
# The report month depends on the calendar quarter:
#   Calendar Q1 (Jan-Mar) -> reported Apr-May
#   Calendar Q2 (Apr-Jun) -> reported Jul-Aug
#   Calendar Q3 (Jul-Sep) -> reported Oct-Nov-Dec
#   Calendar Q4 (Oct-Dec) -> reported Jan-Feb-Mar (next year)
REPORT_MONTHS = {
    1: [4, 5, 6],
    2: [7, 8, 9],
    3: [10, 11, 12],
    4: [1, 2, 3],
}


def _clean_html(s: str) -> str:
    """Remove HTML tags and decode entities."""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"')
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    s = s.replace("&#39;", "'").replace("&mdash;", "\u2014").replace("&ndash;", "\u2013")
    return s.strip()


def _is_ticker_transcript_link(ticker: str, link: str) -> bool:
    """Check whether a transcript path belongs to the ticker."""
    link_lower = link.lower()
    ticker_lower = ticker.lower()
    if ticker_lower in link_lower:
        return True

    # Some Motley Fool transcript slugs are inconsistent and may omit ticker.
    slugs = COMPANY_SLUGS.get(ticker.upper(), [])
    aliases = {ticker_lower}
    for slug in slugs:
        slug_lower = slug.lower()
        aliases.add(slug_lower)
        # If slug is "apple-aapl", also allow "apple-*".
        suffix = f"-{ticker_lower}"
        if slug_lower.endswith(suffix):
            aliases.add(slug_lower[: -len(suffix)])

    return any(f"/{alias}-" in link_lower for alias in aliases if alias)


def _extract_transcript_links_from_text(text: str, ticker: str) -> list[str]:
    """Extract and deduplicate transcript links for a ticker."""
    href_links = re.findall(
        r'href="(/(?:4056/)?earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[^"]+)"',
        text,
    )
    # Next.js server action responses are RSC payloads and usually carry paths
    # as JSON fields instead of href attributes.
    json_links = re.findall(
        r'"path":"(/(?:4056/)?earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[^"]+)"',
        text,
    )
    escaped_json_links = re.findall(
        r'\\"path\\":\\"(/(?:4056/)?earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[^\\"]+)\\"',
        text,
    )
    links = href_links + json_links + escaped_json_links
    unique_links = []
    seen = set()
    for link in links:
        normalized = link.replace("/4056/", "/")
        if _is_ticker_transcript_link(ticker, normalized) and normalized not in seen:
            seen.add(normalized)
            unique_links.append(normalized)
    return unique_links


def _latest_calendar_quarter() -> tuple[int, int]:
    """Return current calendar (year, quarter)."""
    now = datetime.now()
    quarter = ((now.month - 1) // 3) + 1
    return now.year, quarter


def _quarter_sequence_desc(start_year: int, start_quarter: int, count: int) -> list[tuple[int, int]]:
    """Generate descending quarter tuples from a starting quarter."""
    out: list[tuple[int, int]] = []
    year = start_year
    quarter = start_quarter
    for _ in range(count):
        out.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return out


def _extract_fool_urls_from_search_html(html: str, ticker: str) -> list[str]:
    """Extract Motley Fool transcript URLs from search result HTML."""
    urls: list[str] = []
    seen = set()

    # Direct links in result markup.
    direct = re.findall(
        r"https?://www\.fool\.com/(?:4056/)?earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[a-z0-9\-]+/?",
        html,
        flags=re.IGNORECASE,
    )
    # DuckDuckGo redirect links (uddg=urlencoded_target).
    wrapped = re.findall(r"uddg=([^&\"']+)", html)
    # Some engines expose result URLs as fully URL-encoded strings.
    encoded = re.findall(
        r"https%3A%2F%2Fwww\.fool\.com%2F(?:4056%2F)?earnings%2Fcall-transcripts%2F"
        r"\d{4}%2F\d{2}%2F\d{2}%2F[a-z0-9%\-]+",
        html,
        flags=re.IGNORECASE,
    )

    for raw in direct:
        path = urlparse(raw).path.replace("/4056/", "/")
        if _is_ticker_transcript_link(ticker, path):
            normalized = f"https://www.fool.com{path}"
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

    for raw in wrapped:
        decoded = unquote(raw)
        if "fool.com/earnings/call-transcripts/" not in decoded:
            continue
        path = urlparse(decoded).path.replace("/4056/", "/")
        if _is_ticker_transcript_link(ticker, path):
            normalized = f"https://www.fool.com{path}"
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

    for raw in encoded:
        decoded = unquote(raw)
        path = urlparse(decoded).path.replace("/4056/", "/")
        if _is_ticker_transcript_link(ticker, path):
            normalized = f"https://www.fool.com{path}"
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

    return urls


def _extract_quarter_year_from_link(link: str) -> tuple[int, int] | None:
    """Extract (year, quarter) from transcript slug when available."""
    m = re.search(r"-q([1-4])-(\d{4})-earnings", link.lower())
    if not m:
        return None
    quarter = int(m.group(1))
    year = int(m.group(2))
    return (year, quarter)


def _select_latest_unique_quarters(links: list[str], limit: int) -> list[str]:
    """Keep latest URLs with unique (year, quarter) labels."""
    if limit <= 0:
        return []

    parsed: list[tuple[tuple[int, int], str]] = []
    unparsed: list[str] = []
    for link in links:
        qy = _extract_quarter_year_from_link(link)
        if qy:
            parsed.append((qy, link))
        else:
            unparsed.append(link)

    # Latest first by (year, quarter), then preserve first link seen for each quarter.
    parsed.sort(key=lambda x: x[0], reverse=True)
    picked: list[str] = []
    seen_qy: set[tuple[int, int]] = set()
    seen_link: set[str] = set()

    for qy, link in parsed:
        if qy in seen_qy or link in seen_link:
            continue
        seen_qy.add(qy)
        seen_link.add(link)
        picked.append(link)
        if len(picked) >= limit:
            return picked

    for link in unparsed:
        if link in seen_link:
            continue
        seen_link.add(link)
        picked.append(link)
        if len(picked) >= limit:
            break

    return picked


def _select_from_anchor_sequence(
    links: list[str],
    start_year: int,
    start_quarter: int,
    limit: int,
) -> list[str]:
    """Select links that match sequential quarters from an anchor."""
    if limit <= 0:
        return []

    # First-seen link wins for each (year, quarter).
    by_qy: dict[tuple[int, int], str] = {}
    for link in links:
        qy = _extract_quarter_year_from_link(link)
        if qy and qy not in by_qy:
            by_qy[qy] = link

    selected: list[str] = []
    year = start_year
    quarter = start_quarter
    for _ in range(limit):
        qy = (year, quarter)
        link = by_qy.get(qy)
        if link:
            selected.append(link)
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return selected


def _websearch_transcript_urls(
    ticker: str,
    limit: int,
    debug: bool = False,
    start_year: int | None = None,
    start_quarter: int | None = None,
) -> list[str]:
    """Web-search-first transcript discovery using descending quarter prompts."""
    if limit <= 0:
        return []

    if start_year is not None and start_quarter is not None:
        year, quarter = start_year, start_quarter
    else:
        year, quarter = _latest_calendar_quarter()
    # Keep query count small to avoid engine rate limits.
    prompts = _quarter_sequence_desc(year, quarter, count=max(3, limit + 1))
    query_specs: list[tuple[str, int | None, int | None]] = [
        (
            f"site:fool.com/earnings/call-transcripts {ticker} earnings call transcript",
            None,
            None,
        )
    ]
    for y, q in prompts:
        query_specs.append(
            (
                f"site:fool.com/earnings/call-transcripts "
                f"Motley Fool earnings transcript {ticker} q{q} {y}",
                y,
                q,
            )
        )

    collected: list[str] = []
    strict_collected: list[str] = []
    seen = set()
    engines = [
        ("brave", "https://search.brave.com/search?q={}&source=web"),
        ("yahoo", "https://search.yahoo.com/search?p={}"),
        ("duckduckgo", "https://duckduckgo.com/html/?q={}"),
        ("bing", "https://www.bing.com/search?q={}"),
    ]
    blocked_engines: set[str] = set()
    engine_errors: dict[str, int] = {name: 0 for name, _ in engines}

    for query, y, q in query_specs:
        if len(strict_collected) >= limit:
            break
        for engine_name, engine_tmpl in engines:
            if engine_name in blocked_engines:
                continue
            url = engine_tmpl.format(quote_plus(query))
            try:
                r = httpx.get(url, headers=HEADERS, timeout=8, follow_redirects=True)
                if r.status_code != 200:
                    if debug:
                        print(
                            f"  [FOOL] {ticker} websearch[{engine_name}] "
                            f"q{q} {y}: status={r.status_code}"
                        )
                    # Engine-level block/forbidden; stop using it for this ticker.
                    if r.status_code in (401, 403, 429, 503):
                        blocked_engines.add(engine_name)
                    continue
                found = _extract_fool_urls_from_search_html(r.text, ticker)
                # Prefer links that match this prompt's quarter/year exactly.
                strict = []
                loose = []
                for link in found:
                    parsed = _extract_quarter_year_from_link(link)
                    if y is not None and q is not None and parsed == (y, q):
                        strict.append(link)
                    else:
                        loose.append(link)
                if debug:
                    print(
                        f"  [FOOL] {ticker} websearch[{engine_name}] "
                        f"q{q} {y}: found={len(found)} strict={len(strict)}"
                    )
                for link in strict:
                    if link not in seen:
                        seen.add(link)
                        strict_collected.append(link)
                        collected.append(link)
                        if len(strict_collected) >= limit:
                            break
                # Keep loose matches only as a backup pool.
                for link in loose:
                    if link not in seen:
                        seen.add(link)
                        collected.append(link)
                # Use first successful engine response per prompt.
                break
            except httpx.HTTPError as e:
                engine_errors[engine_name] += 1
                if engine_errors[engine_name] >= 2:
                    blocked_engines.add(engine_name)
                if debug:
                    print(f"  [FOOL] {ticker} websearch[{engine_name}] q{q} {y}: error={e}")
                continue

        if len(blocked_engines) == len(engines):
            if debug:
                print(f"  [FOOL] {ticker} websearch: all engines unavailable")
            break

    # Sort by parsed fiscal year/quarter descending when present.
    def _score(link: str) -> tuple[int, int]:
        parsed = _extract_quarter_year_from_link(link)
        return parsed if parsed else (0, 0)

    ordered = sorted(collected, key=_score, reverse=True)
    unique = _select_latest_unique_quarters(ordered, limit * 3)
    if start_year is not None and start_quarter is not None:
        anchored = _select_from_anchor_sequence(unique, start_year, start_quarter, limit)
        return anchored[:limit]
    return unique[:limit]


def _load_more_transcript_links(
    quote_url: str,
    instrument_id: str,
    ticker: str,
    start_page: int,
    max_pages: int,
    debug: bool = False,
) -> list[str]:
    """Fetch extra transcript links by emulating quote page View More action."""
    path = urlparse(quote_url).path
    headers = {
        **HEADERS,
        "Accept": "text/x-component",
        "Origin": "https://www.fool.com",
        "Referer": quote_url,
        "RSC": "1",
        "Next-Action": NEXT_ACTION_LOAD_MORE_TRANSCRIPTS,
        "Next-Url": path,
        "Next-Router-State-Tree": NEXT_ROUTER_STATE_TREE,
    }

    all_links: list[str] = []
    seen = set()
    for page in range(start_page, start_page + max_pages):
        try:
            page_candidates: list[str] = []
            # Strategy A: plain form fields.
            r = httpx.post(
                quote_url,
                headers=headers,
                files={
                    "instrumentId": (None, instrument_id),
                    "page": (None, str(page)),
                },
                timeout=20,
            )
            if r.status_code != 200:
                if debug:
                    print(f"  [FOOL] load-more page={page} status={r.status_code} (plain)")
                break
            page_candidates.extend(_extract_transcript_links_from_text(r.text, ticker))

            # Strategy B: encoded server-action args shape used by Next.
            # This often returns the same data, but keeps behavior aligned
            # with the client runtime when Motley Fool changes internals.
            encoded_args = [page_candidates, "$K1"]
            r2 = httpx.post(
                quote_url,
                headers=headers,
                files={
                    "1_instrumentId": (None, instrument_id),
                    "1_page": (None, str(page)),
                    "0": (None, json.dumps(encoded_args)),
                },
                timeout=20,
            )
            if r2.status_code == 200:
                page_candidates.extend(_extract_transcript_links_from_text(r2.text, ticker))

            # Preserve order while deduplicating this page.
            dedup_page = []
            page_seen = set()
            for link in page_candidates:
                if link not in page_seen:
                    page_seen.add(link)
                    dedup_page.append(link)

            new_page_links = [l for l in dedup_page if l not in seen]
            if debug:
                print(
                    f"  [FOOL] load-more page={page} found={len(dedup_page)} "
                    f"new={len(new_page_links)}"
                )
            if not new_page_links:
                break

            for link in new_page_links:
                seen.add(link)
                all_links.append(link)
        except httpx.HTTPError as e:
            if debug:
                print(f"  [FOOL] load-more page={page} request failed: {e}")
            break

    return all_links


def _get_quote_page_transcripts(
    ticker: str,
    limit: Optional[int] = None,
    debug: bool = False,
) -> list[str]:
    """Fetch transcript URLs from the company's quote page.

    Extracts links from the <div id="quote-earnings-transcripts"> section.
    """
    # Try nasdaq and nyse exchanges; keep best result.
    best_links: list[str] = []
    for exchange in ["nasdaq", "nyse"]:
        url = f"https://www.fool.com/quote/{exchange}/{ticker.lower()}/"
        try:
            r = httpx.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
            if r.status_code != 200:
                if debug:
                    print(f"  [FOOL] {ticker} {exchange}: quote status={r.status_code}")
                continue

            # Find the earnings transcripts section
            html = r.text
            section_match = re.search(
                r'<div[^>]+id="quote-earnings-transcripts"[\s\S]*?</div>\s*</div>\s*</div>',
                html,
            )
            if not section_match:
                continue
            section = section_match.group(0)

            unique_links = _extract_transcript_links_from_text(section, ticker)
            if debug:
                print(f"  [FOOL] {ticker} {exchange}: initial links={len(unique_links)}")

            # If we have fewer than requested, emulate "View More" clicks.
            # Page starts at 1 in the initial form and the client requests page+1.
            if limit and len(unique_links) < limit:
                id_match = re.search(r'name="instrumentId"\s+value="(\d+)"', section)
                page_match = re.search(r'name="page"\s+value="(\d+)"', section)
                if id_match:
                    current_page = int(page_match.group(1)) if page_match else 1
                    extra_links = _load_more_transcript_links(
                        quote_url=url,
                        instrument_id=id_match.group(1),
                        ticker=ticker,
                        start_page=current_page + 1,
                        max_pages=4,
                        debug=debug,
                    )
                    for link in extra_links:
                        if link not in unique_links:
                            unique_links.append(link)
                            if len(unique_links) >= limit:
                                break
                if debug:
                    print(f"  [FOOL] {ticker} {exchange}: after load-more={len(unique_links)}")

            if unique_links:
                if limit and len(unique_links) >= limit:
                    return unique_links
                if len(unique_links) > len(best_links):
                    best_links = unique_links

        except httpx.HTTPError:
            continue

    return best_links


def get_latest_transcript_urls(
    ticker: str,
    limit: int = 4,
    debug: bool = False,
    start_year: int | None = None,
    start_quarter: int | None = None,
) -> list[str]:
    """Return latest transcript URLs from Motley Fool quote page.

    Args:
        ticker: Stock ticker symbol (for example, "NVDA").
        limit: Maximum number of transcript URLs to return.

    Returns:
        List of absolute Motley Fool transcript URLs in page order.
    """
    ticker = ticker.upper()
    # Websearch-only mode by user request.
    web_urls = _websearch_transcript_urls(
        ticker,
        limit=limit,
        debug=debug,
        start_year=start_year,
        start_quarter=start_quarter,
    )
    if debug and len(web_urls) < limit:
        print(f"  [FOOL] {ticker} websearch-only returned {len(web_urls)}/{limit}")
    return web_urls[:limit]


def _discover_url(
    ticker: str,
    year: int,
    quarter: int,
    use_quote_page: bool = True,
    request_pause: float = 0.02,
    request_timeout: float = 8,
) -> Optional[str]:
    """Find the Motley Fool transcript URL.

    Strategy: Check quote page first for recent transcripts, then fall back to date scanning.
    """
    fy_offset = FISCAL_YEAR_OFFSETS.get(ticker, 0)
    fool_year = year + fy_offset

    if use_quote_page:
        # Step 1: Try quote page for recent quarters (fast, no date scanning)
        quote_links = _get_quote_page_transcripts(ticker)

        # Look for matching quarter in quote page links
        for link in quote_links:
            # Match pattern: .../q{quarter}-{year}-earnings...
            if f"-q{quarter}-{fool_year}-earnings" in link:
                return f"https://www.fool.com{link}"
            # Also try with calendar year for fiscal year companies
            if fy_offset and f"-q{quarter}-{year}-earnings" in link:
                return f"https://www.fool.com{link}"

    # Step 2: Fall back to date scanning for older quarters
    slugs = COMPANY_SLUGS.get(ticker, [f"{ticker.lower()}-{ticker.lower()}"])

    months = REPORT_MONTHS.get(quarter, [1, 2, 3])
    # For Q4, reports come out in Jan-Mar of the NEXT calendar year
    report_year = year + 1 if quarter == 4 else year

    suffixes = ["earnings-call-transcript", "earnings-transcript"]

    client = httpx.Client(headers=HEADERS, timeout=request_timeout, follow_redirects=True)

    for slug in slugs:
        for suffix in suffixes:
            for month in months:
                for day in range(1, 32):
                    url = (
                        f"https://www.fool.com/earnings/call-transcripts/"
                        f"{report_year}/{month:02d}/{day:02d}/"
                        f"{slug}-q{quarter}-{fool_year}-{suffix}/"
                    )
                    try:
                        r = client.head(url)
                        if r.status_code == 200:
                            client.close()
                            return url
                    except httpx.HTTPError:
                        continue
                    if request_pause > 0:
                        time.sleep(request_pause)

    # For non-standard fiscal year companies, also try with original year
    if fy_offset != 0:
        for slug in slugs:
            for suffix in suffixes:
                for month in months:
                    for day in range(1, 32):
                        url = (
                            f"https://www.fool.com/earnings/call-transcripts/"
                            f"{report_year}/{month:02d}/{day:02d}/"
                            f"{slug}-q{quarter}-{year}-{suffix}/"
                        )
                        try:
                            r = client.head(url)
                            if r.status_code == 200:
                                client.close()
                                return url
                        except httpx.HTTPError:
                            continue
                        if request_pause > 0:
                            time.sleep(request_pause)

    client.close()
    return None


def _backfill_links_from_cache(
    ticker: str,
    existing_links: list[str],
    limit: int,
    debug: bool = False,
) -> list[str]:
    """Fill missing URLs from locally cached transcript JSON files."""
    if len(existing_links) >= limit:
        return []

    file_re = re.compile(rf"^{re.escape(ticker)}_Q([1-4])_(\d{{4}})\.json$")
    existing_abs = {f"https://www.fool.com{p}" for p in existing_links}
    ranked: list[tuple[int, int, str]] = []

    for path in settings.transcripts_dir.glob(f"{ticker}_Q*_*.json"):
        m = file_re.match(path.name)
        if not m:
            continue
        quarter = int(m.group(1))
        year = int(m.group(2))
        try:
            with open(path) as f:
                payload = json.load(f)
        except Exception:
            continue
        source_url = payload.get("source_url", "")
        source_name = payload.get("source", "")
        if (
            isinstance(source_url, str)
            and source_url.startswith("https://www.fool.com/earnings/call-transcripts/")
            and source_url not in existing_abs
            and source_name == "fool.com"
        ):
            ranked.append((year, quarter, source_url))

    ranked.sort(reverse=True)
    added = [url for _, _, url in ranked[: max(0, limit - len(existing_links))]]
    if debug and added:
        print(f"  [FOOL] {ticker} cache backfill added={len(added)}")

    return [u.replace("https://www.fool.com", "") for u in added]


def _backfill_links_from_quarter_scan(
    ticker: str,
    existing_links: list[str],
    limit: int,
    debug: bool = False,
) -> list[str]:
    """Fill missing URLs by scanning recent quarter URL patterns."""
    missing = max(0, limit - len(existing_links))
    if missing == 0:
        return []

    # Prefer quarter/year labels from already found links: ...-q{q}-{year}-earnings...
    anchor_matches = []
    for link in existing_links:
        m = re.search(r"-q([1-4])-(\d{4})-earnings", link)
        if m:
            anchor_matches.append((int(m.group(2)), int(m.group(1))))

    if anchor_matches:
        anchor_year, anchor_quarter = max(anchor_matches)
    else:
        now_year, now_quarter = _latest_calendar_quarter()
        anchor_year, anchor_quarter = now_year, now_quarter

    # Walk backwards from previous quarter to fill only what is missing.
    candidate_tuples: list[tuple[int, int]] = []
    year, quarter = anchor_year, anchor_quarter
    for _ in range(missing + 6):
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
        candidate_tuples.append((year, quarter))

    existing_abs = {f"https://www.fool.com{p}" for p in existing_links}
    added: list[str] = []

    for year, quarter in candidate_tuples:
        if len(added) >= missing:
            break
        if debug:
            print(f"  [FOOL] {ticker} scan try Q{quarter} {year}")
        url = _discover_url(
            ticker=ticker,
            year=year,
            quarter=quarter,
            use_quote_page=False,
            request_pause=0,
            request_timeout=4,
        )
        if not url or url in existing_abs or url in added:
            continue
        added.append(url)
        if debug:
            print(f"  [FOOL] {ticker} scan backfill Q{quarter} {year}: {url}")

    return [u.replace("https://www.fool.com", "") for u in added]


def _extract_transcript(html: str) -> Optional[str]:
    """Extract the full transcript text from Motley Fool HTML."""
    # Look for "Full Conference Call Transcript" section (primary)
    start_marker = "Full Conference Call Transcript"
    start_idx = html.find(start_marker)

    # Fallback markers
    if start_idx < 0:
        for fallback in ["Prepared Remarks", "Call Start"]:
            start_idx = html.find(fallback)
            if start_idx >= 0:
                break

    if start_idx < 0:
        return None

    # Find the end of the transcript section
    end_markers = [
        "Premium Investing Services",
        "should not be copied",
        "Invest better with The Motley Fool",
    ]
    end_idx = len(html)
    for m in end_markers:
        idx = html.find(m, start_idx)
        if 0 < idx < end_idx:
            end_idx = idx

    section = html[start_idx:end_idx]

    # Extract paragraphs
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", section, re.DOTALL)

    # Boilerplate to skip
    skip_phrases = [
        "Motley Fool has positions",
        "Motley Fool has a disclosure",
        "Average returns of all",
        "Cost basis and return",
        "Image source:",
        "Motley Fool recommends",
    ]

    lines = []
    for p in paragraphs:
        cleaned = _clean_html(p)
        if cleaned and len(cleaned) > 2:
            if any(skip in cleaned for skip in skip_phrases):
                continue
            lines.append(cleaned)

    if not lines:
        return None

    return "\n\n".join(lines)


def _parse_speakers(text: str) -> list[dict]:
    """Parse speaker sections from transcript text."""
    sections = []
    speaker_pattern = re.compile(
        r"^([A-Z][A-Za-z'. -]+(?:\s*-\s*[A-Za-z\s,&]+)?)\s*:\s*",
        re.MULTILINE,
    )

    matches = list(speaker_pattern.finditer(text))
    if not matches:
        return [{"name": "Unknown", "start_char": 0, "end_char": len(text), "text": text}]

    for i, m in enumerate(matches):
        name_raw = m.group(1).strip()
        name = name_raw.split(" - ")[0].strip() if " - " in name_raw else name_raw
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({
            "name": name,
            "start_char": start,
            "end_char": end,
            "text": text[start:end],
        })

    return sections


def fetch_fool_transcript(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Fetch and parse an earnings call transcript from Motley Fool.

    Returns a transcript dict compatible with the pipeline, or None.
    """
    key = f"{ticker}_Q{quarter}_{year}"
    print(f"  [FOOL] {key} \u2014 searching for transcript URL...", end=" ", flush=True)

    url = _discover_url(ticker, year, quarter)
    if not url:
        print("not found")
        return None

    print("found!", flush=True)
    print(f"         {url}")

    # Fetch the full page
    try:
        r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  [WARN] {key} - fetch error: {e}")
        return None

    # Extract transcript text
    raw_text = _extract_transcript(r.text)
    if not raw_text or len(raw_text) < 500:
        print(f"  [WARN] {key} - transcript too short or missing")
        return None

    speaker_sections = _parse_speakers(raw_text)
    print(f"  [FOOL] {key} \u2014 {len(raw_text)} chars, {len(speaker_sections)} speakers")

    return {
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "title": f"{ticker} Q{quarter} {year} Earnings Call",
        "call_date": "",
        "text": raw_text,
        "speaker_sections": speaker_sections,
        "text_hash": hashlib.sha256(raw_text.encode()).hexdigest(),
        "fetched_at": datetime.now().isoformat(),
        "source": "fool.com",
        "source_url": url,
    }
