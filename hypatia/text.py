"""Text hygiene: field normalization, PG boilerplate stripping, collision guard.

Everything Hypatia writes must be printable UTF-8 with only ordinary newlines, and
must never contain the record sentinel or (in header/catalog lines) a tab. These
helpers enforce that so downstream `String.Split` parsing needs zero escaping logic.
"""

import re

from .contract import RECORD_SENTINEL, FIELD_DELIM

# --- Field normalization ---------------------------------------------------

_WS_RUN = re.compile(r"\s+")


def normalize_field(value: str) -> str:
    """Collapse all whitespace (tabs, newlines, CRs, runs of spaces) to single
    spaces and strip ends. Safe for a tab-delimited, newline-terminated line.

    This is the guard against messy titles/authors (e.g. the Bill of Rights row
    with an embedded newline) silently corrupting a header split.
    """
    if value is None:
        return ""
    # Drop the sentinel token defensively (astronomically unlikely in a title,
    # but a metadata field must never carry it).
    value = value.replace(RECORD_SENTINEL, " ")
    return _WS_RUN.sub(" ", value).strip()


def join_fields(*fields: str) -> str:
    """Join normalized fields with the field delimiter into one header/catalog line."""
    return FIELD_DELIM.join(normalize_field(f) for f in fields)


# --- Collision guard -------------------------------------------------------


def assert_body_safe(body: str, book_id) -> None:
    """Raise if a book body contains the record sentinel (would break record split).

    Vanishingly rare in real prose, but a hard invariant of the format. The caller
    decides whether to escape, skip, or abort; we surface it loudly rather than
    silently corrupting a shelf.
    """
    if RECORD_SENTINEL in body:
        raise ValueError(
            f"book {book_id}: body contains record sentinel {RECORD_SENTINEL!r}; "
            "cannot emit without corrupting shelf record boundaries"
        )


# --- PG boilerplate stripping ---------------------------------------------

# Project Gutenberg wraps the actual work between star-banner lines:
#   *** START OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
#   ...work...
#   *** END OF THE PROJECT GUTENBERG EBOOK <TITLE> ***
# Older files use "SMALL PRINT" / slightly different wording; we match loosely.
_START_RE = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.IGNORECASE | re.DOTALL,
)
_END_RE = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.IGNORECASE | re.DOTALL,
)


def strip_boilerplate(raw: str) -> str:
    """Return just the work: the text between the START and END star banners.

    Falls back gracefully: if a banner is missing, keeps what it can rather than
    returning empty. Also normalizes line endings to '\\n' and trims a leading BOM.
    """
    text = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")

    start_m = _START_RE.search(text)
    end_m = _END_RE.search(text)

    if start_m:
        body_start = start_m.end()
    else:
        body_start = 0  # no start banner found; keep from the top

    if end_m and end_m.start() > body_start:
        body_end = end_m.start()
    else:
        body_end = len(text)  # no end banner found; keep to the end

    body = text[body_start:body_end]

    # Collapse the big runs of blank lines that banners leave behind, and trim ends.
    body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")
    return body


def has_boilerplate_markers(raw: str) -> bool:
    """True if both PG star banners were found — useful for logging strip quality."""
    return bool(_START_RE.search(raw) and _END_RE.search(raw))
