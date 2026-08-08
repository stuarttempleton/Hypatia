"""Download book bodies, cached to disk, throttled, mirror-first.

This is the ONLY unavoidable Gutenberg traffic. Good-citizen measures:
  - fetch only the books we actually shelve
  - cache to a local (gitignored) dir so re-runs/re-themes never re-download
  - throttle between live fetches
  - prefer a mirror (aleph.gutenberg.org) — PG explicitly points bulk/automated
    users at mirrors — and fall back to the main site only if the mirror misses.
"""

import os
import time
import urllib.request

# Main-site canonical UTF-8 path.
_MAIN_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
# Mirror path: digits of the id split into directories, then <id>-0.txt (UTF-8).
# e.g. 1342 -> /1/3/4/1342/1342-0.txt ; single-digit ids nest under "0".
_MIRROR_BASE = "http://aleph.gutenberg.org"


def _mirror_url(book_id: int) -> str:
    s = str(book_id)
    if len(s) == 1:
        path = "0/" + s
    else:
        path = "/".join(s[:-1]) + "/" + s
    return f"{_MIRROR_BASE}/{path}/{book_id}-0.txt"


def _download(url: str, timeout: float) -> str | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Hypatia/0.1 (Gutenberg curator; offline reader project)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_book(
    book_id: int,
    cache_dir: str,
    delay_s: float = 1.5,
    timeout: float = 60.0,
    prefer_mirror: bool = True,
    log=print,
) -> str | None:
    """Return the raw text of a book, from cache if present else downloaded.

    Returns None if neither mirror nor main site yields the text.
    The `delay_s` throttle is applied only on an actual network fetch, not a cache hit.
    """
    cache_path = os.path.join(cache_dir, f"pg{book_id}.txt")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return fh.read()

    urls = [_mirror_url(book_id), _MAIN_URL.format(id=book_id)]
    if not prefer_mirror:
        urls.reverse()

    raw = None
    for url in urls:
        raw = _download(url, timeout)
        if raw is not None:
            break

    time.sleep(delay_s)  # polite spacing after a live request

    if raw is None:
        log(f"[fetch]   MISS id={book_id} (mirror+main both failed)")
        return None

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(raw)
    return raw
