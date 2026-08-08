#!/usr/bin/env python3
"""Hypatia build orchestrator.

Pipeline: catalog (offline CSV) -> popularity (cached Gutendex) -> grouping
-> fetch (cached, throttled) -> strip+emit -> Pinakes/{index.txt, shelf_NN.txt}.

Typical use:
    python build.py --pinakes ../Pinakes --limit 2000

Dry run (no downloads, no writes) to preview grouping from metadata only:
    python build.py --plan-only
"""

import argparse
import os
import sys

from hypatia.catalog import load_catalog
from hypatia.popularity import crawl_popularity
from hypatia.grouping import group_into_shelves, select_with_quotas, Shelf
from hypatia.curation import load_blocklist, load_picks
from hypatia.fetch import fetch_book
from hypatia.emit import emit_shelf, emit_index


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Pinakes data set from Project Gutenberg.")
    ap.add_argument("--catalog", default="pg_catalog.csv", help="path to offline pg_catalog.csv")
    ap.add_argument("--pinakes", default="../Pinakes", help="output dir (the Pinakes repo)")
    ap.add_argument("--cache", default="cache", help="cache dir (gitignored)")
    ap.add_argument("--languages", default="en", help="comma-separated language filter")
    ap.add_argument("--limit", type=int, default=2000,
                    help="max books to actually shelve (selected from the crawl pool)")
    ap.add_argument("--crawl", type=int, default=10000,
                    help="how deep to crawl Gutendex popularity (the selection pool; "
                         "larger = deeper genre cuts available for quotas)")
    ap.add_argument("--per-shelf", type=int, default=20, help="max books per shelf slot")
    ap.add_argument("--max-per-theme", type=int, default=6,
                    help="max slots any single theme may occupy (breadth control)")
    ap.add_argument("--blocklist-dir", default="blocklist", help="dir of blocklist policy files")
    ap.add_argument("--curated-dir", default="curated", help="dir of curated pick lists (*.txt)")
    ap.add_argument("--fetch-delay", type=float, default=1.5, help="seconds between live book fetches")
    ap.add_argument("--refresh-popularity", action="store_true", help="force Gutendex re-crawl")
    ap.add_argument("--plan-only", action="store_true", help="print grouping plan; no fetch/emit")
    ap.add_argument("--curated-only", action="store_true",
                    help="build ONLY the curated pick shelves (no auto-themed shelves, "
                         "no popularity crawl needed) — for pivoting to a tightly "
                         "hand-curated library")
    args = ap.parse_args(argv)

    langs = {l.strip() for l in args.languages.split(",") if l.strip()}
    book_cache = os.path.join(args.cache, "books")
    pop_cache = os.path.join(args.cache, "gutendex_popularity.json")

    # 1. Catalog (offline) -----------------------------------------------------
    print(f"[catalog] reading {args.catalog} (languages={sorted(langs)})...")
    catalog = {b.id: b for b in load_catalog(args.catalog, languages=langs)}
    print(f"[catalog] {len(catalog)} '{'/'.join(sorted(langs))}' text records")

    # 2. Popularity (cached network) -------------------------------------------
    # Curated pick shelves reference the catalog by ID directly, so a curated-only
    # build needs NO popularity data at all — skip the crawl entirely.
    if args.curated_only:
        print("[curated-only] skipping popularity crawl and auto-theming")
        pop = {}
    else:
        # Crawl a DEEP pool (args.crawl) so genre quotas can reach below the global
        # top-`limit`; selection/shelving still trims to args.limit later.
        pop = crawl_popularity(
            pop_cache, languages=args.languages, max_books=args.crawl,
            refresh=args.refresh_popularity,
        )

    # Merge popularity into catalog books.
    for bid, meta in pop.items():
        book = catalog.get(bid)
        if book:
            book.download_count = meta.get("download_count", 0)
            book.summary = meta.get("summary", "")

    # 2b. Curation: blocklist (deny) + picks (force onto reserved low slots) ---
    blocklist = load_blocklist(args.blocklist_dir)
    picklists = load_picks(args.curated_dir)

    # Build the eligible pool: in both catalog & popularity crawl, not blocked.
    full_pool = []
    blocked = 0
    for bid, meta in pop.items():
        book = catalog.get(bid)
        if not book:
            continue
        reason = blocklist.blocks(book)
        if reason:
            blocked += 1
            print(f"[blocklist] excluded id={book.id} {book.title[:40]!r} ({reason})")
            continue
        full_pool.append(book)
    full_pool.sort(key=lambda b: b.download_count, reverse=True)
    if not args.curated_only:
        print(f"[select] {len(full_pool)} eligible (crawl {args.crawl}); {blocked} blocked")

    # Genre-quota-aware selection: guarantee favored genres' deep cuts survive the
    # top-`limit` trim (they'd otherwise be culled below the global popularity fold).
    # (Skipped in curated-only mode — there's no auto pool to select from.)
    pool = [] if args.curated_only else select_with_quotas(full_pool, args.limit)

    # 3a. Curated pick shelves occupy the reserved low band (slots 00..k-1).
    pool_by_id = {b.id: b for b in pool}
    pick_shelves = []
    for slot, pl in enumerate(picklists):
        books = [catalog[i] for i in pl.ids if i in catalog]
        # Exclusive: remove picked books from the auto pool so they appear once.
        for b in books:
            pool_by_id.pop(b.id, None)
        missing = [i for i in pl.ids if i not in catalog]
        pick_shelves.append(Shelf(slot=slot, theme=pl.label, books=books))
        print(f"[picks] slot {slot:02d} {pl.label!r}: {len(books)} books"
              + (f" ({len(missing)} unknown ids skipped)" if missing else ""))

    reserved = len(pick_shelves)

    # 3b. Auto-theme the remainder into the slots after the reserved band.
    # Curated-only mode ships ONLY the pick shelves — no auto-themed shelves.
    if args.curated_only:
        if not pick_shelves:
            print("[curated-only] WARNING: no curated pick lists found in "
                  f"{args.curated_dir!r}; nothing to build")
        auto_shelves = []
    else:
        auto_shelves = group_into_shelves(
            list(pool_by_id.values()),
            max_books_per_shelf=args.per_shelf,
            max_slots_per_theme=args.max_per_theme,
            start_slot=reserved,
        )
    shelves = pick_shelves + auto_shelves

    if args.plan_only:
        print("\n=== PLAN (no fetch/emit) ===")
        from hypatia.text import normalize_field
        for s in sorted(shelves, key=lambda s: s.slot):
            top = ", ".join(normalize_field(b.title)[:32] for b in s.books[:3])
            print(f"  slot {s.slot:02d}  {s.theme:<28} {len(s.books):>3} books   e.g. {top}")
        total = sum(len(s.books) for s in shelves)
        print(f"\n  {len(shelves)} shelves, {total} books. (rerun without --plan-only to build)")
        return 0

    # 4/5. Fetch + emit --------------------------------------------------------
    os.makedirs(args.pinakes, exist_ok=True)

    def get_raw(book_id):
        return fetch_book(book_id, book_cache, delay_s=args.fetch_delay)

    for s in sorted(shelves, key=lambda s: s.slot):
        written_ids = set()
        # emit_shelf returns count, but we also want to prune the catalog to books
        # that actually made it onto the shelf; do a wrapping get_raw to record hits.
        def tracking_get_raw(book_id):
            raw = get_raw(book_id)
            if raw is not None:
                written_ids.add(book_id)
            return raw
        emit_shelf(s, args.pinakes, tracking_get_raw)
        s.books = [b for b in s.books if b.id in written_ids]

    # index.txt reflects only books actually written.
    shelves = [s for s in shelves if s.books]
    emit_index(shelves, args.pinakes)

    print(f"\nDone. Wrote {len(shelves)} shelves to {args.pinakes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
