"""Curation policy: blocklist (deny) + curated picks (force onto named shelves).

Two hand-authored, version-controlled policy inputs, applied around the automatic
grouping. Both live in the Hypatia repo (code/policy), not Pinakes (data), so the
"why" is diffable and auditable.

  blocklist/  — remove books from consideration entirely, BEFORE selection/grouping
      ids.txt          one Gutenberg id per line ('# reason' allowed)
      title_terms.txt  case-insensitive substring; deny if title contains it
      subject_terms.txt case-insensitive substring; deny if any subject/shelf has it

  curated/    — force chosen books onto named shelves in a reserved low-slot band
      *.txt   one file per pick shelf, sorted by filename -> slots 00, 01, ...
              first non-blank line may be '# label: Voltur's Picks'
              remaining lines are Gutenberg ids ('# comment' allowed)

Picks are EXCLUSIVE: a picked book is pulled from the auto-grouping pool so it
appears exactly once (keeps the catalog's one-book -> one-slot invariant clean).
"""

import glob
import os
from dataclasses import dataclass, field


# --- Blocklist -------------------------------------------------------------


@dataclass
class Blocklist:
    ids: set[int] = field(default_factory=set)
    title_terms: list[str] = field(default_factory=list)
    subject_terms: list[str] = field(default_factory=list)

    def blocks(self, book) -> str | None:
        """Return a human reason if the book is blocked, else None."""
        if book.id in self.ids:
            return f"id in blocklist"
        title = book.title.lower()
        for t in self.title_terms:
            if t in title:
                return f"title term {t!r}"
        hay = " ; ".join(book.subjects + book.bookshelves).lower()
        for t in self.subject_terms:
            if t in hay:
                return f"subject term {t!r}"
        return None


def _read_lines(path: str) -> list[str]:
    """Read a policy file: strip inline '# ...' comments, drop blanks."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def load_blocklist(blocklist_dir: str) -> Blocklist:
    bl = Blocklist()
    for raw in _read_lines(os.path.join(blocklist_dir, "ids.txt")):
        if raw.isdigit():
            bl.ids.add(int(raw))
    bl.title_terms = [s.lower() for s in _read_lines(os.path.join(blocklist_dir, "title_terms.txt"))]
    bl.subject_terms = [s.lower() for s in _read_lines(os.path.join(blocklist_dir, "subject_terms.txt"))]
    return bl


# --- Curated picks ---------------------------------------------------------


@dataclass
class PickList:
    label: str
    ids: list[int]
    source: str  # filename, for logging


def load_picks(curated_dir: str) -> list[PickList]:
    """Load pick files sorted by filename; each becomes one shelf (low-slot band)."""
    picks: list[PickList] = []
    if not os.path.isdir(curated_dir):
        return picks
    for path in sorted(glob.glob(os.path.join(curated_dir, "*.txt"))):
        label = None
        ids: list[int] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if label is None and stripped.lower().startswith("# label:"):
                    label = stripped.split(":", 1)[1].strip()
                    continue
                body = stripped.split("#", 1)[0].strip()
                if body.isdigit():
                    ids.append(int(body))
        base = os.path.splitext(os.path.basename(path))[0]
        picks.append(PickList(label=label or base, ids=ids, source=os.path.basename(path)))
    return picks
