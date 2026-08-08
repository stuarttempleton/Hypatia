# Pinakes Data Contract

The frozen agreement between the two independently-shipped repos:

- **Hypatia** (this repo, public, MIT) — the builder that *writes* the data.
- **Library-of-Alexandria** (private) — the VRChat world that *reads* the data.
- **Pinakes** (public, CC0) — the data repo the world fetches from.

Why this file exists: because Udon cannot build `VRCUrl`s at runtime, the world
**bakes in the shelf URLs at author time**. Once a world build ships, those URLs
can't be hot-patched. That makes the URL scheme, slot count, and delimiters a
*permanent contract*. Everything in the **Frozen** section below must never change
without a coordinated world rebuild. Everything in the **Flexible** section can
change every rotation with zero world changes.

---

## Frozen (never change without a world rebuild)

### Host & URL scheme

Files are served from GitHub raw — a direct `200` with `Content-Type: text/plain`,
**no redirect** (the client's `VRCStringDownloader` has `redirectLimit = 0` and
cannot follow one).

```
https://raw.githubusercontent.com/stuarttempleton/Pinakes/main/index.txt
https://raw.githubusercontent.com/stuarttempleton/Pinakes/main/shelf_00.txt
https://raw.githubusercontent.com/stuarttempleton/Pinakes/main/shelf_01.txt
…
https://raw.githubusercontent.com/stuarttempleton/Pinakes/main/shelf_63.txt
```

- Branch is **`main`**. Renaming the repo, the owner, or the branch breaks every
  shipped world.
- Filenames are exactly `index.txt` and `shelf_NN.txt`, where `NN` is a
  **two-digit zero-padded** slot number, `00`–`63`.

### Slot count

```
SLOT_COUNT = 64        # slots 00..63
```

This is a **ceiling, not a requirement.** The world authors all 64 `VRCUrl`s once.
Hypatia may populate *any subset* of them in a given rotation — `index.txt` declares
which slots are live (see below). The client MUST NOT assume a slot has data; it
reads the manifest and only offers slots the manifest lists.

### File format — `.txt`, printable UTF-8 only

All files are UTF-8 text with **only printable characters + normal newlines**.
No control bytes (`\x1e`/`\x1f` etc.) — this keeps the files honestly `text/plain`
(WAF/CDN friendly), keeps git diffs textual, and matches the `.txt` extension.

### Delimiters

Records are separated by a **printable sentinel on its own line**:

```
RECORD_DELIM = "\n===PINAKES-REC===\n"
```

The header line within a shelf record uses a **tab** to separate fields:

```
FIELD_DELIM  = "\t"
```

**Collision guarantee (Hypatia's responsibility):** before writing, Hypatia MUST
assert that `RECORD_DELIM` and `FIELD_DELIM` do not occur inside any book body or
metadata field. Tabs are stripped/normalized from bodies; if the record sentinel
ever appears in a body (vanishingly rare in prose), Hypatia escapes or skips it and
logs. This lets the client parse with plain `String.Split(string[])` and
`String.Split(char[])` (both confirmed Udon-exposed) with zero escaping logic.

### `index.txt` layout — the manifest + catalog

Two sections, in order, separated by `RECORD_DELIM`:

**1. Manifest section (first record):** one line per *live* slot, listing which
slots have data this rotation and a human label for the shelf theme.

```
slot<TAB>theme_label<TAB>book_count
00<TAB>Fiction &amp; Novels<TAB>14
01<TAB>Mystery &amp; Detective<TAB>18
07<TAB>Poetry<TAB>11
…
```

(Slots absent from this list are empty — the client offers only listed slots.)

**2. Catalog section (remaining records):** one record per book, each a single line
of tab-separated fields, in this exact field order:

```
id<TAB>title<TAB>author<TAB>subjects<TAB>download_count<TAB>shelf_slot
```

- `id` — Gutenberg text number (e.g. `1342`)
- `title`, `author` — display strings (tabs/newlines normalized out)
- `subjects` — `;`-separated within the single field
- `download_count` — integer popularity from Gutendex (for sort/rank)
- `shelf_slot` — which `NN` slot this book's text lives on

The catalog powers browse/search over all ~1–2k books. A book appears in the
catalog only if its `shelf_slot` is a live slot in the manifest.

### `shelf_NN.txt` layout — the readable texts

Records separated by `RECORD_DELIM`. Each record is one book: a **header line**,
a newline, then the **body** (the stripped public-domain text, which may contain
newlines — that's fine, records split on the sentinel, not on `\n`).

```
<id><TAB><title><TAB><author>
<full book body …>
===PINAKES-REC===
<next id><TAB><title><TAB><author>
<next body …>
```

The client splits a shelf on `RECORD_DELIM`, then splits each record's **first line**
on `FIELD_DELIM` for metadata; everything after the first newline is the body,
paged in the reader via `Substring(offset, window)`.

---

## Flexible (may change every rotation, no world changes)

- **Which slots are populated** and how many books each holds (declared by the manifest).
- **Theme grouping** — how books map to slots; retheme freely.
- **Catalog contents** — which ~1–2k books are included, their ranking, metadata.
- **Book bodies** — re-stripped, re-sourced, updated any time.

The world only ever learns the current state by fetching `index.txt` on load.
The index references shelves **by slot number, never by URL**, so the URL scheme
stays frozen while everything it points at rotates underneath.

---

## Constants (single source of truth — mirror in both repos)

| Constant | Value |
|---|---|
| `SLOT_COUNT` | `64` (slots `00`–`63`) |
| `HOST` | `raw.githubusercontent.com/stuarttempleton/Pinakes/main/` |
| `INDEX_FILE` | `index.txt` |
| `SHELF_FILE` | `shelf_NN.txt` (two-digit zero-padded) |
| `RECORD_DELIM` | `\n===PINAKES-REC===\n` |
| `FIELD_DELIM` | `\t` (tab) |
| `SUBJECT_DELIM` | `;` (within the subjects field) |

Relevant client limits (from the `vrchat/` client, informational — not ours to change):
`VRCStringDownloader` max download **100 MB**; **5 s** global min delay between
requests; **no redirects**; `str[i]` indexer not Udon-exposed (use `Substring`).
