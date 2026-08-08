"""Frozen constants mirroring CONTRACT.md.

These MUST match the values baked into any consumer (e.g. the Library-of-Alexandria
VRChat world). Changing anything here is a breaking change for shipped clients.
"""

# --- Frozen: slots ---------------------------------------------------------
SLOT_COUNT = 64  # slots 00..63; this is a CEILING, not a requirement.

# --- Frozen: hosting / filenames ------------------------------------------
GITHUB_USER = "stuarttempleton"
REPO = "Pinakes"
BRANCH = "main"
HOST = f"raw.githubusercontent.com/{GITHUB_USER}/{REPO}/{BRANCH}/"

INDEX_FILE = "index.txt"


def shelf_filename(slot: int) -> str:
    """shelf_NN.txt with two-digit zero-padded slot number."""
    if not 0 <= slot < SLOT_COUNT:
        raise ValueError(f"slot {slot} out of range 0..{SLOT_COUNT - 1}")
    return f"shelf_{slot:02d}.txt"


# --- Frozen: delimiters (printable UTF-8 only; no control bytes) ----------
# Record separator: a printable sentinel on its own line. Hypatia guarantees it
# never occurs inside a book body or metadata field before writing.
RECORD_DELIM = "\n===PINAKES-REC===\n"
# Field separator within a header/catalog line.
FIELD_DELIM = "\t"
# Separator within the multi-valued subjects field.
SUBJECT_DELIM = ";"

# The raw sentinel token (without surrounding newlines) — used for collision checks.
RECORD_SENTINEL = "===PINAKES-REC==="

# --- Informational: client limits (from the vrchat/ client; not ours) -----
CLIENT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # VRCStringDownloader cap
CLIENT_MIN_REQUEST_DELAY_S = 5.0               # global min delay between downloads
