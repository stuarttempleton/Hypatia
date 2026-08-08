"""Assign books to themed slots.

Two-tier classifier, most-trusted signal first:
  1. PG **Bookshelves** — Gutenberg's own human-curated category column (~95% of
     popular books have one, e.g. "Category: Crime, Thrillers and Mystery"). We map
     these curated categories to our themes. This is the primary, high-quality signal.
  2. **Subject keyword** fallback — only for the ~5% of books with no usable
     bookshelf; the old loose substring match on the subjects field.

Both tables are ordered: first match wins, more specific themes higher. Grouping is
in the CONTRACT's "Flexible" layer, so changing any of this never requires a world
rebuild — re-run the build and the shelves re-theme.
"""

from dataclasses import dataclass

from .catalog import Book
from .contract import SLOT_COUNT


# --- Tier 1: PG Bookshelf category -> theme -------------------------------
# A book's bookshelves AND subjects are combined into one lowercase haystack, and
# these rules are tried in ORDER — first theme with any matching phrase wins. This
# is deliberately priority-ordered by SPECIFICITY, not by field: a distinctive genre
# signal ("horror tales", "gothic fiction", "detective fiction") outranks generic
# catch-alls ("category: novels", "fiction") even when both are present. Verified
# against real PG tagging — e.g. Dracula carries {Horror, Gothic Fiction, Mystery
# Fiction, Category: Sci-Fi & Fantasy, Category: Novels} and lands in Horror because
# the horror/gothic signals sit above the generic novel/sci-fi catches.
#
# Multi-genre works are inherently ambiguous (Frankenstein = SciFi+Horror+Gothic);
# order encodes the editorial call. Tune freely — it's Flexible-layer.
CLASSIFY_RULES: list[tuple[str, tuple[str, ...]]] = [
    # --- Most distinctive genre signals first ---
    ("Horror & Gothic",           ("horror", "gothic fiction", "gothic",
                                    "horror tales", "ghost stories", "vampires")),
    ("Drama & Plays",             ("plays/films/dramas", "one act plays",
                                    "drama", "-- drama", "plays")),
    ("Poetry",                    ("poetry", "poems", "-- poetry")),
    ("Mystery & Detective",       ("detective fiction", "mystery fiction",
                                    "crime, thrillers and mystery",
                                    "detective and mystery stories")),
    ("Science Fiction & Fantasy", ("science fiction", "science-fiction & fantasy",
                                    "precursors of science fiction", "fantasy fiction",
                                    "fantasy", "utopias")),
    ("Myth & Folklore",           ("mythology, legends & folklore", "mythology",
                                    "folklore", "fairy tales", "legends")),
    ("Adventure & Westerns",      ("adventure", "sea stories", "western stories",
                                    "-- fiction -- adventure")),
    # --- Audience / form themes ---
    ("Children's & Fairy Tales",  ("children & young adult", "children's book series",
                                    "children's", "juvenile fiction", "juvenile")),
    ("Romance & Love",            ("romance", "love stories")),
    ("Humour & Satire",           ("humour", "humor", "satire", "wit and humor")),
    ("Short Stories",             ("short stories",)),
    # --- Non-fiction ---
    ("History & Biography",       ("biographies", "biography", "autobiography",
                                    "history -", "historical novels", "warfare",
                                    "travel writing", "-- history")),
    ("Philosophy & Religion",     ("philosophy & ethics", "philosophy",
                                    "religion/spirituality", "religion", "ethics",
                                    "theology")),
    ("Science & Nature",          ("science -", "nature/gardening/animals",
                                    "health & medicine", "how to", "natural history",
                                    "botany", "astronomy")),
    ("Essays & Speeches",         ("essays, letters & speeches", "politics", "essays")),
    # --- Broad literary catch-alls LAST (only if nothing specific matched) ---
    ("Fiction & Novels",          ("novels", "british literature", "american literature",
                                    "french literature", "german literature",
                                    "classics of literature", "best books ever",
                                    "harvard classics", "fiction")),
]

MISC_LABEL = "Misc & Classics"


# --- Audience weighting ----------------------------------------------------
# Gutenberg's raw download counts reflect the general internet (heavy on
# children's, history, general fiction). Our audience (VRChat) skews toward
# speculative fiction and theatrical works. These multipliers bias slot
# allocation toward that audience: a theme's "hunger" for slots is scaled by its
# weight, so favored themes win contested slots. 1.0 = neutral (pure popularity);
# >1 favors, <1 de-emphasizes. This does NOT change categorization, only how many
# shelves each theme earns. Tune freely — it's Flexible-layer.
THEME_WEIGHTS: dict[str, float] = {
    "Science Fiction & Fantasy": 2.5,
    "Horror & Gothic":           2.5,
    "Poetry":                    2.0,
    "Drama & Plays":             2.0,
    "Mystery & Detective":       1.5,
    "Myth & Folklore":           1.5,
    # de-emphasized relative to their raw popularity:
    "Children's & Fairy Tales":  0.5,
    "History & Biography":       0.7,
    # everything else defaults to 1.0 (neutral)
}
DEFAULT_WEIGHT = 1.0


# --- Per-theme slot caps ---------------------------------------------------
# Override the global max_slots_per_theme for specific themes. Use to directly
# suppress general themes that would otherwise dominate on volume (History) or that
# our audience cares less about (Children's), and to let favored genres grow past
# the global cap. A theme absent here uses the global max_slots_per_theme.
# Numbers finalized after the 10k crawl — these are the intended shape.
THEME_CAPS: dict[str, int] = {
    "History & Biography":       3,
    "Children's & Fairy Tales":  2,
    "Science Fiction & Fantasy": 8,
    "Horror & Gothic":           6,
    "Poetry":                    5,
    "Drama & Plays":             4,
    "Mystery & Detective":       6,
}


# --- Genre quotas (selection) ----------------------------------------------
# The global popularity cutoff (--limit) would trim favored-genre deep cuts before
# grouping ever sees them (verified against the 10k crawl: e.g. Drama has only 38
# books in the top 2000 but 312 in the top 10000). A quota guarantees the top-N
# books WITHIN a genre survive selection even if globally ranked below the cutoff,
# so a genre's shelves can actually be filled. Set to comfortably exceed
# cap*per_shelf for each favored theme. Themes absent here get no quota (they rely
# on the normal top-`limit` selection).
GENRE_QUOTAS: dict[str, int] = {
    "Science Fiction & Fantasy": 170,   # cap 8 * 20 = 160 needed; pool has 396
    "Horror & Gothic":           125,   # cap 6 * 20 = 120 needed; pool has 127 (snug)
    "Poetry":                    110,   # cap 5 * 20 = 100 needed; pool has 604
    "Drama & Plays":             90,    # cap 4 * 20 = 80 needed;  pool has 312
    "Mystery & Detective":       130,   # cap 6 * 20 = 120 needed; pool has 320
    "Myth & Folklore":           70,    # helps this favored-ish theme too
}


def select_with_quotas(
    ranked_pool: list[Book],
    limit: int,
    quotas: dict[str, int] | None = None,
    log=print,
) -> list[Book]:
    """Select up to `limit` books, but first guarantee each genre's quota.

    `ranked_pool` must be pre-sorted by download_count desc (most popular first).
    Returns books preserving that popularity order.

    Two passes:
      1. For each theme with a quota, take its top-N (by the pool's existing order)
         even if those books rank below the global `limit` cutoff.
      2. Fill the rest of `limit` with the next most-popular books not already taken.
    If quota books alone exceed `limit`, we keep all of them (favored genres win)
    and log that the effective count exceeds the nominal limit.
    """
    if quotas is None:
        quotas = GENRE_QUOTAS

    taken: dict[int, Book] = {}
    per_theme_count: dict[str, int] = {}

    # Pass 1: quotas (pool is already popularity-ordered, so first-seen = top-N).
    for b in ranked_pool:
        t = _theme_for(b)
        q = quotas.get(t)
        if q and per_theme_count.get(t, 0) < q:
            taken[b.id] = b
            per_theme_count[t] = per_theme_count.get(t, 0) + 1

    quota_n = len(taken)

    # Pass 2: fill remaining budget with the most popular not-yet-taken books.
    for b in ranked_pool:
        if len(taken) >= limit:
            break
        if b.id not in taken:
            taken[b.id] = b

    # Preserve popularity order in the result.
    result = [b for b in ranked_pool if b.id in taken]
    if quota_n > limit:
        log(f"[select] quotas ({quota_n}) exceed limit ({limit}); "
            f"keeping all quota books -> {len(result)} selected")
    else:
        log(f"[select] {len(result)} selected (limit {limit}; "
            f"{quota_n} guaranteed by genre quotas)")
    return result


def _theme_for(book: Book) -> str:
    # Combine bookshelves + subjects into one haystack; distinctive genre signals
    # (near the top of CLASSIFY_RULES) win over generic catch-alls regardless of
    # which field they appear in.
    haystack = " ; ".join(book.bookshelves + book.subjects).lower()
    for label, phrases in CLASSIFY_RULES:
        if any(p in haystack for p in phrases):
            return label
    return MISC_LABEL


@dataclass
class Shelf:
    slot: int
    theme: str
    books: list[Book]


def group_into_shelves(
    books: list[Book],
    max_slots: int = SLOT_COUNT,
    max_books_per_shelf: int = 20,
    min_books_per_shelf: int = 3,
    max_slots_per_theme: int | None = None,
    start_slot: int = 0,
    theme_weights: dict[str, float] | None = None,
    theme_caps: dict[str, int] | None = None,
    log=print,
) -> list[Shelf]:
    """Group books by theme, then allocate slots *proportionally* across themes.

    Allocation favors BREADTH over depth so a library shows variety rather than 22
    shelves of one genre:
      1. Every theme with >= min_books_per_shelf books is guaranteed 1 slot (floor),
         as long as slots remain. This ensures Poetry/Sci-Fi/Drama etc. appear even
         when a giant theme like History could otherwise eat the whole ceiling.
      2. Remaining slots are handed out one at a time to whichever theme currently
         has the most *unshelved* books (largest-remainder / proportional fairness).
      3. No theme exceeds max_slots_per_theme (defaults to a cap that keeps any one
         theme under ~1/4 of all slots).
      4. Within a theme, most-popular books fill the allotted slots first; overflow
         beyond a theme's slot allotment is dropped (logged).

    Slots are numbered by theme popularity, starting at `start_slot` (biggest-draw
    theme gets the lowest available slot). `start_slot` lets a reserved band (e.g.
    curated picks) occupy the low slots so auto-themes fill only what remains.
    """
    available_slots = max(0, max_slots - start_slot)
    if max_slots_per_theme is None:
        # keep any single theme to at most ~a quarter of the *available* shelf space
        max_slots_per_theme = max(1, available_slots // 4)
    if theme_weights is None:
        theme_weights = THEME_WEIGHTS
    if theme_caps is None:
        theme_caps = THEME_CAPS

    def weight(label: str) -> float:
        return theme_weights.get(label, DEFAULT_WEIGHT)

    def theme_cap(label: str) -> int:
        # per-theme override wins over the global cap
        return theme_caps.get(label, max_slots_per_theme)

    by_theme: dict[str, list[Book]] = {}
    for b in books:
        by_theme.setdefault(_theme_for(b), []).append(b)

    # Fold tiny themes into Misc so we don't waste slots on 1-book themes.
    misc = by_theme.pop(MISC_LABEL, [])
    for label in list(by_theme):
        if len(by_theme[label]) < min_books_per_shelf:
            misc.extend(by_theme.pop(label))
    if misc:
        by_theme[MISC_LABEL] = misc

    # Order books within a theme (most popular first).
    for label in by_theme:
        by_theme[label].sort(key=lambda b: b.download_count, reverse=True)

    def slots_needed(label: str) -> int:
        import math
        return min(
            theme_cap(label),
            max(1, math.ceil(len(by_theme[label]) / max_books_per_shelf)),
        )

    # --- Slot allocation ---------------------------------------------------
    alloc: dict[str, int] = {}
    remaining_slots = available_slots

    # Step 1: floor — one slot per eligible theme (by theme popularity order).
    theme_order = sorted(
        by_theme,
        key=lambda lbl: sum(b.download_count for b in by_theme[lbl]),
        reverse=True,
    )
    for label in theme_order:
        if remaining_slots <= 0:
            break
        alloc[label] = 1
        remaining_slots -= 1

    # Step 2: weighted-proportional — give each remaining slot to the theme with
    # the highest weighted hunger (unshelved books scaled by audience weight),
    # respecting the per-theme cap. Favored themes win contested slots.
    while remaining_slots > 0:
        best = None
        best_score = 0.0
        for label in theme_order:
            if alloc.get(label, 0) >= slots_needed(label):
                continue  # theme fully served or at cap
            unshelved = len(by_theme[label]) - alloc.get(label, 0) * max_books_per_shelf
            score = unshelved * weight(label)
            if score > best_score:
                best_score, best = score, label
        if best is None:
            break  # every theme fully served; leftover slots stay empty
        alloc[best] += 1
        remaining_slots -= 1

    # --- Emit shelves in theme-popularity order ---------------------------
    shelves: list[Shelf] = []
    slot = start_slot
    dropped = 0
    for label in theme_order:
        n_slots = alloc.get(label, 0)
        if n_slots == 0:
            dropped += len(by_theme[label])
            continue
        capacity = n_slots * max_books_per_shelf
        chunk = by_theme[label][:capacity]
        dropped += len(by_theme[label]) - len(chunk)
        for i in range(0, len(chunk), max_books_per_shelf):
            batch = chunk[i : i + max_books_per_shelf]
            for b in batch:
                b.slot = slot
            shelves.append(Shelf(slot=slot, theme=label, books=batch))
            slot += 1

    placed = sum(len(s.books) for s in shelves)
    log(f"[grouping] {len(shelves)} shelves across {slot} slots, "
        f"{len(alloc)} themes; {placed} books placed"
        + (f", {dropped} dropped (theme caps / ceiling)" if dropped else ""))
    return shelves
