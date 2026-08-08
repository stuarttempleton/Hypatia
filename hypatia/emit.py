"""Emit index.txt + shelf_NN.txt into the Pinakes output dir, per CONTRACT.md.

index.txt  = manifest record (live slots) + one catalog record per placed book.
shelf_NN.txt = per-book records: a tab-delimited header line, newline, then body.

All output is printable UTF-8; the record sentinel is asserted absent from bodies
before writing (text.assert_body_safe), so consumers parse with plain String.Split.
"""

import os

from .contract import (
    INDEX_FILE,
    RECORD_DELIM,
    SUBJECT_DELIM,
    shelf_filename,
)
from .grouping import Shelf
from .text import assert_body_safe, join_fields, strip_boilerplate


def emit_shelf(shelf: Shelf, out_dir: str, get_raw, log=print) -> int:
    """Write one shelf_NN.txt. `get_raw(book_id) -> raw text or None`.

    Returns the number of books actually written (a book whose text is missing or
    whose body trips the collision guard is skipped and logged).
    """
    records: list[str] = []
    written = 0
    for book in shelf.books:
        raw = get_raw(book.id)
        if raw is None:
            log(f"[emit]   slot {shelf.slot:02d}: skip id={book.id} (no text)")
            continue
        body = strip_boilerplate(raw)
        try:
            assert_body_safe(body, book.id)
        except ValueError as e:
            log(f"[emit]   slot {shelf.slot:02d}: skip id={book.id} ({e})")
            continue
        header = join_fields(str(book.id), book.title, book.author)
        records.append(header + "\n" + body)
        written += 1

    path = os.path.join(out_dir, shelf_filename(shelf.slot))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(RECORD_DELIM.join(records))
    log(f"[emit] wrote {shelf_filename(shelf.slot)} ({written} books, {shelf.theme})")
    return written


def emit_index(shelves: list[Shelf], out_dir: str, log=print) -> None:
    """Write index.txt: manifest section, then catalog section.

    Only books actually written to a shelf should appear in the catalog. We rely on
    the caller having pruned shelf.books to what was emitted (see build.py), or the
    catalog simply reflects placement intent — acceptable for MVP since the client
    tolerates a catalog entry whose text is momentarily absent.
    """
    # Manifest: one line per live slot -> slot, theme, book_count
    manifest_lines = [
        join_fields(f"{s.slot:02d}", s.theme, str(len(s.books)))
        for s in sorted(shelves, key=lambda s: s.slot)
    ]
    manifest_record = "\n".join(manifest_lines)

    # Catalog: one line per book -> id, title, author, subjects, downloads, slot
    catalog_lines = []
    for s in shelves:
        for b in s.books:
            catalog_lines.append(
                join_fields(
                    str(b.id),
                    b.title,
                    b.author,
                    SUBJECT_DELIM.join(b.subjects),
                    str(b.download_count),
                    f"{s.slot:02d}",
                )
            )
    catalog_record = "\n".join(catalog_lines)

    path = os.path.join(out_dir, INDEX_FILE)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(manifest_record + RECORD_DELIM + catalog_record)
    log(f"[emit] wrote {INDEX_FILE} ({len(manifest_lines)} slots, {len(catalog_lines)} books)")
