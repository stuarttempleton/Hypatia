"""Parse the offline pg_catalog.csv into Book records.

pg_catalog.csv columns: Text#, Type, Issued, Title, Language, Authors, Subjects,
LoCC, Bookshelves. It is proper RFC-4180 CSV (quoted fields may contain commas and
embedded newlines), so we use the stdlib csv module — never a naive line split.
"""

import csv
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Book:
    id: int
    title: str
    author: str
    language: str
    subjects: list[str] = field(default_factory=list)
    bookshelves: list[str] = field(default_factory=list)
    # Filled in by later stages:
    download_count: int = 0
    summary: str = ""
    slot: int | None = None


def _split_multi(value: str) -> list[str]:
    """Gutenberg packs multi-values as 'a; b; c' within one CSV field."""
    if not value:
        return []
    return [p.strip() for p in value.split(";") if p.strip()]


def load_catalog(csv_path: str, languages: set[str] | None = None) -> Iterator[Book]:
    """Yield Book records from pg_catalog.csv.

    languages: if given, keep only rows whose Language is in the set (e.g. {"en"}).
               Gutenberg language codes can be multi-valued too (e.g. "en; fr").
    Only rows of Type == "Text" are yielded (skips Sound, Collection, Dataset...).
    """
    # csv has a default field-size limit that a few huge PG rows can exceed.
    csv.field_size_limit(10 * 1024 * 1024)

    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("Type") != "Text":
                continue

            raw_id = (row.get("Text#") or "").strip()
            if not raw_id.isdigit():
                continue
            book_id = int(raw_id)

            langs = _split_multi(row.get("Language", ""))
            # Filter on the PRIMARY (first-listed) language only. Bilingual editions
            # like "de; en" (e.g. Wittgenstein's Tractatus) would otherwise leak into
            # an English library and, since the text is really German, both break the
            # mirror filename convention (MISS on download) and don't belong.
            if languages is not None and (not langs or langs[0] not in languages):
                continue

            authors = _split_multi(row.get("Authors", ""))
            # Author fields look like "Austen, Jane, 1775-1817"; keep the name part.
            primary_author = authors[0] if authors else ""

            yield Book(
                id=book_id,
                title=(row.get("Title") or "").strip(),
                author=primary_author,
                language=langs[0] if langs else "",
                subjects=_split_multi(row.get("Subjects", "")),
                bookshelves=_split_multi(row.get("Bookshelves", "")),
            )
