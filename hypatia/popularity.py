"""Fetch popularity (download_count) + summaries from Gutendex, cached to disk.

download_count is the one field NOT present in the offline pg_catalog.csv, so it is
the only metadata that requires the network. We crawl Gutendex (a free, open API
built for exactly this — the polite target, NOT gutenberg.org) once, sorted by
popularity, and cache the result. Re-runs read the cache and make zero requests.

Gutendex summaries come along for free in the same response, so we harvest them here
too and skip MARC entirely for MVP.

Hardened for deep crawls (10k+):
  - retry with exponential backoff on 429 / 5xx / transient network errors, honoring
    a Retry-After header when present (Gutendex throttles by slowing, not banning);
  - incremental progress written to `<cache>.partial` every few pages, so an
    interrupted or failed crawl RESUMES from where it stopped instead of restarting.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GUTENDEX_BASE = "https://gutendex.com/books/"

# Save partial progress at least this often (in pages) so a long crawl is resumable.
_CHECKPOINT_EVERY_PAGES = 5


def _get_json(url: str, timeout: float, log, max_retries: int = 6) -> dict:
    """GET + parse JSON with exponential backoff on throttle / transient failures."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Hypatia/0.1 (Gutenberg curator)"}
    )
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 = rate limited; 5xx = transient server. Back off and retry.
            if e.code == 429 or 500 <= e.code < 600:
                wait = delay
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after and str(retry_after).isdigit():
                    wait = max(wait, float(retry_after))
                log(f"[popularity]   HTTP {e.code}; backing off {wait:.0f}s "
                    f"(attempt {attempt}/{max_retries})")
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            raise  # 4xx other than 429 is a real error — don't mask it
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"[popularity]   network error ({e}); retrying in {delay:.0f}s "
                f"(attempt {attempt}/{max_retries})")
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    raise RuntimeError(f"[popularity] gave up after {max_retries} retries on {url}")


def _partial_path(cache_path: str) -> str:
    return cache_path + ".partial"


def _save_partial(path: str, out: dict, next_url: str | None, page: int) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"results": {str(k): v for k, v in out.items()},
                   "next": next_url, "page": page}, fh)
    os.replace(tmp, path)  # atomic-ish: never leave a half-written partial


def crawl_popularity(
    cache_path: str,
    languages: str = "en",
    max_books: int = 2000,
    delay_s: float = 1.0,
    timeout: float = 30.0,
    refresh: bool = False,
    log=print,
) -> dict[int, dict]:
    """Return {book_id: {"download_count": int, "summary": str}} for the top
    `max_books` by popularity in `languages`.

    Cached to `cache_path` (JSON). Pass refresh=True to force a full re-crawl.
    An interrupted crawl leaves `<cache_path>.partial` and resumes automatically.
    """
    if os.path.exists(cache_path) and not refresh:
        with open(cache_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        cached = {int(k): v for k, v in raw.items()}
        if len(cached) >= max_books:
            log(f"[popularity] using cache {cache_path} ({len(cached)} entries)")
            return cached
        # Cache is shallower than requested — crawl deeper, seeding from what we have.
        log(f"[popularity] cache has {len(cached)} < {max_books} requested; "
            f"extending crawl")

    params = {"languages": languages, "sort": "popular"}
    start_url = GUTENDEX_BASE + "?" + urllib.parse.urlencode(params)

    out: dict[int, dict] = {}
    url = start_url
    page = 0
    partial = _partial_path(cache_path)

    # Resume from a prior interrupted crawl if present (and not force-refreshing).
    if os.path.exists(partial) and not refresh:
        try:
            with open(partial, "r", encoding="utf-8") as fh:
                p = json.load(fh)
            out = {int(k): v for k, v in p.get("results", {}).items()}
            url = p.get("next") or start_url
            page = p.get("page", 0)
            log(f"[popularity] resuming from partial: {len(out)} entries, page {page}")
        except (json.JSONDecodeError, OSError):
            log("[popularity] partial unreadable; starting fresh")
            out, url, page = {}, start_url, 0

    if not out:
        log(f"[popularity] crawling Gutendex (top {max_books}, lang={languages})...")

    while url and len(out) < max_books:
        page += 1
        data = _get_json(url, timeout, log)
        for b in data.get("results", []):
            bid = b.get("id")
            if bid is None:
                continue
            summaries = b.get("summaries") or []
            out[int(bid)] = {
                "download_count": int(b.get("download_count") or 0),
                "summary": summaries[0] if summaries else "",
            }
            if len(out) >= max_books:
                break
        log(f"[popularity]   page {page}: {len(out)}/{max_books}")
        url = data.get("next")

        # Checkpoint periodically so progress survives an interruption.
        if page % _CHECKPOINT_EVERY_PAGES == 0 and len(out) < max_books:
            _save_partial(partial, out, url, page)

        if url and len(out) < max_books:
            time.sleep(delay_s)  # be polite to Gutendex

    # Write the final flat cache and remove the partial.
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in out.items()}, fh)
    os.replace(tmp, cache_path)
    if os.path.exists(partial):
        os.remove(partial)
    log(f"[popularity] cached {len(out)} entries -> {cache_path}")
    return out
