"""Merge Apple Photos duplicates deterministically, starting from Apple's own
Duplicates analysis, verified by czkawka.

scan/plan/review (READ-ONLY): harvest Apple's duplicate groups straight from
the Photos library database (the same groups the Duplicates album shows --
`ZASSET.ZDUPLICATEPERCEPTUALMATCHINGALBUM` / `ZDUPLICATEMETADATAMATCHINGALBUM`
carry the grouping itself, so nothing needs to be selected or exported),
cross-check every group with czkawka's exact (BLAKE3) and perceptual hashes,
compute a deterministic merge plan, and render an HTML review gallery. Nothing
in the library is modified by those stages.

apply/verify: execute ONLY the tranches approved in the review page's
decisions.json, in a safety-ordered sequence -- (1) album membership, keyword
and title/description transfer to the keeper via photoscript (Photos must be
running; --skip-photoscript to forgo), (2) merged dates and favorites on
keepers via the merge-helper binary (PhotoKit `PHAssetChangeRequest`, the
supported change API, so everything syncs to iCloud; build it once with
`make -C merge-helper`), (3) keeper dates re-verified against the live
database, and only then (4) losers deleted through ONE batched PhotoKit call:
a single system confirmation dialog for the whole run, everything lands in
Recently Deleted (30-day recovery). Tranches whose members changed since the
plan, or whose metadata transfer failed, are held back automatically and
retried on the next apply. Every apply writes an apply-log JSON (old dates,
deleted uuids) as the undo record; `verify` reports per-tranche completeness
afterwards. apply without --apply is a dry run.

The merge plan fixes what Apple's own Merge button gets wrong:

  * KEEPER (which pixels survive) is chosen by resolution, then file size --
    but only a MATERIAL size difference counts (outside --size-tolerance-pct,
    default 1%), because a few bytes of metadata padding on a multi-megabyte
    file says nothing about which copy is better. Copies within that band are
    equal quality, so the ladder moves on to format (RAW > HEIC > PNG/TIFF >
    JPEG), then the oldest timestamp, then the earliest import, then the
    shortest filename (suffixes like "-2" mark copies and re-saves), then
    UUID. So the original beats a re-import whose date drifted, while a
    genuinely bigger or higher-resolution file still wins outright.
  * ALL DATES ARE WALL CLOCK AT THE CAPTURE LOCATION, never the machine's
    current zone. Photos stores an absolute instant plus the capture UTC
    offset; EXIF stores a bare wall clock with no zone at all. Rendering the
    former in the machine's zone made the two disagree by exactly the
    travel offset (a London photo read an hour late on a laptop in Paris),
    which made apply "fix" a difference that never existed and write the
    wrong instant. Dates that get written carry the asset's own offset so
    merge-helper rebuilds the correct instant.

  * MERGED DATE is the OLDEST plausible timestamp found anywhere in the
    tranche: every member's Photos date plus its file's EXIF/QuickTime dates
    (DateTimeOriginal, CreateDate, CreationDate via exiftool). Implausible
    dates (epoch markers, before --min-plausible-year, in the future) are
    excluded but shown. Deterministic: same inputs, same answer, always.

Pipeline (each stage writes JSON the next one reads; rerun any stage):

    osxphotos run dedupe_photos.py scan     # library -> scan.json + czkawka runs
    osxphotos run dedupe_photos.py plan     # scan.json -> plan.json (pure, offline)
    osxphotos run dedupe_photos.py review   # plan.json -> report/index.html
    osxphotos run dedupe_photos.py all      # the three in sequence
    osxphotos run dedupe_photos.py apply --decisions decisions.json          # dry run
    osxphotos run dedupe_photos.py apply --decisions decisions.json --apply  # write
    osxphotos run dedupe_photos.py verify --decisions decisions.json         # check

scan readers (--reader):
    osxphotos  (default) rich metadata: albums, keywords, Live/RAW/burst
               pairing, derivative thumbnails. Loads the library via the
               osxphotos package, which COPIES Photos.sqlite AND its WAL to
               a temp dir -- so scan refuses to run this reader while the WAL
               is huge (see --max-wal-gb). Quit Photos and let it checkpoint
               first (reopen Photos once, or reboot), then rerun.
    sqlite     reads the live database directly (WAL honored, nothing copied,
               safe at any WAL size, works mid-import). Fewer fields: no
               albums/keywords, flags limited to hidden/trash/video/live.

czkawka verification (needs `czkawka_cli` on PATH; Homebrew build decodes
HEIC): originals are HARDLINKED into <out>/farm/ (no data copied) and czkawka
runs its `dup` (byte-identical), `image` (perceptual distance) and `video`
tools over the farm. By default the farm holds only Apple's group members
and czkawka merely VERIFIES Apple's claims. `scan --discover` instead farms
EVERY local, mergeable original and feeds czkawka's groups into the same
union-find as Apple's: copies Apple missed attach to their Apple tranches
(3-, 4-, N-member tranches), czkawka links merge Apple groups that are
really one photo, and czkawka-only tranches appear with their own source
chip. czkawka-only groups made entirely of one burst are dropped (burst
siblings, not duplicates; --include-bursts keeps them). Assets without a
local original are invisible to discovery -- scan reports how many. The
first discovery sweep perceptually hashes the whole library (hours; cached
and incremental afterwards). Each tranche is annotated with a verification
tier:

    exact       every member byte-identical (dup)
    visual-0    perceptual distance 0 and identical dimensions
    near        all members matched within --near distance
    video       video-signature match
    partial     czkawka matched some but not all members
    unverified  czkawka found no relation (Apple-only claim) -- review these!

The review page (static HTML, no dependencies) shows each tranche side by
side: thumbnails, metadata, every date candidate (implausible ones struck
through), the proposed keeper and merged date. Click anywhere on a member
card to make it the keeper. Vim-style keys (? shows the map): j/k/gg/G
navigate, a/x approve/reject and advance, u clears, n next undecided, h/l
cycle keeper, o reveals the keeper in Photos.app. Approve/reject in bulk,
then Export writes decisions.json for apply. Decisions persist in browser
localStorage keyed by plan fingerprint. `review --serve [PORT]` serves the
page on 127.0.0.1 (default 8942) and enables reveal-in-Photos (per-member
buttons + the o key) via a /reveal endpoint that AppleScript-spotlights the
asset; uuids are validated against the plan.

Everything is read-only toward the Photos library: the database is opened
read-only, farm entries are hardlinks, czkawka never deletes (its delete
method is never enabled), thumbnails are written under --out only.

Options (see each subcommand's --help):
    --out DIR                working directory (default: dedupe-out)
    --library PATH           Photos library (default: last-opened / ~/Pictures)
    --discover               sweep ALL local originals, not just Apple's groups
    --include-bursts         keep czkawka-only single-burst groups
    --limit N                scan only the first N groups (smoke tests)
    --skip-czkawka/--skip-exif  skip those scan steps
    --near N                 czkawka image max distance (default 10)
    --max-wal-gb F           refuse osxphotos reader above this WAL size (2.0)
    --min-plausible-year Y   older dates are implausible (default 1990)
    --date-spread-warn-days D  warn when candidates span more (default 2)
    --selftest               offline self-tests, safe anywhere, no library
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT = "dedupe_photos.py"
PLAN_VERSION = 1

DEFAULT_OUT = "dedupe-out"
DEFAULT_NEAR = 10
DEFAULT_MAX_WAL_GB = 2.0
DEFAULT_MIN_PLAUSIBLE_YEAR = 1990
# Two copies whose sizes are within this percentage of each other are treated
# as equal quality, so the older one wins instead of the one with a few more
# bytes of metadata padding. Scale matters, not absolute bytes: 1% of a 3 MB
# TIFF is 30 KB, still far below a real difference in encoded detail.
DEFAULT_SIZE_TOL_PCT = 1.0
# A better format only wins if its file is at least this percentage of the
# biggest same-resolution file. HEIC is ~2x more efficient than JPEG, so a
# smaller HEIC is normal and must still win; but a file far below even that
# is a degraded re-encode and forfeits its format advantage.
DEFAULT_FORMAT_FLOOR_PCT = 25.0
DEFAULT_SPREAD_WARN_DAYS = 2.0
DEFAULT_THUMB_PX = 768

ISO_FMT = "%Y-%m-%dT%H:%M:%S"

RAW_EXTS = {"dng", "cr2", "cr3", "crw", "nef", "nrw", "arw", "orf", "raf",
            "rw2", "pef", "srw", "x3f", "3fr", "erf", "kdc", "mrw", "raw"}
VIDEO_EXTS = {"mov", "mp4", "m4v", "avi", "mpg", "mpeg", "3gp", "mkv", "webm",
              "mts", "m2ts", "wmv", "flv"}

# keeper tiebreak only (after pixels and bytes): smaller rank wins
_FORMAT_RANKS = [(RAW_EXTS, 0), ({"heic", "heif"}, 1), ({"png", "tiff", "tif"}, 2),
                 ({"jpg", "jpeg", "gif", "bmp", "webp"}, 3), (VIDEO_EXTS, 4)]

# timestamps that are camera/importer placeholders, never real capture times
EPOCH_MARKERS = {
    datetime(1904, 1, 1, 0, 0, 0),
    datetime(1970, 1, 1, 0, 0, 0),
    datetime(1980, 1, 1, 0, 0, 0),
    datetime(2001, 1, 1, 0, 0, 0),
}

VISIBILITY_FLAGS = ("shared-with-you", "hidden", "shared-album", "trash")
TIER_ORDER = ["exact", "visual-0", "near", "video", "partial", "unverified"]

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# small pure helpers (covered by --selftest)
# --------------------------------------------------------------------------

def _norm_uuid(raw: str) -> str:
    """AppleScript ids look like "UUID/L0/001"; reduce to the plain UUID."""
    return raw.split("/")[0].upper()


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime(ISO_FMT) if dt else None


def parse_iso(text: Optional[str]) -> Optional[datetime]:
    return datetime.strptime(text, ISO_FMT) if text else None


def core_data_to_local(value, tz_offset: Optional[float] = None
                       ) -> Optional[datetime]:
    """Core Data timestamp (seconds since 2001-01-01 UTC) -> naive wall-clock
    datetime AT THE CAPTURE LOCATION, using the asset's own stored UTC offset.

    Not the machine's timezone: rendering a 2021 London photo on a laptop
    currently in Paris used to add an hour, which made Photos dates disagree
    with EXIF (a bare wall clock with no zone) by exactly that hour, and made
    apply "correct" a difference that never existed. tz_offset=None keeps the
    old machine-local behaviour for callers that have no offset to hand.

    0/None -> None (0 is Photos' own missing-date placeholder)."""
    if value is None or value == 0:
        return None
    try:
        dt = CORE_DATA_EPOCH + timedelta(seconds=float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if tz_offset is None:
        return dt.astimezone().replace(tzinfo=None)
    return (dt + timedelta(seconds=float(tz_offset))).replace(tzinfo=None)


def parse_exif_dt(text) -> Optional[datetime]:
    """Parse exiftool output into naive local time. Handles our -d format
    ("2016-08-19T09:12:44"), trailing UTC offsets ("+02:00" / "+0200" / "Z"),
    exiftool's native "2016:08:19 09:12:44", and rejects placeholders."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text or text.startswith(("-", "0000")):
        return None
    native = re.match(r"^(\d{4}):(\d{2}):(\d{2}) ", text)
    if native:
        text = f"{native.group(1)}-{native.group(2)}-{native.group(3)}T{text[11:]}"
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)  # +0200 -> +02:00
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def is_suspect_date(dt: datetime, min_year: int, now: datetime) -> bool:
    """Placeholder epochs, too-old, and future dates are implausible as the
    real capture time; they are excluded from the merged-date minimum (but
    still recorded and shown in review)."""
    if dt in EPOCH_MARKERS:
        return True
    if dt.year < min_year:
        return True
    if dt > now + timedelta(days=1):
        return True
    return False


def format_rank(ext: str) -> int:
    ext = ext.lower().lstrip(".")
    for exts, rank in _FORMAT_RANKS:
        if ext in exts:
            return rank
    return 5


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def hsize(n: Optional[int]) -> str:
    if not n:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{n}"


def oldest_plausible(m: dict, min_year: int, now: datetime) -> datetime:
    """The member's own earliest believable timestamp, or datetime.max when
    it has none (so a dateless copy never wins a date-based tiebreak)."""
    cands = [dt for _, dt in date_candidates(m)
             if not is_suspect_date(dt, min_year, now)]
    return min(cands) if cands else datetime.max


def keeper_pool(members: List[dict], size_tol_pct: float,
                format_floor_pct: float) -> Tuple[List[dict], str]:
    """Narrow to the best-quality candidates, returning them and the rung
    that last narrowed the field ("resolution", "format", "size", "" if the
    group never narrowed).

    Order matters: FORMAT is judged before size, because bytes only measure
    quality within one codec. HEIC is about twice as efficient as JPEG, so
    the same picture is roughly half the size as HEIC -- comparing the two by
    byte count picks the JPEG export over the camera's own original every
    time. Size then compares like with like."""
    pool, why = list(members), ""

    best_pix = max(_pixels(m) for m in pool)
    if any(_pixels(m) != best_pix for m in pool):
        why = "resolution"
    pool = [m for m in pool if _pixels(m) == best_pix]

    floor = max((m.get("size") or 0) for m in pool) * format_floor_pct / 100.0

    def rank(m: dict) -> int:
        # a grossly small file is a degraded re-encode, not a better format
        return (format_rank(m.get("ext") or "")
                if (m.get("size") or 0) >= floor else 99)

    best_rank = min(rank(m) for m in pool)
    if any(rank(m) != best_rank for m in pool):
        why = "format"
    pool = [m for m in pool if rank(m) == best_rank]

    threshold = max((m.get("size") or 0) for m in pool) * (1 - size_tol_pct / 100.0)
    if any((m.get("size") or 0) < threshold for m in pool):
        why = "size"
    pool = [m for m in pool if (m.get("size") or 0) >= threshold]
    return pool, why


def choose_keeper(members: List[dict],
                  min_year: int = DEFAULT_MIN_PLAUSIBLE_YEAR,
                  now: Optional[datetime] = None,
                  size_tol_pct: float = DEFAULT_SIZE_TOL_PCT,
                  format_floor_pct: float = DEFAULT_FORMAT_FLOOR_PCT) -> str:
    """Resolution first, then file size -- but size only counts when the
    difference is MATERIAL (outside size_tol_pct of the biggest file). Copies
    within that band are treated as equal quality, so the ladder moves on to
    format, then the oldest timestamp, then the earliest import, then the
    shortest filename (suffixes like "-2" mark copies and re-saves), with
    UUID as the final deterministic backstop.

    The tolerance is the point: a handful of bytes of EXIF padding on a
    multi-megabyte file says nothing about which copy is better, so it must
    not outrank an eleven-year-older capture date. Filename sits ABOVE import
    date for the same reason -- import order inside one batch is bookkeeping
    noise, while a "-2" suffix is real evidence of a copy."""
    now = now or datetime.now()
    pool, _ = keeper_pool(members, size_tol_pct, format_floor_pct)

    def key(m: dict):
        return (
            oldest_plausible(m, min_year, now),
            len(m.get("filename") or ""),
            (m.get("filename") or "").lower(),
            parse_iso(m.get("date_added")) or datetime.max,
            m["uuid"],
        )
    return min(pool, key=key)["uuid"]


def _pixels(m: dict) -> int:
    return (m.get("width") or 0) * (m.get("height") or 0)


def keeper_reason(members: List[dict], keeper: str,
                  min_year: int = DEFAULT_MIN_PLAUSIBLE_YEAR,
                  now: Optional[datetime] = None,
                  size_tol_pct: float = DEFAULT_SIZE_TOL_PCT,
                  format_floor_pct: float = DEFAULT_FORMAT_FLOOR_PCT) -> str:
    """Explain in one phrase which rule made this member the keeper.

    Mirrors choose_keeper's ladder exactly (resolution -> bytes -> format ->
    uuid) and names the rung that actually decided, so the review page can
    answer "why this one?". When every rung ties the choice really is
    arbitrary, and it says so rather than inventing a justification."""
    k = next((m for m in members if m["uuid"] == keeper), None)
    others = [m for m in members if m["uuid"] != keeper]
    if k is None or not others:
        return "only member"

    now = now or datetime.now()
    dims = f"{k.get('width') or '?'}x{k.get('height') or '?'}"
    ksize = k.get("size") or 0

    # Mirrors keeper_pool's order exactly: resolution, then format, then size.
    # Each message says "of the N tied" rather than "identical" -- claiming
    # members matched on a rung they were already eliminated on was misleading.
    pool, rung = keeper_pool(members, size_tol_pct, format_floor_pct)
    if rung == "resolution":
        return f"highest resolution ({dims})"
    if rung == "format":
        beaten = sorted({(m.get("ext") or "?") for m in members
                         if m["uuid"] != k["uuid"]
                         and format_rank(m.get("ext") or "") >
                         format_rank(k.get("ext") or "")})
        note = (" - a smaller HEIC is normal, it is ~2x more efficient"
                if (k.get("ext") or "").lower() in ("heic", "heif") else "")
        return (f"better format (.{k.get('ext') or '?'} over "
                f"{', '.join('.' + e for e in beaten) or 'the rest'}) at the "
                f"same {dims}{note}")
    if rung == "size":
        beaten = max((m.get("size") or 0) for m in members
                     if (m.get("size") or 0) < min((x.get("size") or 0)
                                                   for x in pool))
        gap = f"{(ksize - beaten) / ksize * 100:.1f}%" if ksize else "?"
        return (f"materially larger file ({hsize(ksize)} vs {hsize(beaten)}, "
                f"{gap} bigger) - same {dims} and format")

    def narrow(pool_, fn):
        best = min(fn(m) for m in pool_ + [k])
        return [m for m in pool_ if fn(m) == best], fn(k) == best

    rivals = [m for m in pool if m["uuid"] != k["uuid"]]
    if not rivals:
        return f"best quality of the group ({dims}, {hsize(ksize)})"
    n = len(rivals) + 1

    rivals, kbest = narrow(rivals, lambda m: oldest_plausible(m, min_year, now))
    if kbest and not rivals:
        return (f"oldest timestamp ({iso(oldest_plausible(k, min_year, now))}) "
                f"- of the {n} copies tied on pixels, size and format, so the "
                "original wins")

    # Suffixes like "-2" or a trailing " - 2006-09-23 18-54-00" mark copies
    # and re-saves, so the shortest name is the closest thing to the original.
    # This outranks import date: import order within one batch means nothing.
    rivals, kbest = narrow(rivals, lambda m: len(m.get("filename") or ""))
    if kbest and not rivals:
        return (f"shortest filename ({k.get('filename')}) - of the {n} "
                "otherwise identical; suffixed names like '-2' are usually "
                "copies or edits")

    rivals, kbest = narrow(
        rivals, lambda m: parse_iso(m.get("date_added")) or datetime.max)
    if kbest and not rivals:
        return (f"imported first ({k.get('date_added')}) - of the {n} tied on "
                "pixels, timestamps and filename")

    return (f"{n} copies identical on pixels, size, format, dates and name "
            "length - tie broken by name/UUID order, so this pick is "
            "arbitrary; choose by albums/keywords if it matters")


_SEQ_RE = re.compile(r"^(.*?)(\d+)(\D*)$")


def sequential_filenames(names: Sequence[str]) -> bool:
    """True when the names differ only by a running number (GRMN1639,
    GRMN1640, ...). Cameras number consecutive captures that way, so a
    "duplicate" group of them is far more likely to be a SERIES than copies
    -- true copies repeat a name or add a "-2" suffix."""
    if len(names) < 3:
        return False
    stems, nums = set(), []
    for name in names:
        m = _SEQ_RE.match(os.path.splitext(name)[0])
        if not m:
            return False
        stems.add((m.group(1).lower(), m.group(3).lower()))
        nums.append(int(m.group(2)))
    return len(stems) == 1 and len(set(nums)) == len(nums)


def mixed_orientation(members: List[dict]) -> bool:
    """True when the group mixes portrait and landscape. A duplicate keeps
    its shape -- a crop or re-encode changes the ratio, not the orientation --
    so this usually means the matcher pulled in something unrelated."""
    seen = set()
    for m in members:
        w, h = m.get("width") or 0, m.get("height") or 0
        if not w or not h:
            return False
        seen.add("portrait" if h > w else "landscape" if w > h else "square")
    return len(seen - {"square"}) > 1


def distinct_capture_times(members: List[dict], min_year: int,
                           now: datetime) -> bool:
    """True when every member has its own capture timestamp, spread over more
    than a couple of seconds. Genuine duplicates share a capture time (it is
    the same moment); a set of consecutive recordings does not.

    Needs THREE or more members: a pair with differing timestamps is the
    everyday re-import, which this tool exists to fix silently, not warn
    about. Three copies each stamped a different moment is a series."""
    times = [oldest_plausible(m, min_year, now) for m in members]
    if len(times) < 3 or any(t == datetime.max for t in times):
        return False
    return (len(set(times)) == len(times)
            and (max(times) - min(times)).total_seconds() > 2)


def date_candidates(member: dict) -> List[Tuple[str, datetime]]:
    """(label, naive local datetime) for every timestamp this member offers,
    deterministic order."""
    out: List[Tuple[str, datetime]] = []
    photos = parse_iso(member.get("photos_date"))
    if photos:
        out.append(("photos", photos))
    exif = member.get("exif") or {}
    for label, key in (("exif-dto", "DateTimeOriginal"),
                       ("exif-create", "CreateDate"),
                       ("exif-creation", "CreationDate")):
        dt = parse_exif_dt(exif.get(key))
        if dt:
            out.append((label, dt))
    return out


def merged_date(members: List[dict], min_year: int, now: datetime,
                spread_warn_days: float) -> Tuple[Optional[str], Optional[str], float, List[str]]:
    """The oldest plausible timestamp across the whole tranche.

    Returns (iso date, source label, spread in days, warnings). If every
    candidate is implausible, falls back to the oldest Photos date and says
    so.

    The spread (and its warning) measures disagreement among EXIF/QuickTime
    candidates ONLY. Duplicates re-imported on different days routinely carry
    different Photos dates -- fixing that silently is this tool's job, not a
    reason for review scrutiny. Two capture-metadata candidates disagreeing,
    however, means something recorded the moment wrong: that deserves eyes."""
    pool: List[Tuple[str, str, datetime]] = []  # (label, filename, dt)
    all_cands: List[Tuple[str, str, datetime]] = []
    for m in members:
        for label, dt in date_candidates(m):
            all_cands.append((label, m.get("filename") or m["uuid"], dt))
            if not is_suspect_date(dt, min_year, now):
                pool.append((label, m.get("filename") or m["uuid"], dt))

    warnings: List[str] = []
    if not all_cands:
        return None, None, 0.0, ["no-dates"]
    if not pool:
        warnings.append("all-dates-suspect")
        photos_only = [c for c in all_cands if c[0] == "photos"] or all_cands
        pool = photos_only

    label, fname, dt = min(pool, key=lambda c: (c[2], c[0], c[1]))
    exif_pool = [c for c in pool if c[0] != "photos"]
    spread = 0.0
    if len(exif_pool) >= 2:
        spread = round((max(c[2] for c in exif_pool)
                        - min(c[2] for c in exif_pool)).total_seconds() / 86400, 1)
    if spread > spread_warn_days:
        # flat label on purpose: the magnitude lives in date_spread_days, and
        # baking it into the name gave one summary bucket per distinct value
        warnings.append("date-spread")
    return iso(dt), f"{label}:{fname}", spread, warnings


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:  # deterministic root choice
                ra, rb = rb, ra
            self.parent[rb] = ra


_SOURCE_NAMES = {"P": "apple-perceptual", "M": "apple-metadata", "C": "czkawka"}


def build_apple_tranches(rows: List[Tuple[str, Optional[int], Optional[int]]],
                         czkawka_groups: Optional[List[Set[str]]] = None
                         ) -> List[dict]:
    """rows = (uuid, perceptual_group_id, metadata_group_id) from Apple's
    analysis; czkawka_groups = discovered groups from a full-library czkawka
    sweep. All of them feed ONE union-find, so an Apple pair plus a czkawka
    group containing a third copy fuse into a single 3-member tranche, and
    czkawka links between two Apple groups merge them. Group-id namespaces
    are kept apart with a prefix (P/M/C), which is also where each tranche's
    sources come from. Singletons drop out."""
    uf = UnionFind()
    group_members: Dict[str, List[str]] = {}
    for uuid, pgrp, mgrp in rows:
        for prefix, grp in (("P", pgrp), ("M", mgrp)):
            if grp is not None:
                group_members.setdefault(f"{prefix}{grp}", []).append(uuid)
    for i, grp in enumerate(czkawka_groups or []):
        if len(grp) >= 2:
            group_members[f"C{i}"] = sorted(grp)
    for gid, uuids in group_members.items():
        for other in uuids[1:]:
            uf.union(uuids[0], other)

    clusters: Dict[str, Set[str]] = {}
    uuid_groups: Dict[str, Set[str]] = {}
    for gid, uuids in group_members.items():
        for u in uuids:
            clusters.setdefault(uf.find(u), set()).add(u)
            uuid_groups.setdefault(u, set()).add(gid)

    tranches = []
    for root in sorted(clusters):
        members = sorted(clusters[root])
        if len(members) < 2:
            continue
        gids = sorted(set().union(*(uuid_groups[u] for u in members)))
        sources = sorted({_SOURCE_NAMES[g[0]] for g in gids})
        tranches.append({
            "key": tranche_key(members),
            "members": members,
            "apple_groups": gids,
            "sources": sources,
        })
    tranches.sort(key=lambda t: t["key"])
    return tranches


def tranche_key(uuids: Iterable[str]) -> str:
    """Stable id for a set of assets, independent of discovery order."""
    joined = "\n".join(sorted(u.upper() for u in uuids))
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def drop_burst_only_tranches(tranches: List[dict], members: Dict[str, dict]
                             ) -> Tuple[List[dict], int]:
    """A czkawka-only group whose members all belong to one burst is burst
    siblings, not duplicates -- at loose perceptual distance czkawka groups
    them enthusiastically. Groups Apple also flagged are kept regardless
    (Apple's analysis distinguishes bursts from re-imports)."""
    kept, dropped = [], 0
    for t in tranches:
        if t["sources"] == ["czkawka"]:
            keys = {(members.get(u) or {}).get("burst_key") for u in t["members"]}
            if len(keys) == 1 and next(iter(keys)):
                dropped += 1
                continue
        kept.append(t)
    return kept, dropped


CZKAWKA_TOOLS = ("dup", "image", "video")


def parse_tools(value: str) -> List[str]:
    """--czkawka-tools "dup,image" -> ["dup", "image"], in canonical order."""
    names = [t.strip().lower() for t in (value or "").split(",") if t.strip()]
    bad = [t for t in names if t not in CZKAWKA_TOOLS]
    if bad:
        raise SystemExit(f"unknown czkawka tool(s) {bad}; choose from "
                         f"{', '.join(CZKAWKA_TOOLS)}")
    return [t for t in CZKAWKA_TOOLS if t in names]


def czkawka_group_sets(out_dir: Path) -> List[Set[str]]:
    """Every czkawka group (dup + image + video) as a set of asset uuids,
    for feeding the tranche union-find in discovery mode."""
    sets: List[Set[str]] = []
    for name in ("czkawka_dup.json", "czkawka_image.json", "czkawka_video.json"):
        for group in load_czkawka(out_dir, name):
            sets.append(set(group))
    return sets


def farm_name(uuid: str, path: str) -> str:
    return f"{uuid}__{os.path.basename(path)}"


def farm_uuid(name: str) -> str:
    return _norm_uuid(os.path.basename(name).split("__", 1)[0])


def iter_czkawka_groups(data) -> List[List[dict]]:
    """Normalize czkawka JSON: `image`/`video` emit a list of groups;
    `dup` emits {size: [group, ...]}. A group is a list of entries with a
    "path" key."""
    groups: List[List[dict]] = []
    if isinstance(data, dict):
        for value in data.values():
            groups.extend(iter_czkawka_groups(value))
        return groups
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "path" in data[0]:
            return [data]
        for item in data:
            groups.extend(iter_czkawka_groups(item))
    return groups


def czkawka_uuid_groups(data) -> List[Dict[str, dict]]:
    """czkawka JSON -> list of {uuid: entry} per group (farm filenames carry
    the uuid). Entries whose name does not look like a farm entry are
    dropped."""
    out = []
    for group in iter_czkawka_groups(data):
        m: Dict[str, dict] = {}
        for entry in group:
            name = os.path.basename(entry.get("path", ""))
            if "__" in name:
                m[farm_uuid(name)] = entry
        if len(m) >= 2:
            out.append(m)
    return out


def tranche_tier(member_uuids: List[str], members: Dict[str, dict],
                 dup_groups: List[Dict[str, dict]],
                 image_groups: List[Dict[str, dict]],
                 video_groups: List[Dict[str, dict]]) -> Tuple[str, Optional[int]]:
    """Verification tier for one tranche (see module docstring), plus the
    max perceptual distance seen between members (None when not applicable)."""
    mset = set(member_uuids)

    for g in dup_groups:
        if mset <= set(g):
            return "exact", None

    covered: Set[str] = set()
    max_diff: Optional[int] = None
    dims_equal = True
    for g in image_groups:
        overlap = mset & set(g)
        if len(overlap) >= 2:
            covered |= overlap
            for u in overlap:
                d = g[u].get("difference")
                if isinstance(d, int):
                    max_diff = d if max_diff is None else max(max_diff, d)
    for g in video_groups:
        overlap = mset & set(g)
        if len(overlap) >= 2:
            covered |= overlap
    # byte-identical subsets still count as covered members
    for g in dup_groups:
        overlap = mset & set(g)
        if len(overlap) >= 2:
            covered |= overlap

    if covered >= mset:
        dims = {(members[u].get("width"), members[u].get("height")) for u in mset}
        dims_equal = len(dims) == 1
        if max_diff == 0 and dims_equal:
            return "visual-0", 0
        if max_diff is not None:
            return "near", max_diff
        return "video", None
    if covered:
        return "partial", max_diff
    return "unverified", None


def wal_refusal(wal_bytes: int, max_gb: float) -> Optional[str]:
    """Reason to refuse the osxphotos reader, or None when safe. osxphotos
    copies Photos.sqlite AND the -wal to a temp dir on load; with a runaway
    WAL that is a huge, slow copy."""
    limit = int(max_gb * 1024**3)
    if wal_bytes <= limit:
        return None
    return (
        f"Photos.sqlite-wal is {wal_bytes / 1024**3:.1f} GB (limit {max_gb:g} GB). "
        "The osxphotos reader would copy all of it to a temp dir. Quit Photos "
        "(and Messages, which also holds the database open) so macOS can "
        "checkpoint -- reopening Photos once after quitting everything, or a "
        "reboot, does it -- then rerun. Or rerun with --reader sqlite (fewer "
        "fields, but reads the live database without copying), or raise "
        "--max-wal-gb if you really want the copy."
    )


def member_public(m: dict) -> dict:
    """Member as stored in scan.json / shown to review (drop absolute paths
    from the report data; keep them in scan.json for later stages)."""
    return m


def suggest_approve(tier: str, warnings: List[str]) -> bool:
    return tier in ("exact", "visual-0") and not warnings


# --------------------------------------------------------------------------
# scan: Photos.sqlite groups + member metadata + exiftool + czkawka
# --------------------------------------------------------------------------

def default_library_path() -> Path:
    try:
        from osxphotos.utils import get_last_library_path
        p = get_last_library_path()
        if p:
            return Path(p)
    except Exception:
        pass
    return Path.home() / "Pictures" / "Photos Library.photoslibrary"


def sqlite_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{urllib.parse.quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def read_apple_groups(conn: sqlite3.Connection
                      ) -> List[Tuple[str, Optional[int], Optional[int]]]:
    cols = table_columns(conn, "ZASSET")
    need = {"ZUUID", "ZTRASHEDSTATE",
            "ZDUPLICATEPERCEPTUALMATCHINGALBUM", "ZDUPLICATEMETADATAMATCHINGALBUM"}
    missing = need - cols
    if missing:
        raise SystemExit(
            f"Photos.sqlite schema is missing {sorted(missing)} -- this macOS "
            "version stores duplicate groups differently; scan cannot proceed."
        )
    rows = conn.execute(
        "SELECT ZUUID, ZDUPLICATEPERCEPTUALMATCHINGALBUM AS P,"
        " ZDUPLICATEMETADATAMATCHINGALBUM AS M"
        " FROM ZASSET WHERE ZTRASHEDSTATE = 0"
        " AND (ZDUPLICATEPERCEPTUALMATCHINGALBUM IS NOT NULL"
        "  OR ZDUPLICATEMETADATAMATCHINGALBUM IS NOT NULL)"
        " ORDER BY Z_PK"
    ).fetchall()
    return [(_norm_uuid(r["ZUUID"]), r["P"], r["M"]) for r in rows]


def library_generation(conn: sqlite3.Connection, wal_bytes: int) -> dict:
    row = conn.execute("SELECT COUNT(*) AS n, MAX(Z_PK) AS mx FROM ZASSET").fetchone()
    return {"asset_count": row["n"], "max_pk": row["mx"], "wal_bytes": wal_bytes}


def read_members_sqlite(conn: sqlite3.Connection, library: Path,
                        uuids: Optional[Set[str]], verbose: Callable
                        ) -> Dict[str, dict]:
    """Member metadata straight from ZASSET (live WAL read, nothing copied).
    uuids=None indexes every asset (discovery mode). Optional columns are
    guarded so schema drift degrades instead of breaking."""
    cols = table_columns(conn, "ZASSET")
    aaa_cols = table_columns(conn, "ZADDITIONALASSETATTRIBUTES")

    def sel(name: str, alias: str) -> str:
        return f"a.{name} AS {alias}" if name in cols else f"NULL AS {alias}"

    parts = [
        "a.ZUUID AS uuid", sel("ZDIRECTORY", "dir"), sel("ZFILENAME", "fname"),
        sel("ZDATECREATED", "created"), sel("ZADDEDDATE", "added"),
        sel("ZWIDTH", "width"), sel("ZHEIGHT", "height"),
        sel("ZFAVORITE", "favorite"), sel("ZHIDDEN", "hidden"),
        sel("ZTRASHEDSTATE", "trashed"), sel("ZKIND", "kind"),
        sel("ZKINDSUBTYPE", "kindsubtype"), sel("ZAVALANCHEUUID", "burst_key"),
    ]
    join = ""
    if "ZASSET" in aaa_cols:
        join = "LEFT JOIN ZADDITIONALASSETATTRIBUTES aaa ON aaa.ZASSET = a.Z_PK"
        parts.append("aaa.ZORIGINALFILENAME AS original_filename"
                     if "ZORIGINALFILENAME" in aaa_cols
                     else "NULL AS original_filename")
        # the capture-location UTC offset, so dates read as wall-clock there
        parts.append("aaa.ZTIMEZONEOFFSET AS tz_offset"
                     if "ZTIMEZONEOFFSET" in aaa_cols else "NULL AS tz_offset")
    else:
        parts.append("NULL AS original_filename")
        parts.append("NULL AS tz_offset")

    members: Dict[str, dict] = {}
    rows = conn.execute(f"SELECT {', '.join(parts)} FROM ZASSET a {join}").fetchall()
    for r in rows:
        uuid = _norm_uuid(r["uuid"])
        if uuids is not None and uuid not in uuids:
            continue
        # ZDIRECTORY is the shard char ("5") for plain assets, but a full
        # library-relative path ("scopes/syndication/...") for shared ones
        path = None
        if r["dir"] is not None and r["fname"]:
            for candidate in (library / "originals" / str(r["dir"]) / r["fname"],
                              library / str(r["dir"]) / r["fname"]):
                if candidate.exists():
                    path = str(candidate)
                    break
        ext = os.path.splitext(r["fname"] or "")[1].lstrip(".").lower()
        flags = []
        if r["hidden"]:
            flags.append("hidden")
        if r["trashed"]:
            flags.append("trash")
        if r["kind"] == 1 or ext in VIDEO_EXTS:
            flags.append("video")
        if r["kindsubtype"] == 2:
            flags.append("live")
        if path is None:
            flags.append("missing")
        members[uuid] = {
            "uuid": uuid,
            "filename": r["original_filename"] or r["fname"] or uuid,
            "ext": ext,
            "path": path,
            "thumb_source": path,
            "size": os.path.getsize(path) if path else None,
            "width": r["width"],
            "height": r["height"],
            "photos_date": iso(core_data_to_local(r["created"], r["tz_offset"])),
            "date_added": iso(core_data_to_local(r["added"])),
            "tz_offset": r["tz_offset"],
            "favorite": bool(r["favorite"]),
            "flags": flags,
            "burst_key": r["burst_key"],
            "albums": [],
            "keywords": [],
            "title": None,
            "description": None,
            "reader": "sqlite",
        }
    if uuids is not None:
        for uuid in sorted(uuids - set(members)):
            verbose(f"  warning: {uuid} in a duplicate group but not readable; skipping")
    return members


def _photoinfo_lite(p) -> dict:
    """The cheap scalar fields of one PhotoInfo -- what the library-wide
    index holds. Albums/keywords/derivatives are enriched later, only for
    assets that end up in tranches."""
    # osxphotos returns p.date tz-aware IN THE PHOTO'S OWN timezone, so drop
    # the tzinfo to get capture-local wall clock. Converting to the machine's
    # zone (.astimezone()) would shift every photo taken elsewhere.
    d = p.date
    if d is not None and d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    added = p.date_added
    if added is not None and added.tzinfo is not None:
        added = added.astimezone().replace(tzinfo=None)
    path = p.path
    ext = os.path.splitext(p.original_filename or p.filename or "")[1].lstrip(".").lower()
    if not ext and path:
        ext = os.path.splitext(path)[1].lstrip(".").lower()
    flags = []
    if getattr(p, "syndicated", None) and not getattr(p, "saved_to_library", True):
        flags.append("shared-with-you")
    if p.hidden:
        flags.append("hidden")
    if getattr(p, "shared", False):
        flags.append("shared-album")
    if getattr(p, "intrash", False):
        flags.append("trash")
    if getattr(p, "ismissing", False) or not path:
        flags.append("missing")
    if getattr(p, "ismovie", False) or ext in VIDEO_EXTS:
        flags.append("video")
    if getattr(p, "live_photo", False):
        flags.append("live")
    if getattr(p, "has_raw", False):
        flags.append("raw")
    if getattr(p, "burst", False):
        flags.append("burst")
    return {
        "uuid": _norm_uuid(p.uuid),
        "filename": p.original_filename or p.filename or p.uuid,
        "ext": ext,
        "path": path,
        "thumb_source": path,
        "size": getattr(p, "original_filesize", None),
        "width": p.width,
        "height": p.height,
        "photos_date": iso(d),
        "date_added": iso(added),
        "tz_offset": getattr(p, "tzoffset", None),
        "favorite": bool(p.favorite),
        "flags": flags,
        "burst_key": getattr(p, "burst_key", None),
        "album_uuids": [],
        "albums": [],
        "keywords": [],
        "title": None,
        "description": None,
        "reader": "osxphotos",
    }


def read_all_osxphotos(library: Optional[Path], verbose: Callable
                       ) -> Tuple[object, Dict[str, dict], Path]:
    """Index every (non-trash) asset via osxphotos: one pass over
    db.photos(), scalar fields only. Returns the loaded db too, so tranche
    members can be enriched from it without a second library load."""
    import osxphotos

    verbose("loading the Photos library via osxphotos (may take a while)...")
    db = osxphotos.PhotosDB(dbfile=str(library)) if library else osxphotos.PhotosDB()
    lib_path = Path(db.library_path)
    verbose(f"loaded {lib_path}")
    members: Dict[str, dict] = {}
    for p in db.photos():
        m = _photoinfo_lite(p)
        members[m["uuid"]] = m
    verbose(f"indexed {len(members):,} assets")
    return db, members, lib_path


def enrich_members_osxphotos(db, members: Dict[str, dict], uuids: Set[str],
                             verbose: Callable) -> None:
    """Fill in the expensive fields (albums, album uuids, keywords, titles,
    derivative thumbnails, exact byte size) for tranche members only."""
    verbose(f"enriching {len(uuids):,} tranche member(s) with albums/keywords...")
    for uuid in sorted(uuids):
        p = db.get_photo(uuid)
        m = members.get(uuid)
        if p is None or m is None:
            continue
        m["albums"] = sorted(set(p.albums or []))
        m["album_uuids"] = sorted(
            [[a.uuid, a.title] for a in (getattr(p, "album_info", None) or [])
             if getattr(a, "uuid", None)])
        m["keywords"] = sorted(set(p.keywords or []))
        m["title"] = p.title
        m["description"] = p.description
        derivatives = [d for d in (getattr(p, "path_derivatives", None) or [])
                       if str(d).lower().endswith((".jpg", ".jpeg"))]
        if derivatives:
            m["thumb_source"] = derivatives[0]
        if m.get("path") and os.path.exists(m["path"]):
            m["size"] = os.path.getsize(m["path"])


def run_exiftool(members: Dict[str, dict], out_dir: Path, verbose: Callable) -> dict:
    """One batched exiftool run over every locally-present original.
    QuickTimeUTC=1 renders QuickTime dates in local time like everything else."""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        verbose("exiftool not found on PATH; skipping EXIF date candidates")
        return {}
    paths = {m["path"]: u for u, m in members.items() if m.get("path")}
    if not paths:
        return {}
    listfile = out_dir / "exiftool_files.txt"
    listfile.write_text("\n".join(sorted(paths)) + "\n")
    verbose(f"exiftool: reading dates from {len(paths):,} original(s)...")
    proc = subprocess.run(
        [exiftool, "-j", "-f", "-fast2", "-api", "QuickTimeUTC=1",
         "-d", "%Y-%m-%dT%H:%M:%S",
         "-DateTimeOriginal", "-CreateDate", "-CreationDate",
         "-@", str(listfile)],
        capture_output=True, text=True)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        # exit 1 just means some files had no tags; anything else is real
        verbose(f"exiftool failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
        return {}
    result = {}
    for rec in json.loads(proc.stdout):
        uuid = paths.get(rec.get("SourceFile"))
        if uuid:
            result[uuid] = {k: rec.get(k) for k in
                            ("DateTimeOriginal", "CreateDate", "CreationDate")}
    return result


def farm_shard(uuid: str) -> str:
    """Shard subdirectory for one asset: the first two hex chars of its
    uuid. Stable per asset, so batch membership and czkawka's path-keyed
    hash cache survive rescans."""
    return _norm_uuid(uuid)[:2]


def build_farm(members: Dict[str, dict], farm_dir: Path, verbose: Callable) -> int:
    """Hardlink every locally-present original into farm_dir as
    <shard>/<uuid>__<basename>. Rebuild is incremental; stale entries
    (including any from the old flat layout) are removed. Nothing inside
    the library is written or moved."""
    farm_dir.mkdir(parents=True, exist_ok=True)
    wanted: Dict[str, str] = {}
    for uuid, m in sorted(members.items()):
        if m.get("path"):
            wanted[f"{farm_shard(uuid)}/{farm_name(uuid, m['path'])}"] = m["path"]
    for existing in farm_dir.rglob("*"):
        if existing.is_file() and str(existing.relative_to(farm_dir)) not in wanted:
            existing.unlink()
    linked = 0
    for rel, src in wanted.items():
        dst = farm_dir / rel
        if not dst.exists():
            dst.parent.mkdir(exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)  # cross-device fallback; src untouched
        linked += 1
    verbose(f"farm: {linked:,} original(s) hardlinked in {farm_dir}")
    return linked


def chunk_shards(counts: List[Tuple[object, int]], batch_size: int
                 ) -> List[List[Tuple[object, int]]]:
    """Group (shard, file_count) pairs into batches of >= batch_size files
    (last batch takes the remainder)."""
    batches: List[List[Tuple[object, int]]] = []
    cur: List[Tuple[object, int]] = []
    cur_n = 0
    for item in counts:
        cur.append(item)
        cur_n += item[1]
        if cur_n >= batch_size:
            batches.append(cur)
            cur, cur_n = [], 0
    if cur:
        batches.append(cur)
    return batches


def run_czkawka(farm_dir: Path, out_dir: Path, near: int, verbose: Callable,
                threads: int = 0, tools: Sequence[str] = ("dup", "image", "video"),
                dirs: Optional[Sequence[Path]] = None,
                json_prefix: str = "czkawka") -> None:
    """Run the selected czkawka tools over dirs (default: the whole farm).
    czkawka caches every hash it computes (keyed by path), so partial runs
    -- the batched warm-up passes -- make the final full pass cheap."""
    czkawka = shutil.which("czkawka_cli")
    if not czkawka:
        raise SystemExit("czkawka_cli not found on PATH (brew install czkawka)")
    dir_args: List[str] = []
    for d in (dirs or [farm_dir]):
        dir_args += ["-d", str(d)]
    thread_args = ["-T", str(threads)] if threads > 0 else []
    runs = {
        "dup": [czkawka, "dup", *dir_args, "-m", "1024", "-s", "HASH"],
        "image": [czkawka, "image", *dir_args, "-m", "1024", "-s", str(near)],
        "video": [czkawka, "video", *dir_args, "-m", "1024"],
    }
    # czkawka renders a live progress bar (indicatif) on stderr. Capturing
    # stderr hides it, so let it through whenever we are on a terminal --
    # long passes (video especially) are otherwise completely silent.
    show_progress = sys.stderr.isatty()
    for label in tools:
        json_path = out_dir / f"{json_prefix}_{label}.json"
        started = datetime.now()
        verbose(f"czkawka {label}: scanning... ({started:%H:%M:%S})")
        # NOT subprocess.run(): it SIGKILLs the child on KeyboardInterrupt,
        # which would kill czkawka in the milliseconds before it writes its
        # hash cache -- throwing away hours of hashing. Ctrl+C already
        # reaches czkawka directly (same process group) and it stops
        # gracefully, so we wait for it instead of killing it.
        proc = subprocess.Popen(
            runs[label] + thread_args + ["-p", str(json_path), "-N", "-M", "-W"],
            stdout=subprocess.PIPE,
            stderr=(None if show_progress else subprocess.PIPE),
            text=True)
        try:
            out, err = proc.communicate()
        except KeyboardInterrupt:
            verbose(f"\nczkawka {label}: stopping -- waiting for it to write "
                    "its hash cache (do NOT kill it; a rerun then resumes "
                    "from there)...")
            try:
                proc.communicate(timeout=900)
                verbose("  czkawka exited cleanly; computed hashes are cached.")
            except subprocess.TimeoutExpired:
                verbose("  still saving after 15 min -- leaving it running; "
                        "check the cache file's timestamp before rerunning.")
            raise SystemExit(130)
        elapsed = datetime.now() - started
        if proc.returncode != 0:
            tail = (err or out or "").strip()[-400:]
            verbose(f"  czkawka {label} failed (exit {proc.returncode}): {tail}")
            verbose(f"  continuing; tranches will show as unverified for {label}")
            json_path.write_text("[]")
        else:
            # a graceful mid-run stop exits 0 without writing results
            raw = json_path.read_text() if json_path.exists() else ""
            if not raw.strip():
                verbose(f"  czkawka {label}: no results written (stopped early?)")
                json_path.write_text("[]")
                raw = "[]"
            groups = czkawka_uuid_groups(json.loads(raw))
            verbose(f"  czkawka {label}: {len(groups):,} group(s) "
                    f"in {_hms(elapsed.total_seconds())}")


def cmd_scan(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    verbose = print

    library = Path(args.library) if args.library else default_library_path()
    db_path = library / "database" / "Photos.sqlite"
    if not db_path.exists():
        raise SystemExit(f"no Photos.sqlite under {library}")
    wal = db_path.with_name(db_path.name + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0

    reader = args.reader
    if reader in ("auto", "osxphotos"):
        reason = wal_refusal(wal_bytes, args.max_wal_gb)
        if reason:
            raise SystemExit(f"refusing --reader osxphotos: {reason}")
        reader = "osxphotos"

    tools = parse_tools(args.czkawka_tools)
    verbose(f"Library: {library}")
    farm_dir = Path(args.farm_dir or (out_dir / "farm"))
    db = None
    conn = sqlite_ro(db_path)
    try:
        rows = read_apple_groups(conn)
        generation = library_generation(conn, wal_bytes)
        verbose(f"Apple duplicate analysis: {len(rows):,} asset(s) "
                f"(library: {generation['asset_count']:,} assets)")
        if reader == "sqlite":
            members = read_members_sqlite(conn, library, None, verbose)
        else:
            db, members, library = read_all_osxphotos(
                Path(args.library) if args.library else None, verbose)
    finally:
        conn.close()

    total_local = sum(1 for m in members.values() if m.get("path"))

    # Discovery: czkawka sweeps EVERY local, mergeable original, and its
    # groups join Apple's in the union-find below -- extra copies Apple
    # missed attach to their tranches, czkawka-only tranches appear.
    cz_group_sets: Optional[List[Set[str]]] = None
    if args.discover and not args.skip_czkawka:
        farm_uuids = {u for u, m in members.items()
                      if m.get("path")
                      and not any(f in VISIBILITY_FLAGS for f in m["flags"])}
        verbose(f"discovery: {len(farm_uuids):,} local mergeable original(s) "
                f"to sweep ({len(members) - total_local:,} assets have no "
                "local original and are invisible to discovery)")
        build_farm({u: members[u] for u in farm_uuids}, farm_dir, verbose)

        # batched, pausable hashing: warm czkawka's hash cache shard-group
        # by shard-group, waiting for RETURN between batches, then let the
        # final full pass below group everything from the cache
        if args.batch_size > 0:
            if not sys.stdin.isatty():
                verbose("--batch-size needs an interactive terminal; running "
                        "the sweep in one pass instead")
            else:
                shard_dirs = sorted(d for d in farm_dir.iterdir() if d.is_dir())
                counts = [(d, sum(1 for f in d.iterdir() if f.is_file()))
                          for d in shard_dirs]
                batches = chunk_shards(counts, args.batch_size)
                verbose(f"batched hashing: {len(batches)} batch(es); every "
                        "hash is cached, so quitting and rerunning later "
                        "resumes where you left off")
                pause = True
                for i, batch in enumerate(batches, start=1):
                    n = sum(c for _, c in batch)
                    if pause:
                        answer = input(
                            f"Batch {i}/{len(batches)} (~{n:,} files) -- "
                            "RETURN to run, a=run all remaining, "
                            "q=quit (resume later): ").strip().lower()
                        if answer == "q":
                            verbose("stopped; rerun scan --discover to resume "
                                    "(already-hashed batches are nearly free)")
                            return 0
                        if answer == "a":
                            pause = False
                    run_czkawka(farm_dir, out_dir, args.near, verbose,
                                threads=args.threads,
                                tools=[t for t in tools if t != "dup"],
                                dirs=[d for d, _ in batch],
                                json_prefix="czkawka_warm")

        run_czkawka(farm_dir, out_dir, args.near, verbose, threads=args.threads,
                    tools=tools)
        cz_group_sets = czkawka_group_sets(out_dir)
        verbose(f"czkawka: {len(cz_group_sets):,} group(s) across the sweep")

    tranches = build_apple_tranches(rows, cz_group_sets)
    verbose(f"{len(tranches):,} combined tranche(s) before filtering")

    dropped_bursts = 0
    if args.discover and not args.include_bursts:
        tranches, dropped_bursts = drop_burst_only_tranches(tranches, members)
        if dropped_bursts:
            verbose(f"dropped {dropped_bursts:,} czkawka-only burst-sibling "
                    "group(s) (--include-bursts keeps them)")

    if args.limit:
        tranches = tranches[:args.limit]
        verbose(f"--limit {args.limit}: keeping the first {len(tranches)} group(s)")

    # drop members that could not be read at all, and groups that collapse
    tranches = [dict(t, members=[u for u in t["members"] if u in members])
                for t in tranches]
    tranches = [t for t in tranches if len(t["members"]) >= 2]
    uuids = {u for t in tranches for u in t["members"]}

    if db is not None:
        enrich_members_osxphotos(db, members, uuids, verbose)

    scan_members = {u: members[u] for u in sorted(uuids)}
    exif = {} if args.skip_exif else run_exiftool(scan_members, out_dir, verbose)
    for uuid, tags in exif.items():
        scan_members[uuid]["exif"] = tags

    # apple-only mode: czkawka runs as a verifier over just the tranche
    # members (discovery mode already scanned them all above)
    if not args.discover and not args.skip_czkawka:
        build_farm({u: scan_members[u] for u in scan_members
                    if scan_members[u].get("path")}, farm_dir, verbose)
        run_czkawka(farm_dir, out_dir, args.near, verbose, threads=args.threads,
                    tools=tools)

    local = sum(1 for m in scan_members.values() if m.get("path"))
    scan = {
        "version": PLAN_VERSION,
        "script": SCRIPT,
        "generated": datetime.now().strftime(ISO_FMT),
        "library": str(library),
        "reader": reader,
        "discover": bool(args.discover),
        "dropped_burst_only": dropped_bursts,
        "generation": generation,
        "near": args.near,
        "apple_tranches": tranches,
        "members": scan_members,
    }
    (out_dir / "scan.json").write_text(json.dumps(scan, indent=1, sort_keys=True))
    verbose(f"\nWrote {out_dir / 'scan.json'}: {len(tranches):,} tranche(s), "
            f"{len(scan_members):,} member(s), {local:,} with local originals"
            + ("" if local == len(scan_members) else
               f" ({len(scan_members) - local:,} not downloaded -- czkawka "
               "cannot verify those; consider 'Download Originals to this Mac')"))
    verbose(f"Next: osxphotos run {SCRIPT} plan --out {out_dir}")
    return 0


# --------------------------------------------------------------------------
# plan: pure computation from scan.json (+ czkawka JSON)
# --------------------------------------------------------------------------

def load_czkawka(out_dir: Path, name: str) -> List[Dict[str, dict]]:
    path = out_dir / name
    if not path.exists():
        return []
    try:
        return czkawka_uuid_groups(json.loads(path.read_text() or "[]"))
    except (json.JSONDecodeError, OSError):
        return []


def build_plan(scan: dict, dup_groups, image_groups, video_groups,
               min_year: int, spread_warn_days: float,
               now: Optional[datetime] = None,
               size_tol_pct: float = DEFAULT_SIZE_TOL_PCT) -> dict:
    """Deterministic: no timestamps, stable ordering, so identical inputs
    produce byte-identical plan.json."""
    now = now or datetime.now()
    members: Dict[str, dict] = scan["members"]

    tranches = []
    for t in scan["apple_tranches"]:
        ms = [members[u] for u in t["members"]]
        tier, max_diff = tranche_tier(t["members"], members,
                                      dup_groups, image_groups, video_groups)
        m_date, source, spread, warnings = merged_date(
            ms, min_year, now, spread_warn_days)
        keeper = choose_keeper(ms, min_year, now, size_tol_pct)
        for m in ms:
            for flag in m["flags"]:
                if flag in VISIBILITY_FLAGS:
                    warnings.append(f"unmergeable-member-{flag}")
        if any("missing" in m["flags"] for m in ms):
            warnings.append("missing-original")
        kinds = {"video" if "video" in m["flags"] else "photo" for m in ms}
        if len(kinds) > 1:
            warnings.append("mixed-media")
        # A group whose members are consecutively numbered AND each carry
        # their own capture time is almost certainly a series of separate
        # shots -- a static camera makes their frames match, but they are not
        # duplicates. Loudly flagged, and never auto-suggested.
        # ...unless the files are byte-identical, where "different captures"
        # is impossible by definition and the differing dates are just
        # re-import bookkeeping.
        if tier != "exact":
            if sequential_filenames([m.get("filename") or "" for m in ms]):
                warnings.append("sequential-filenames")
            if distinct_capture_times(ms, min_year, now):
                warnings.append("distinct-capture-times")
            if mixed_orientation(ms):
                warnings.append("mixed-orientation")
        burst_keys = [m.get("burst_key") for m in ms if m.get("burst_key")]
        if len(burst_keys) >= 2 and len(set(burst_keys)) < len(ms):
            warnings.append("burst-mates")
        if tier in ("unverified", "partial"):
            warnings.append(f"czkawka-{tier}")
        warnings = sorted(set(warnings))
        tranches.append({
            "key": t["key"],
            "sources": t["sources"],
            "apple_groups": t["apple_groups"],
            "members": t["members"],
            "tier": tier,
            "czkawka_max_diff": max_diff,
            "keeper": keeper,
            "keeper_reason": keeper_reason(ms, keeper, min_year, now,
                                           size_tol_pct),
            "merged_date": m_date,
            "date_source": source,
            "date_spread_days": spread,
            "warnings": warnings,
            "suggested": suggest_approve(tier, warnings),
        })

    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}
    tranches.sort(key=lambda t: (tier_rank.get(t["tier"], 99), t["key"]))
    for i, t in enumerate(tranches, start=1):
        t["id"] = i

    # czkawka links that cross Apple's group boundaries: a preview of what a
    # full-library scan will add (czkawka relates assets Apple kept apart)
    uuid_tranche = {u: t["key"] for t in tranches for u in t["members"]}
    cross: Set[Tuple[str, str]] = set()
    for groups in (dup_groups, image_groups, video_groups):
        for g in groups:
            keys = sorted({uuid_tranche.get(u) for u in g} - {None})
            for a in range(len(keys)):
                for b in range(a + 1, len(keys)):
                    cross.add((keys[a], keys[b]))

    tier_counts: Dict[str, int] = {}
    warning_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for t in tranches:
        tier_counts[t["tier"]] = tier_counts.get(t["tier"], 0) + 1
        for w in t["warnings"]:
            warning_counts[w] = warning_counts.get(w, 0) + 1
        has_cz = "czkawka" in t["sources"]
        has_apple = any(s.startswith("apple") for s in t["sources"])
        cls = ("both" if has_cz and has_apple
               else "czkawka-only" if has_cz else "apple-only")
        source_counts[cls] = source_counts.get(cls, 0) + 1

    plan_body = {
        "version": PLAN_VERSION,
        "script": SCRIPT,
        "library": scan["library"],
        "reader": scan["reader"],
        "generation": scan["generation"],
        "policy": {
            "keeper": ("pixels desc, format rank (RAW>HEIC>PNG/TIFF>JPEG), "
                       f"file size within the surviving format (ties within "
                       f"{size_tol_pct:g}% count as equal), oldest timestamp, "
                       "shortest filename, earliest import, uuid"),
            "size_tolerance_pct": size_tol_pct,
            "format_floor_pct": DEFAULT_FORMAT_FLOOR_PCT,
            "date": "oldest plausible candidate across tranche",
            "min_plausible_year": min_year,
            "date_spread_warn_days": spread_warn_days,
            "near": scan.get("near"),
        },
        "summary": {
            "tranches": len(tranches),
            "members": sum(len(t["members"]) for t in tranches),
            "losers": sum(len(t["members"]) - 1 for t in tranches),
            "by_source": dict(sorted(source_counts.items())),
            "tiers": dict(sorted(tier_counts.items())),
            "warnings": dict(sorted(warning_counts.items())),
            "suggested_auto_approve": sum(1 for t in tranches if t["suggested"]),
            "czkawka_cross_tranche_links": len(cross),
        },
        "tranches": tranches,
        "members": {u: member_public(m) for u, m in sorted(members.items())},
    }
    plan_body["plan_key"] = hashlib.sha1(
        json.dumps(plan_body, sort_keys=True).encode()).hexdigest()[:12]
    return plan_body


def uuid_file_lines(plan: dict, kind: str) -> List[str]:
    """keepers.txt / losers.txt bodies. Tranches with unmergeable members or
    no verification are written commented out, mirroring the convention of
    graph_photo_dates.py (downstream --uuid-from-file consumers skip them)."""
    lines = [f"# PROPOSED {kind} from {SCRIPT} plan -- review first; no "
             "decisions applied"]
    for t in plan["tranches"]:
        blocked = [w for w in t["warnings"] if w.startswith("unmergeable")]
        uuids = ([t["keeper"]] if kind == "keepers"
                 else [u for u in t["members"] if u != t["keeper"]])
        for u in uuids:
            if blocked:
                lines.append(f"# {u}  (tranche {t['id']}: {', '.join(blocked)})")
            else:
                lines.append(u)
    return lines


def cmd_plan(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    scan_path = out_dir / "scan.json"
    if not scan_path.exists():
        raise SystemExit(f"{scan_path} not found -- run scan first")
    scan = json.loads(scan_path.read_text())
    dup_groups = load_czkawka(out_dir, "czkawka_dup.json")
    image_groups = load_czkawka(out_dir, "czkawka_image.json")
    video_groups = load_czkawka(out_dir, "czkawka_video.json")

    plan = build_plan(scan, dup_groups, image_groups, video_groups,
                      args.min_plausible_year, args.date_spread_warn_days,
                      size_tol_pct=args.size_tolerance_pct)
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=1, sort_keys=True))
    for kind in ("keepers", "losers"):
        (out_dir / f"{kind}.txt").write_text(
            "\n".join(uuid_file_lines(plan, kind)) + "\n")

    s = plan["summary"]
    print(f"Plan: {s['tranches']:,} tranche(s), {s['members']:,} member(s), "
          f"{s['losers']:,} proposed removal(s)")
    print("Sources: " + ", ".join(f"{k}={v}" for k, v in s["by_source"].items()))
    print(f"Tiers: " + ", ".join(f"{k}={v}" for k, v in s["tiers"].items()))
    if s["warnings"]:
        top = sorted(s["warnings"].items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{k}={v:,}" for k, v in top[:8])
        extra = f", +{len(top) - 8} more kind(s)" if len(top) > 8 else ""
        print(f"Warnings: {shown}{extra}")
    print(f"Suggested auto-approvals (exact/visual-0, no warnings): "
          f"{s['suggested_auto_approve']:,}")
    if s["czkawka_cross_tranche_links"]:
        print(f"Note: czkawka links {s['czkawka_cross_tranche_links']:,} pair(s) "
              "of tranches Apple kept apart -- the full-library scan (phase 2) "
              "will surface those properly.")
    print(f"\nWrote {out_dir / 'plan.json'}, keepers.txt, losers.txt")
    print(f"Next: osxphotos run {SCRIPT} review --out {out_dir} --open")
    return 0


# --------------------------------------------------------------------------
# review: thumbnails + static HTML gallery
# --------------------------------------------------------------------------

def make_thumb(src: str, dst: Path, is_video: bool, px: int) -> bool:
    if dst.exists():
        return True
    if not src or not os.path.exists(src):
        return False
    if is_video and not src.lower().endswith((".jpg", ".jpeg", ".png", ".heic")):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-ss", "1", "-i", src,
             "-frames:v", "1", "-vf", f"scale={px}:-2", str(dst)],
            capture_output=True)
        return proc.returncode == 0 and dst.exists()
    proc = subprocess.run(
        ["sips", "-s", "format", "jpeg", "-Z", str(px), src, "--out", str(dst)],
        capture_output=True)
    return proc.returncode == 0 and dst.exists()


def load_saved_decisions(out_dir: Path) -> Dict[str, dict]:
    """Decisions from a previous review session, if any, so a part-finished
    review resumes instead of restarting. Keys that no longer name a tranche
    are harmless -- apply only ever looks at tranches in the current plan."""
    path = out_dir / "decisions.json"
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return obj.get("decisions") or {} if isinstance(obj, dict) else {}


def render_report(plan: dict, thumbs_ok: Set[str],
                  saved: Optional[Dict[str, dict]] = None,
                  thumb_rel: str = "thumbs") -> str:
    """Static review page. All member data reaches the DOM via textContent
    (never innerHTML), so filenames and titles cannot inject markup."""
    data = {
        "plan_key": plan["plan_key"],
        "summary": plan["summary"],
        "policy": plan["policy"],
        "library": plan["library"],
        "tranches": plan["tranches"],
        "members": {
            u: {k: m.get(k) for k in
                ("uuid", "filename", "ext", "size", "width", "height",
                 "photos_date", "date_added", "favorite", "flags", "albums",
                 "keywords", "title", "description", "exif")}
            for u, m in plan["members"].items()
        },
        "thumbs": sorted(thumbs_ok),
        "thumb_dir": thumb_rel,
        "saved_decisions": saved or {},
    }
    # <-escape every "<" so nothing in the data can ever terminate the
    # <script> block or read as markup ("<" never occurs in JSON syntax, so
    # the global replace only touches string contents)
    payload = json.dumps(data, sort_keys=True).replace("<", "\\u003c")
    return REPORT_TEMPLATE.replace("__DATA__", payload)


_UUID_RE = re.compile(r"[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}")


def _reveal_allowed(uuid, allowed: Set[str]) -> bool:
    """Only well-formed uuids that the plan actually contains may be passed
    to AppleScript -- the page can only reveal assets it already shows."""
    if not isinstance(uuid, str):
        return False
    uuid = _norm_uuid(uuid)
    return bool(_UUID_RE.fullmatch(uuid)) and uuid in allowed


def _valid_decisions_payload(obj) -> bool:
    return (isinstance(obj, dict) and obj.get("version") == 1
            and isinstance(obj.get("decisions"), dict))


def serve_report(out_dir: Path, report_dir: Path, port: int, allowed: Set[str],
                 plan_key: str, open_browser: bool) -> int:
    """Serve the report on 127.0.0.1 so the page gains two endpoints:
    GET /reveal?uuid=X spotlights that asset in Photos.app via AppleScript
    (the same reveal `osxphotos show` does), and POST /decisions saves the
    review's decisions as a timestamped file next to plan.json, refreshing
    the stable decisions.json that apply reads."""
    import http.server
    from functools import partial

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *log_args):  # keep the terminal quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            """Reply with a JSON body and a STANDARD reason phrase.

            Never put arbitrary text in the status line: it is encoded
            latin-1, and AppleScript errors carry curly quotes, which raised
            UnicodeEncodeError and killed the request thread. json.dumps
            escapes non-ASCII, so the body is always safe."""
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_one_request(self):
            # a handler crash must not take the connection down with it
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as err:
                try:
                    self._json(500, {"ok": False, "error": repr(err)[:300]})
                except Exception:
                    pass
                self.close_connection = True

        def do_POST(self):
            if self.path != "/decisions":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not 0 < length <= 10_000_000:
                self.send_error(400, "bad content length")
                return
            try:
                obj = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_error(400, "not JSON")
                return
            if not _valid_decisions_payload(obj):
                self.send_error(400, "not a decisions payload")
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            body = json.dumps(obj, indent=1, sort_keys=True)
            stamped = out_dir / f"decisions-{stamp}.json"
            stamped.write_text(body)
            (out_dir / "decisions.json").write_text(body)
            print(f"decisions saved: {stamped} (and refreshed decisions.json)")
            resp = json.dumps({"ok": True, "path": str(stamped),
                               "plan_key_match": obj.get("plan_key") == plan_key})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(resp.encode())

        def do_GET(self):
            if not self.path.startswith("/reveal"):
                return super().do_GET()
            query = urllib.parse.urlparse(self.path).query
            uuid = (urllib.parse.parse_qs(query).get("uuid") or [""])[0]
            if not _reveal_allowed(uuid, allowed):
                self._json(403, {"ok": False, "error": "unknown uuid"})
                return
            script = ('tell application "Photos"\n  activate\n'
                      f'  spotlight media item id "{_norm_uuid(uuid)}"\n'
                      "end tell")
            try:
                proc = subprocess.run(["osascript", "-e", script],
                                      capture_output=True, text=True, timeout=20)
            except subprocess.TimeoutExpired:
                self._json(504, {"ok": False, "error": "Photos did not respond"})
                return
            if proc.returncode == 0:
                self._json(200, {"ok": True})
            else:
                self._json(502, {"ok": False,
                                 "error": (proc.stderr or "").strip()[:300]
                                 or "osascript failed"})

    handler = partial(Handler, directory=str(report_dir))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/index.html"
        print(f"Serving the review at {url} -- Ctrl-C to stop.")
        print("The per-member Photos buttons and the 'o' key reveal assets "
              "in Photos.app.")
        if open_browser:
            subprocess.run(["open", url], check=False)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    plan_path = out_dir / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"{plan_path} not found -- run plan first")
    plan = json.loads(plan_path.read_text())

    report_dir = out_dir / "report"
    # keyed by size: changing --thumb-size must not silently reuse thumbnails
    # generated at the old size, and switching back stays instant
    thumb_rel = f"thumbs-{args.thumb_size}"
    thumbs_dir = report_dir / thumb_rel
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    thumbs_ok: Set[str] = set()
    members = plan["members"]
    wanted = [u for t in plan["tranches"] for u in t["members"]]
    if args.skip_thumbs:
        thumbs_ok = {u for u in wanted if (thumbs_dir / f"{u}.jpg").exists()}
    else:
        todo = [u for u in wanted if not (thumbs_dir / f"{u}.jpg").exists()]
        thumbs_ok = set(wanted) - set(todo)   # NOT a per-item set() build
        if todo:
            # sips/ffmpeg are subprocesses, so threads parallelise fine
            workers = args.thumb_workers or max(1, (os.cpu_count() or 4) - 2)
            print(f"thumbnails: {len(todo):,} to generate at "
                  f"{args.thumb_size}px on {workers} workers "
                  f"({len(thumbs_ok):,} already cached)...")
            from concurrent.futures import ThreadPoolExecutor

            def one(uuid: str) -> Tuple[str, bool]:
                m = members[uuid]
                return uuid, make_thumb(
                    m.get("thumb_source") or m.get("path") or "",
                    thumbs_dir / f"{uuid}.jpg",
                    "video" in m.get("flags", []), args.thumb_size)

            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for uuid, ok in pool.map(one, todo):
                    done += 1
                    if ok:
                        thumbs_ok.add(uuid)
                    if done % 2000 == 0:
                        print(f"  {done:,}/{len(todo):,}")

    saved = load_saved_decisions(out_dir)
    if saved:
        print(f"carrying {len(saved):,} decision(s) forward from "
              f"{out_dir / 'decisions.json'}")
    index = report_dir / "index.html"
    index.write_text(render_report(plan, thumbs_ok, saved, thumb_rel),
                     encoding="utf-8")
    missing = len(wanted) - len(thumbs_ok)
    print(f"Wrote {index} ({len(plan['tranches']):,} tranche(s)"
          + (f", {missing:,} thumbnail(s) unavailable" if missing else "") + ")")
    print("Open it, review, then use 'Export decisions' -- the downloaded "
          "decisions.json is what apply executes.")
    serve_port = getattr(args, "serve", None)
    if serve_port is not None:
        allowed = {u for t in plan["tranches"] for u in t["members"]}
        return serve_report(out_dir, report_dir, serve_port, allowed,
                            plan["plan_key"], args.open)
    if args.open:
        subprocess.run(["open", str(index)], check=False)
        print("(opened as file:// -- run with --serve to enable the "
              "reveal-in-Photos buttons)")
    return 0


REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Photos duplicate review</title>
<style>
:root { color-scheme: light dark; }
body { font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 0;
       background: Canvas; color: CanvasText; }
header { position: sticky; top: 0; background: Canvas; border-bottom: 1px solid
         color-mix(in srgb, CanvasText 20%, Canvas); padding: 10px 16px;
         display: flex; gap: 12px; align-items: center; flex-wrap: wrap; z-index: 2; }
header .counts { font-variant-numeric: tabular-nums; }
button, select { font: inherit; padding: 4px 10px; }
main { padding: 12px 16px 80px; max-width: 1300px; margin: 0 auto; }
.card { border: 1px solid color-mix(in srgb, CanvasText 22%, Canvas);
        border-radius: 10px; margin: 14px 0; padding: 10px 12px; }
.card.approved { border-color: #2e9e44; box-shadow: 0 0 0 1px #2e9e44 inset; }
.card.rejected { border-color: #c33; opacity: .6; }
.card h3 { margin: 0 0 6px; font-size: 14px; display: flex; gap: 8px;
           align-items: baseline; flex-wrap: wrap; }
.chip { font-size: 11px; padding: 1px 8px; border-radius: 999px;
        background: color-mix(in srgb, CanvasText 12%, Canvas); }
.chip.tier-exact, .chip.tier-visual-0 { background: #2e9e4433; }
.chip.tier-near, .chip.tier-video { background: #e8a02033; }
.chip.tier-partial, .chip.tier-unverified { background: #cc333333; }
.chip.warn { background: #cc333322; }
.dateline { margin: 2px 0 2px; }
.dateline b { color: #2e9e44; }
.keeperline { margin: 0 0 8px; font-size: 12px; opacity: .85; }
.keeperline b { color: #2e9e44; }
.keeperline .why { opacity: .8; }
.keeperline .manual { color: #e8a020; }
.members { display: flex; gap: 10px; overflow-x: auto; }
.member { min-width: 400px; max-width: 560px; border: 1px solid
          color-mix(in srgb, CanvasText 15%, Canvas); border-radius: 8px;
          padding: 8px; }
.member.keeper { border-color: #2e9e44; }
.member img { max-width: 100%; max-height: 380px; display: block;
              margin: 0 auto 6px; border-radius: 4px; object-fit: contain; }
.member .noimg { height: 200px; display: flex; align-items: center;
                 justify-content: center; font-size: 48px; opacity: .4; }
.member .name { font-weight: 600; word-break: break-all; }
.member table { border-collapse: collapse; margin-top: 4px; width: 100%; }
.member td { padding: 0 6px 1px 0; vertical-align: top; font-size: 12px; }
.member td:first-child { opacity: .55; white-space: nowrap; }
.suspect { text-decoration: line-through; opacity: .6; }
.oldest { color: #2e9e44; font-weight: 600; }
.actions { margin-top: 8px; display: flex; gap: 8px; align-items: center; }
.spacer { flex: 1; }
footer.load { text-align: center; padding: 16px; }
.card.focused { outline: 2px solid #4a90d9; outline-offset: 1px; }
.member { cursor: pointer; }
.member:hover { border-color: color-mix(in srgb, CanvasText 45%, Canvas); }
.member .reveal, .member .excl { font-size: 12px; margin-left: 8px; }
.member.excluded { opacity: .38; border-style: dashed; }
.member.excluded .name { text-decoration: line-through; }
.member.excluded img { filter: grayscale(1); }
#help { position: fixed; right: 16px; bottom: 16px; background: Canvas;
        border: 1px solid color-mix(in srgb, CanvasText 25%, Canvas);
        border-radius: 10px; padding: 10px 14px; display: none; z-index: 3;
        box-shadow: 0 6px 24px #0005; }
#help.show { display: block; }
#help td { padding: 1px 10px 1px 0; }
#status { font-size: 12px; opacity: .7; }
kbd { font: 12px ui-monospace, monospace; padding: 0 5px; border-radius: 4px;
      background: color-mix(in srgb, CanvasText 12%, Canvas); }
</style>
</head>
<body>
<header>
  <strong>Duplicate review</strong>
  <span class="counts" id="counts"></span>
  <select id="filter">
    <option value="all">all</option>
    <option value="undecided" selected>undecided</option>
    <option value="approved">approved</option>
    <option value="rejected">rejected</option>
    <option value="warnings">with warnings</option>
    <option value="exact">tier: exact</option>
    <option value="visual-0">tier: visual-0</option>
    <option value="near">tier: near</option>
    <option value="video">tier: video</option>
    <option value="partial">tier: partial</option>
    <option value="unverified">tier: unverified</option>
    <option value="src-czkawka">source: czkawka only</option>
    <option value="src-apple">source: apple only</option>
    <option value="src-both">source: both</option>
  </select>
  <button id="approve-shown">Approve all shown</button>
  <button id="clear-shown">Clear shown</button>
  <button id="helpbtn" title="keyboard shortcuts">?</button>
  <span id="status"></span>
  <span class="spacer"></span>
  <button id="export">Export decisions</button>
  <label><input type="file" id="import" hidden>
    <button onclick="document.getElementById('import').click()">Import</button>
  </label>
</header>
<main id="list"></main>
<div id="help"><table>
<tr><td><kbd>j</kbd> / <kbd>k</kbd></td><td>next / previous tranche</td></tr>
<tr><td><kbd>g</kbd><kbd>g</kbd> / <kbd>G</kbd></td><td>first / last</td></tr>
<tr><td><kbd>a</kbd> / <kbd>x</kbd></td><td>approve / reject, then advance</td></tr>
<tr><td><kbd>u</kbd></td><td>clear decision</td></tr>
<tr><td><kbd>n</kbd></td><td>next undecided</td></tr>
<tr><td><kbd>h</kbd> / <kbd>l</kbd></td><td>cycle keeper</td></tr>
<tr><td><kbd>o</kbd></td><td>reveal keeper in Photos.app</td></tr>
<tr><td>exclude</td><td>drop one member: never deleted, never merged</td></tr>
<tr><td><kbd>?</kbd></td><td>toggle this help</td></tr>
<tr><td colspan="2" style="padding-top:8px;opacity:.75">
keeper: resolution → format (RAW&gt;HEIC&gt;PNG&gt;JPEG) → file size
within that format (differences under 1% count as equal) →
oldest date → shortest filename → first imported → UUID.
A smaller HEIC still wins: it is ~2x more efficient than JPEG.</td></tr>
<tr><td colspan="2" style="opacity:.75">
decisions autosave in this browser and reload from decisions.json</td></tr>
</table></div>
<footer class="load"><button id="more" hidden>Show more</button></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);
const KEY = "dedupe-decisions-" + DATA.plan_key;
const THUMBS = new Set(DATA.thumbs);
let dec = {};
try { dec = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const save = () => localStorage.setItem(KEY, JSON.stringify(dec));

// Decisions saved by a previous session (out-dir decisions.json, embedded at
// render time) are folded in for tranches this browser has no opinion on --
// so review can be picked up weeks later, on another machine, or after a
// rescan. Anything already decided here wins; nothing is overwritten.
let carried = 0;
for (const [k, v] of Object.entries(DATA.saved_decisions || {})) {
  if (!dec[k]) { dec[k] = v; carried++; }
}
if (carried) save();
// A 40k-tranche plan cannot all live in the DOM, so cards are a sliding
// window over `view`: MAX_RENDER at most, anchored at windowStart.
const PAGE = 100, MAX_RENDER = 600;
let view = [];         // filtered tranches (all of them, data only)
let windowStart = 0;   // view index of the first card in the DOM
let rendered = 0;      // how many cards are in the DOM
let focusIdx = 0;
let lastKey = "";
const REVEAL = location.protocol === "http:" || location.protocol === "https:";

const state = k => (dec[k] && dec[k].d) || "undecided";
const keeperOf = t => (dec[t.key] && dec[t.key].k) || t.keeper;

function matches(t, f) {
  if (f === "all") return true;
  if (f === "warnings") return t.warnings.length > 0;
  if (["undecided", "approved", "rejected"].includes(f)) return state(t.key) === f;
  const cz = t.sources.includes("czkawka");
  if (f === "src-czkawka") return cz && t.sources.length === 1;
  if (f === "src-apple") return !cz;
  if (f === "src-both") return cz && t.sources.length > 1;
  return t.tier === f;
}

function visibleTranches() {
  const f = document.getElementById("filter").value;
  return DATA.tranches.filter(t => matches(t, f));
}

// Decisions and keeper changes touch ONLY the affected card -- never a full
// re-render, so nothing flickers. Cards that stop matching the filter stay
// put until the next rebuild (filter change / import); that is deliberate,
// it keeps the triage flow visually stable.
// Members dropped from a tranche: not deleted, not merged into the keeper.
// Lets a single false match be removed without discarding the whole group.
const excludedOf = t => (dec[t.key] && dec[t.key].x) || [];

function toggleExcluded(t, uuid) {
  if (keeperOf(t) === uuid) return;          // the keeper can never be dropped
  dec[t.key] = dec[t.key] || {};
  const x = new Set(excludedOf(t));
  x.has(uuid) ? x.delete(uuid) : x.add(uuid);
  if (x.size) dec[t.key].x = [...x].sort(); else delete dec[t.key].x;
  if (!Object.keys(dec[t.key]).length) delete dec[t.key];
  save();
  const card = document.querySelector('.card[data-key="' + t.key + '"]');
  if (card) {
    const m = card.querySelector('.member[data-uuid="' + uuid + '"]');
    if (m) {
      m.classList.toggle("excluded", x.has(uuid));
      const b = m.querySelector(".excl");
      if (b) b.textContent = x.has(uuid) ? "include" : "exclude";
    }
    const kl = card.querySelector(".keeperline");
    if (kl) renderKeeperLine(t, kl);
  }
}

function setKeeper(t, uuid) {
  if (keeperOf(t) === uuid) return;
  // picking an excluded member as keeper implicitly brings it back
  const x = new Set(excludedOf(t));
  if (x.has(uuid)) toggleExcluded(t, uuid);
  dec[t.key] = dec[t.key] || {};
  dec[t.key].k = uuid;
  save();
  const card = document.querySelector('.card[data-key="' + t.key + '"]');
  if (!card) return;
  card.querySelectorAll(".member").forEach(m => {
    const mine = m.dataset.uuid === uuid;
    m.classList.toggle("keeper", mine);
    const radio = m.querySelector("input[type=radio]");
    if (radio) radio.checked = mine;
  });
  const kl = card.querySelector(".keeperline");
  if (kl) renderKeeperLine(t, kl);
}

function syncCard(card) {
  const s = state(card.dataset.key);
  card.classList.toggle("approved", s === "approved");
  card.classList.toggle("rejected", s === "rejected");
}

function decide(key, d, advance) {
  dec[key] = dec[key] || {};
  // Clearing writes an explicit "undecided" TOMBSTONE rather than deleting
  // the entry. Deleting it made the decision look like one never made, so
  // the carry-forward from decisions.json silently resurrected the old
  // answer on the next load -- a rejection could revert to approved.
  const cleared = (d === null || (dec[key].d === d && !advance));
  dec[key].d = cleared ? "undecided" : d;
  save();
  const card = document.querySelector('.card[data-key="' + key + '"]');
  if (card) syncCard(card);
  counts();
  if (advance) focusTo(focusIdx + 1);
}

function reveal(uuid) {
  if (!REVEAL) return;
  fetch("/reveal?uuid=" + encodeURIComponent(uuid))
    .then(r => r.json().then(j => ({ok: r.ok, j})))
    .then(({ok, j}) => { if (!ok) flash("reveal failed: " + (j.error || "?")); })
    .catch(() => flash("reveal failed -- is the review server still running?"));
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function hsize(n) {
  if (!n) return "?";
  const u = ["B", "KB", "MB", "GB"]; let i = 0, s = n;
  while (s >= 1024 && i < 3) { s /= 1024; i++; }
  return i ? s.toFixed(1) + " " + u[i] : s + " B";
}

// Why is THIS one the keeper? t.keeper_reason names the rule that decided
// (resolution -> file size -> format -> uuid tiebreak). If the reviewer
// picked someone else, say so and keep the default's rationale visible.
function renderKeeperLine(t, kl) {
  const cur = keeperOf(t);
  const name = u => (DATA.members[u] || {}).filename || u;
  kl.textContent = "";
  kl.appendChild(el("span", "", "keeper "));
  kl.appendChild(el("b", "", name(cur)));
  if (cur === t.keeper) {
    kl.appendChild(el("span", "why", " — " + (t.keeper_reason || "")));
  } else {
    kl.appendChild(el("span", "manual", " — your pick"));
    kl.appendChild(el("span", "why",
      "  (default was " + name(t.keeper) + ": " + (t.keeper_reason || "") + ")"));
  }
  const nx = excludedOf(t).length;
  if (nx) {
    kl.appendChild(el("span", "manual",
      "  · " + nx + " excluded, " + (t.members.length - nx - 1) +
      " to delete"));
  }
}

function memberCard(t, uuid) {
  const m = DATA.members[uuid];
  const isExcl = excludedOf(t).indexOf(uuid) !== -1;
  const card = el("div", "member" + (keeperOf(t) === uuid ? " keeper" : "")
                 + (isExcl ? " excluded" : ""));
  card.dataset.uuid = uuid;
  if (THUMBS.has(uuid)) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = (DATA.thumb_dir || "thumbs") + "/" + uuid + ".jpg";
    card.appendChild(img);
  } else {
    card.appendChild(el("div", "noimg",
      (m.flags || []).includes("video") ? "▶" : "✖"));
  }
  card.appendChild(el("div", "name",
    ((m.flags || []).includes("video") ? "▶ " : "") + (m.filename || uuid)));
  const tbl = document.createElement("table");
  const oldest = t.merged_date;
  const row = (k, v, cls) => {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "", k));
    tr.appendChild(el("td", cls || "", v || "—"));
    tbl.appendChild(tr);
  };
  row("dims", (m.width || "?") + "×" + (m.height || "?") +
      "  " + hsize(m.size) + "  ." + (m.ext || "?"));
  const dateRow = (label, val) => {
    if (!val) return;
    let cls = "";
    if (val === oldest) cls = "oldest";
    row(label, val.replace("T", " "), cls);
  };
  dateRow("photos", m.photos_date);
  const ex = m.exif || {};
  dateRow("exif dto", cleanExif(ex.DateTimeOriginal));
  dateRow("exif create", cleanExif(ex.CreateDate));
  dateRow("exif creation", cleanExif(ex.CreationDate));
  row("added", (m.date_added || "—").replace("T", " "));
  if ((m.flags || []).length) row("flags", m.flags.join(", "));
  if ((m.albums || []).length) row("albums", m.albums.join(", "));
  if ((m.keywords || []).length) row("keywords", m.keywords.join(", "));
  card.appendChild(tbl);
  const pick = el("label", "", " keeper");
  const radio = document.createElement("input");
  radio.type = "radio"; radio.name = "k-" + t.key;
  radio.checked = keeperOf(t) === uuid;
  radio.onchange = () => setKeeper(t, uuid);
  pick.prepend(radio);
  card.appendChild(pick);
  if (REVEAL) {
    const btn = el("button", "reveal", "Photos");
    btn.title = "Reveal in Photos.app";
    btn.onclick = ev => { ev.stopPropagation(); reveal(uuid); };
    card.appendChild(btn);
  }
  if (t.members.length > 2) {
    const ex = el("button", "excl", isExcl ? "include" : "exclude");
    ex.title = "Drop this one from the group: never deleted, never merged";
    ex.onclick = ev => { ev.stopPropagation(); toggleExcluded(t, uuid); };
    card.appendChild(ex);
  }
  card.onclick = () => setKeeper(t, uuid);  // the whole card picks the keeper
  return card;
}

function cleanExif(v) {
  if (!v || typeof v !== "string" || v.startsWith("-") || v.startsWith("0000")) return null;
  return v;
}

function trancheCard(t) {
  const card = el("div", "card " + state(t.key));
  card.dataset.key = t.key;
  const h = el("h3");
  h.appendChild(el("span", "", "#" + t.id));
  h.appendChild(el("span", "chip tier-" + t.tier,
    t.tier + (t.czkawka_max_diff != null ? " d" + t.czkawka_max_diff : "")));
  t.sources.forEach(s => h.appendChild(el("span", "chip", s.replace("apple-", " "))));
  t.warnings.forEach(w => h.appendChild(el("span", "chip warn", w)));
  card.appendChild(h);
  const dl = el("div", "dateline");
  dl.appendChild(el("span", "", "merged date "));
  dl.appendChild(el("b", "", (t.merged_date || "none").replace("T", " ")));
  dl.appendChild(el("span", "", "  from " + (t.date_source || "?") +
    (t.date_spread_days ? "  (spread " + t.date_spread_days + "d)" : "")));
  card.appendChild(dl);
  const kl = el("div", "keeperline");
  card.appendChild(kl);
  renderKeeperLine(t, kl);
  const row = el("div", "members");
  displayOrder(t).forEach(u => row.appendChild(memberCard(t, u)));
  card.appendChild(row);
  const actions = el("div", "actions");
  const mk = (label, d) => {
    const b = el("button", "", label);
    b.onclick = () => decide(t.key, d, false);
    return b;
  };
  actions.appendChild(mk("Approve", "approved"));
  actions.appendChild(mk("Reject", "rejected"));
  if (t.suggested) actions.appendChild(el("span", "chip", "suggested"));
  card.appendChild(actions);
  return card;
}

function counts() {
  let a = 0, r = 0;
  DATA.tranches.forEach(t => {
    const s = state(t.key);
    if (s === "approved") a++; else if (s === "rejected") r++;
  });
  const win = view.length
    ? "  ·  showing " + (windowStart + 1) + "–" + (windowStart + rendered) +
      " of " + view.length
    : "";
  document.getElementById("counts").textContent =
    DATA.tranches.length + " tranches · " + a + " approved · " +
    r + " rejected · " + (DATA.tranches.length - a - r) + " undecided" + win;
}

function cardAt(i) {
  const off = i - windowStart;
  const list = document.getElementById("list");
  return (off >= 0 && off < rendered) ? list.children[off] : null;
}

function appendChunk() {
  const list = document.getElementById("list");
  const from = windowStart + rendered;
  view.slice(from, from + PAGE).forEach(t => list.appendChild(trancheCard(t)));
  rendered = Math.min(rendered + PAGE, view.length - windowStart);
  while (rendered > MAX_RENDER) {  // drop from the front, keep the DOM small
    for (let n = 0; n < PAGE && list.firstChild; n++)
      list.removeChild(list.firstChild);
    windowStart += PAGE;
    rendered -= PAGE;
  }
  document.getElementById("more").hidden = windowStart + rendered >= view.length;
  counts();
}

function renderWindow(start) {
  document.getElementById("list").textContent = "";
  windowStart = Math.max(0, Math.min(start, Math.max(view.length - 1, 0)));
  rendered = 0;
  appendChunk();
}

// Moving focus swaps one CSS class and scrolls -- the DOM stays untouched,
// so thumbnails never reload and nothing flickers.
function applyFocus(scroll) {
  const list = document.getElementById("list");
  const old = list.querySelector(".card.focused");
  if (old) old.classList.remove("focused");
  if (!view.length) return;
  const card = cardAt(focusIdx);
  if (!card) return;
  card.classList.add("focused");
  if (scroll) scrollCardIntoView(card);
}

// Explicit scrolling, not scrollIntoView(): after the window re-anchors, the
// DOM has just been rebuilt and scrollIntoView goes by stale layout, leaving
// the focused card thousands of pixels off-screen. getBoundingClientRect
// forces a fresh layout, so this is always accurate -- and it only moves the
// page when the card is actually out of view, so j/k stay still.
function scrollCardIntoView(card) {
  // innerHeight can be 0 in embedded/headless viewports; fall back rather
  // than compute against a zero-height window
  const vh = window.innerHeight || document.documentElement.clientHeight || 800;
  const top = document.querySelector("header").offsetHeight + 8;
  const bottom = vh - 8;
  const r = card.getBoundingClientRect();
  if (r.top < top || r.height > bottom - top) window.scrollBy(0, r.top - top);
  else if (r.bottom > bottom) window.scrollBy(0, r.bottom - bottom);
}

function focusTo(i) {
  if (!view.length) return;
  const c = Math.max(0, Math.min(i, view.length - 1));
  if (c === focusIdx && cardAt(c)) return;  // j/k at the edges: a true no-op
  focusIdx = c;
  if (!cardAt(c)) {
    // one step past the end is the common j-at-the-boundary case: extend.
    // anything else is a jump (G, n, filter) -- re-anchor the window instead
    // of rendering everything in between.
    if (c === windowStart + rendered) appendChunk();
    if (!cardAt(c)) renderWindow(Math.floor(c / PAGE) * PAGE);
  }
  applyFocus(true);
}

function nextUndecided() {
  for (let s = 1; s <= view.length; s++) {
    const i = (focusIdx + s) % view.length;
    if (state(view[i].key) === "undecided") { focusTo(i); return; }
  }
}

// The plan's chosen keeper always renders first, so the pick is where your
// eye already is. Deliberately keyed on t.keeper, NOT the current selection:
// re-sorting as you cycle with h/l would make the row shuffle under you.
function displayOrder(t) {
  return [t.keeper].concat(t.members.filter(u => u !== t.keeper));
}

function moveKeeper(t, delta) {
  const order = displayOrder(t);
  const i = order.indexOf(keeperOf(t));
  setKeeper(t, order[(i + delta + order.length) % order.length]);
}

function toggleHelp() { document.getElementById("help").classList.toggle("show"); }

// Full rebuild only when the card SET changes: filter switch, import,
// initial load. Everything else edits the standing DOM in place.
function rebuild() {
  view = visibleTranches();
  if (focusIdx >= view.length) focusIdx = Math.max(0, view.length - 1);
  renderWindow(Math.floor(focusIdx / PAGE) * PAGE);
  applyFocus(true);
}

document.addEventListener("keydown", ev => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const tag = ev.target && ev.target.tagName;
  if (tag === "SELECT" || tag === "TEXTAREA"
      || (tag === "INPUT" && ev.target.type !== "radio")) return;
  const cur = view[focusIdx];
  let handled = true;
  switch (ev.key) {
    case "j": focusTo(focusIdx + 1); break;
    case "k": focusTo(focusIdx - 1); break;
    case "G": focusTo(view.length - 1); break;
    case "g": if (lastKey === "g") focusTo(0); break;
    case "a": if (cur) decide(cur.key, "approved", true); break;
    case "x": if (cur) decide(cur.key, "rejected", true); break;
    case "u": if (cur) decide(cur.key, null, false); break;
    case "n": nextUndecided(); break;
    case "h": if (cur) moveKeeper(cur, -1); break;
    case "l": if (cur) moveKeeper(cur, 1); break;
    case "o": if (cur) reveal(keeperOf(cur)); break;
    case "?": toggleHelp(); break;
    case "Escape": document.getElementById("help").classList.remove("show"); break;
    default: handled = false;
  }
  lastKey = ev.key;
  if (handled) ev.preventDefault();
});

document.getElementById("filter").onchange = () => { focusIdx = 0; rebuild(); };
document.getElementById("more").onclick = () => appendChunk();
document.getElementById("helpbtn").onclick = toggleHelp;
document.getElementById("approve-shown").onclick = () => {
  view.forEach(t => { dec[t.key] = dec[t.key] || {}; dec[t.key].d = "approved"; });
  save();
  [...document.getElementById("list").children].forEach(syncCard);
  counts();
};
document.getElementById("clear-shown").onclick = () => {
  view.forEach(t => { dec[t.key] = dec[t.key] || {};
    dec[t.key].d = "undecided"; });   // tombstone, see decide()
  save();
  [...document.getElementById("list").children].forEach(syncCard);
  counts();
};
function flash(msg) {
  const s = document.getElementById("status");
  s.textContent = msg;
  clearTimeout(flash.t);
  flash.t = setTimeout(() => { s.textContent = ""; }, 6000);
}

function downloadDecisions(out) {
  const blob = new Blob([JSON.stringify(out, null, 1)],
                        { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "decisions.json";
  a.click();
}

document.getElementById("export").onclick = () => {
  const out = { version: 1, plan_key: DATA.plan_key, decisions: dec };
  if (!REVEAL) { downloadDecisions(out); return; }
  fetch("/decisions", { method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(out) })
    .then(r => { if (!r.ok) throw 0; return r.json(); })
    .then(res => flash("saved " + res.path
                       + (res.plan_key_match === false ? "  (PLAN MISMATCH!)" : "")))
    .catch(() => { flash("server save failed -- downloaded instead");
                   downloadDecisions(out); });
};
document.getElementById("import").onchange = ev => {
  const file = ev.target.files[0];
  if (!file) return;
  file.text().then(txt => {
    const obj = JSON.parse(txt);
    if (obj.plan_key !== DATA.plan_key &&
        !confirm("decisions.json is for a different plan; import anyway?")) return;
    Object.assign(dec, obj.decisions || {});
    save(); rebuild();
  });
};
rebuild();
if (carried) flash("loaded " + carried + " saved decision(s) from decisions.json");
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# apply / verify: consume plan.json + decisions.json (phase 2)
# --------------------------------------------------------------------------

DATE_TOLERANCE_SECONDS = 2


def _dt_close(a_iso: Optional[str], b_iso: Optional[str],
              tol: int = DATE_TOLERANCE_SECONDS) -> bool:
    a, b = parse_iso(a_iso), parse_iso(b_iso)
    if a is None or b is None:
        return a is b
    return abs((a - b).total_seconds()) <= tol


def load_decisions(path: Path, plan: dict, force_plan_key: bool) -> Dict[str, dict]:
    obj = json.loads(path.read_text())
    if obj.get("version") != 1:
        raise SystemExit(f"{path}: unsupported decisions version {obj.get('version')!r}")
    decisions = obj.get("decisions") or {}
    if obj.get("plan_key") != plan.get("plan_key"):
        keys = {t["key"] for t in plan["tranches"]}
        matched = sum(1 for k in decisions if k in keys)
        msg = (f"{path} was exported for plan {obj.get('plan_key')!r}; this is "
               f"plan {plan.get('plan_key')!r} (the library or the scan "
               f"changed since). {matched:,} of {len(decisions):,} decision(s) "
               "still name a tranche in this plan -- tranche ids are derived "
               "from their members, so groups whose membership is unchanged "
               "carry over; the rest need re-reviewing.")
        if not force_plan_key:
            raise SystemExit(msg + "\nRe-run with --force-plan-key to apply "
                             "the ones that match.")
        print("note: " + msg)
    return decisions


def approved_tranches(plan: dict, decisions: Dict[str, dict]
                      ) -> List[Tuple[dict, str]]:
    """(tranche, effective keeper) for every approved tranche. A reviewer
    keeper override must name a member; anything else means the decisions
    file does not belong to this plan."""
    out = []
    for t in plan["tranches"]:
        d = decisions.get(t["key"]) or {}
        if d.get("d") != "approved":
            continue
        keeper = d.get("k") or t["keeper"]
        if keeper not in t["members"]:
            raise SystemExit(
                f"decisions.json picks keeper {keeper} for tranche {t['key']}, "
                "which is not a member of that tranche -- wrong decisions file?")
        out.append((t, keeper))
    return out


def read_live_rows(conn: sqlite3.Connection, uuids: Iterable[str]
                   ) -> Dict[str, dict]:
    """Current state of these assets straight from the live database:
    existence, trash state, date, favorite. The ground truth apply trusts
    over anything recorded at scan time."""
    rows: Dict[str, dict] = {}
    todo = sorted(set(uuids))
    has_tz = "ZTIMEZONEOFFSET" in table_columns(conn, "ZADDITIONALASSETATTRIBUTES")
    # same capture-local frame as the scan, or the comparison against
    # merged_date invents a whole-hour difference and writes a wrong instant
    tz_sel = "aaa.ZTIMEZONEOFFSET" if has_tz else "NULL"
    for i in range(0, len(todo), 800):
        chunk = todo[i:i + 800]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT a.ZUUID, a.ZDATECREATED, a.ZFAVORITE, a.ZTRASHEDSTATE,"
                f" {tz_sel} AS tz_offset FROM ZASSET a"
                f" LEFT JOIN ZADDITIONALASSETATTRIBUTES aaa ON aaa.ZASSET = a.Z_PK"
                f" WHERE a.ZUUID IN ({marks})", chunk):
            rows[_norm_uuid(r["ZUUID"])] = {
                "exists": True,
                "trashed": bool(r["ZTRASHEDSTATE"]),
                "date": iso(core_data_to_local(r["ZDATECREATED"], r["tz_offset"])),
                "tz_offset": r["tz_offset"],
                "favorite": bool(r["ZFAVORITE"]),
            }
    for uuid in todo:
        rows.setdefault(uuid, {"exists": False, "trashed": False, "date": None,
                               "tz_offset": None, "favorite": False})
    return rows


def build_apply_worklist(plan: dict, decisions: Dict[str, dict],
                         live: Dict[str, dict],
                         skip_photoscript: bool = False) -> dict:
    """Everything --apply would do, computed up front and deterministically.

    Per approved tranche: a date write when the keeper's live date differs
    from the merged date by more than the tolerance; a favorite when any
    member is favorited but the keeper is not; album adds / keyword union /
    title+description fill (photoscript work, needs the rich scan); and the
    losers to delete. Tranches with unmergeable members or members that
    vanished since the plan are skipped with a reason."""
    members_all: Dict[str, dict] = plan["members"]
    work = {"tranches": [], "dates": [], "favorites": [], "albums": [],
            "keywords": [], "texts": [], "deletes": [], "skipped": []}

    for t, keeper in approved_tranches(plan, decisions):
        key = t["key"]
        if any(w.startswith("unmergeable-member") for w in t["warnings"]):
            work["skipped"].append({"key": key, "reason": "unmergeable-member"})
            continue

        # Members the reviewer excluded are dropped from the group entirely:
        # not deleted, and their albums/keywords are NOT merged into the
        # keeper. This is how a false match inside an otherwise-good tranche
        # gets handled without throwing the whole tranche away.
        excluded = set(( decisions.get(key) or {}).get("x") or [])
        members = [u for u in t["members"] if u not in excluded]
        if keeper in excluded or len(members) < 2:
            work["skipped"].append({"key": key, "reason": "excluded-collapsed"})
            continue

        gone = [u for u in members
                if not live[u]["exists"] or live[u]["trashed"]]
        if gone:
            work["skipped"].append({"key": key,
                                    "reason": f"member-gone:{','.join(gone)}"})
            continue

        losers = [u for u in members if u != keeper]
        rec = {"key": key, "id": t["id"], "keeper": keeper, "losers": losers}
        if excluded:
            rec["excluded"] = sorted(excluded)

        if t["merged_date"] and not _dt_close(live[keeper]["date"], t["merged_date"]):
            # merged_date is capture-local wall clock; the keeper's own UTC
            # offset turns it back into the right absolute instant. Without
            # it merge-helper would assume the machine's current zone.
            work["dates"].append({"tranche": key, "uuid": keeper,
                                  "date": t["merged_date"],
                                  "utc_offset": live[keeper].get("tz_offset"),
                                  "old_date": live[keeper]["date"]})
        if any(live[u]["favorite"] for u in members) \
                and not live[keeper]["favorite"]:
            work["favorites"].append({"tranche": key, "uuid": keeper})

        if not skip_photoscript:
            keeper_m = members_all[keeper]
            keeper_albums = {a[0] for a in keeper_m.get("album_uuids") or []}
            adds = {}
            for u in losers:
                for auuid, title in members_all[u].get("album_uuids") or []:
                    if auuid not in keeper_albums:
                        adds[auuid] = title
            for auuid in sorted(adds):
                work["albums"].append({"tranche": key, "album_uuid": auuid,
                                       "album_title": adds[auuid],
                                       "uuid": keeper})
            union = sorted(set().union(
                *(members_all[u].get("keywords") or [] for u in members)))
            if union and set(union) != set(keeper_m.get("keywords") or []):
                work["keywords"].append({"tranche": key, "uuid": keeper,
                                         "keywords": union})
            texts = {}
            for field_name in ("title", "description"):
                if keeper_m.get(field_name):
                    continue
                donors = [members_all[u].get(field_name)
                          for u in [keeper] + losers
                          if members_all[u].get(field_name)]
                if donors:
                    texts[field_name] = donors[0]
            if texts:
                work["texts"].append({"tranche": key, "uuid": keeper, **texts})

        work["deletes"].extend({"tranche": key, "uuid": u} for u in losers)
        work["tranches"].append(rec)

    for lst in ("dates", "favorites", "albums", "keywords", "texts", "deletes"):
        work[lst].sort(key=lambda e: (e["tranche"], e.get("uuid", ""),
                                      e.get("album_uuid", "")))
    work["skipped"].sort(key=lambda e: e["key"])
    return work


def verify_tranche(t: dict, keeper: str, live: Dict[str, dict],
                   excluded: Optional[Set[str]] = None) -> str:
    if not live[keeper]["exists"] or live[keeper]["trashed"]:
        return "keeper-missing"
    date_ok = (not t["merged_date"]
               or _dt_close(live[keeper]["date"], t["merged_date"]))
    # excluded members were never meant to go, so they must not read as pending
    skip = excluded or set()
    losers = [u for u in t["members"] if u != keeper and u not in skip]
    losers_gone = all(not live[u]["exists"] or live[u]["trashed"] for u in losers)
    if date_ok and losers_gone:
        return "complete"
    if date_ok:
        return "losers-pending"
    if losers_gone:
        return "date-pending"
    return "pending"


def default_helper_path() -> Path:
    return Path(__file__).resolve().parent / "merge-helper" / "merge-helper"


def run_helper(helper: Path, manifest: Path, apply_writes: bool,
               verbose: Callable) -> int:
    cmd = [str(helper), "--manifest", str(manifest)]
    if apply_writes:
        cmd.append("--apply")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        verbose(f"  {line}")
    if proc.stderr:
        verbose(f"  {proc.stderr.strip()}")
    return proc.returncode


def apply_photoscript(work: dict, verbose: Callable) -> Set[str]:
    """Album adds, keyword unions, title/description fills via photoscript
    (AppleScript; Photos must be running). Returns the tranche keys that had
    failures -- their losers are held back from deletion this run, so the
    context they carry is never lost."""
    import photoscript

    failed: Set[str] = set()
    by_album: Dict[str, List[dict]] = {}
    for entry in work["albums"]:
        by_album.setdefault(entry["album_uuid"], []).append(entry)
    for auuid, entries in sorted(by_album.items()):
        title = entries[0]["album_title"]
        try:
            album = photoscript.Album(auuid)
            album.add([photoscript.Photo(e["uuid"]) for e in entries])
            verbose(f"  album '{title}': added {len(entries)} keeper(s)")
        except Exception as err:
            verbose(f"  album '{title}' ({auuid}): FAILED ({err})")
            failed.update(e["tranche"] for e in entries)
    for entry in work["keywords"]:
        try:
            photoscript.Photo(entry["uuid"]).keywords = entry["keywords"]
            verbose(f"  keywords -> {entry['uuid']}: {', '.join(entry['keywords'])}")
        except Exception as err:
            verbose(f"  keywords -> {entry['uuid']}: FAILED ({err})")
            failed.add(entry["tranche"])
    for entry in work["texts"]:
        try:
            photo = photoscript.Photo(entry["uuid"])
            if entry.get("title"):
                photo.title = entry["title"]
            if entry.get("description"):
                photo.description = entry["description"]
            verbose(f"  title/description -> {entry['uuid']}")
        except Exception as err:
            verbose(f"  title/description -> {entry['uuid']}: FAILED ({err})")
            failed.add(entry["tranche"])
    return failed


def cmd_apply(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    plan_path = out_dir / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"{plan_path} not found -- run plan first")
    plan = json.loads(plan_path.read_text())
    decisions = load_decisions(Path(args.decisions), plan, args.force_plan_key)

    library = Path(args.library) if args.library else Path(plan["library"])
    conn = sqlite_ro(library / "database" / "Photos.sqlite")
    try:
        wanted = {u for t, _ in approved_tranches(plan, decisions)
                  for u in t["members"]}
        if not wanted:
            print("No approved tranches in the decisions file; nothing to do.")
            return 0
        live = read_live_rows(conn, wanted)
        gen = library_generation(conn, 0)
    finally:
        conn.close()

    if gen["asset_count"] != plan["generation"]["asset_count"]:
        print(f"note: library has {gen['asset_count']:,} assets now vs "
              f"{plan['generation']['asset_count']:,} at scan time; every "
              "member is re-validated against the live database below.")

    work = build_apply_worklist(plan, decisions, live,
                                skip_photoscript=args.skip_photoscript)

    n_tr = len(work["tranches"])
    print(f"\nApply worklist: {n_tr:,} tranche(s) "
          f"({len(work['skipped'])} skipped)")
    print(f"  dates to set     : {len(work['dates']):,}")
    print(f"  favorites to set : {len(work['favorites']):,}")
    print(f"  album adds       : {len(work['albums']):,}")
    print(f"  keyword unions   : {len(work['keywords']):,}")
    print(f"  title/desc fills : {len(work['texts']):,}")
    print(f"  losers to delete : {len(work['deletes']):,}")
    for s in work["skipped"][:20]:
        print(f"  skipped {s['key']}: {s['reason']}")
    if len(work["skipped"]) > 20:
        print(f"  ... and {len(work['skipped']) - 20} more skipped")

    meta_manifest = out_dir / "apply-metadata.json"
    meta_manifest.write_text(json.dumps({
        "dates": [{"uuid": e["uuid"], "date": e["date"],
                   **({} if e.get("utc_offset") is None
                      else {"utc_offset": int(e["utc_offset"])})}
                  for e in work["dates"]],
        "favorites": [e["uuid"] for e in work["favorites"]],
    }, indent=1, sort_keys=True))
    del_manifest = out_dir / "apply-deletes.json"
    del_manifest.write_text(json.dumps({
        "deletes": [e["uuid"] for e in work["deletes"]],
    }, indent=1, sort_keys=True))
    print(f"\nManifests written: {meta_manifest}, {del_manifest}")

    if not args.apply:
        print("\nDry run only -- nothing written to the library. Re-run with "
              "--apply to execute: photoscript merges (Photos must be open "
              "unless --skip-photoscript), then dates/favorites via "
              "merge-helper, then ONE batched delete (one system dialog; "
              "everything goes to Recently Deleted, recoverable for 30 days).")
        return 0

    helper = Path(args.helper) if args.helper else default_helper_path()
    if (work["dates"] or work["favorites"] or work["deletes"]) \
            and not helper.exists():
        raise SystemExit(f"{helper} not built -- run: make -C {helper.parent}")

    log: dict = {"decisions": str(args.decisions), "worklist": work,
                 "status": "started", "phase": "none",
                 "photoscript_failed_tranches": [], "date_mismatches": [],
                 "helper_metadata_rc": None, "helper_deletes_rc": None,
                 "deletes_cancelled": False}
    held_back: Set[str] = set()

    # The undo record (keepers' old dates, losers' uuids) is written BEFORE
    # anything is modified and re-flushed after every phase, so an interrupt
    # or a crash mid-apply can never leave writes without a record of them.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = out_dir / f"apply-log-{stamp}.json"

    def flush_log() -> None:
        log_path.write_text(json.dumps(log, indent=1, sort_keys=True))

    flush_log()
    print(f"\nApply log started (undo record): {log_path}")

    try:
        _apply_phases(args, work, helper, meta_manifest, del_manifest,
                      library, log, held_back, flush_log)
        log["status"] = "complete"
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print(f"\nInterrupted during phase '{log['phase']}'. The log above "
              "records what was planned and what completed; rerun apply to "
              "continue (finished tranches are detected and skipped).")
        raise SystemExit(130)
    except SystemExit:
        log["status"] = "failed"
        raise
    finally:
        flush_log()

    print(f"\nApply log (undo record: old dates, deleted uuids): {log_path}")
    print("Deleted items sit in Recently Deleted for 30 days; date changes "
          "are listed in the log and revertible with osxphotos timewarp --reset.")
    print(f"Check the outcome with: osxphotos run {SCRIPT} verify "
          f"--decisions {args.decisions} --out {out_dir}")
    return 0


def _apply_phases(args: argparse.Namespace, work: dict, helper: Path,
                  meta_manifest: Path, del_manifest: Path, library: Path,
                  log: dict, held_back: Set[str], flush_log: Callable) -> None:
    """The write phases, in safety order. Split out so cmd_apply can wrap the
    whole thing in the log's try/finally."""
    out_dir = Path(args.out)

    if not args.skip_photoscript and (work["albums"] or work["keywords"]
                                      or work["texts"]):
        log["phase"] = "photoscript-metadata"
        flush_log()
        print("\nMerging albums/keywords/titles via photoscript "
              "(Photos must be running)...")
        try:
            failed = apply_photoscript(work, print)
        except Exception as err:
            raise SystemExit(
                f"photoscript merge failed outright ({err}) -- is Photos "
                "running? Use --skip-photoscript to merge without album/"
                "keyword transfer (that context is then not preserved).")
        held_back |= failed
        log["photoscript_failed_tranches"] = sorted(failed)

    if work["dates"] or work["favorites"]:
        log["phase"] = "photokit-dates-favorites"
        flush_log()
        print("\nWriting dates/favorites via merge-helper (PhotoKit)...")
        rc = run_helper(helper, meta_manifest, True, print)
        log["helper_metadata_rc"] = rc
        flush_log()
        if rc != 0:
            print(f"merge-helper metadata pass exited {rc}; verifying what "
                  "landed before considering deletes...")

        print("\nVerifying keeper dates against the live database...")
        import time
        time.sleep(3)
        conn = sqlite_ro(library / "database" / "Photos.sqlite")
        try:
            post = read_live_rows(conn, [e["uuid"] for e in work["dates"]])
        finally:
            conn.close()
        for e in work["dates"]:
            if not _dt_close(post[e["uuid"]]["date"], e["date"]):
                held_back.add(e["tranche"])
                log["date_mismatches"].append(e)
                print(f"  MISMATCH {e['uuid']}: wanted {e['date']}, "
                      f"library has {post[e['uuid']]['date']}")
        if not log["date_mismatches"]:
            print(f"  all {len(work['dates']):,} keeper date(s) verified")
        flush_log()

    deletes = [e for e in work["deletes"] if e["tranche"] not in held_back]
    held_deletes = len(work["deletes"]) - len(deletes)
    if held_deletes:
        print(f"\nHolding back {held_deletes:,} delete(s) from "
              f"{len(held_back)} tranche(s) with unverified merges -- "
              "rerun apply once the cause is fixed; they are retried then.")
    if args.skip_deletes:
        print(f"\n--skip-deletes: leaving {len(deletes):,} loser(s) in place.")
    elif deletes:
        log["phase"] = "photokit-deletes"
        log["deletes_attempted"] = [e["uuid"] for e in deletes]
        flush_log()
        del_manifest.write_text(json.dumps(
            {"deletes": [e["uuid"] for e in deletes]}, indent=1, sort_keys=True))
        print(f"\nDeleting {len(deletes):,} loser(s) via merge-helper -- "
              "answer the system confirmation dialog (one for the whole batch)...")
        rc = run_helper(helper, del_manifest, True, print)
        log["helper_deletes_rc"] = rc
        if rc == 3:
            log["deletes_cancelled"] = True
            print("Delete dialog cancelled; keepers are merged, losers remain. "
                  "Rerun apply to try the deletes again.")
    log["phase"] = "done"


def _stage_rows(out_dir: Path) -> List[Tuple[str, bool, str]]:
    """(stage, done, detail) for each pipeline stage, read off the out dir."""
    scan_p, plan_p = out_dir / "scan.json", out_dir / "plan.json"
    report_p = out_dir / "report" / "index.html"
    dec_p = out_dir / "decisions.json"
    rows = []

    scan = plan = None
    if scan_p.exists():
        try:
            scan = json.loads(scan_p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    rows.append(("scan", scan is not None,
                 (f"{len(scan['apple_tranches']):,} tranches, "
                  f"{len(scan['members']):,} members"
                  f"{', discovery' if scan.get('discover') else ', apple-only'}")
                 if scan else "not run"))

    if plan_p.exists():
        try:
            plan = json.loads(plan_p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    stale = bool(plan and scan and plan_p.stat().st_mtime < scan_p.stat().st_mtime)
    rows.append(("plan", plan is not None and not stale,
                 (f"{plan['summary']['tranches']:,} tranches, "
                  f"{plan['summary']['losers']:,} proposed removals"
                  + (" -- STALE, older than scan.json" if stale else ""))
                 if plan else "not run"))

    r_stale = bool(plan and report_p.exists()
                   and report_p.stat().st_mtime < plan_p.stat().st_mtime)
    rows.append(("review", report_p.exists() and not r_stale,
                 ("rendered" + (" -- STALE, older than plan.json" if r_stale
                                else ""))
                 if report_p.exists() else "not rendered"))

    detail, done = "no decisions exported yet", False
    if dec_p.exists() and plan:
        try:
            obj = json.loads(dec_p.read_text())
            dec = obj.get("decisions") or {}
            appr = [k for k, v in dec.items() if v.get("d") == "approved"]
            keys = {t["key"] for t in plan["tranches"]}
            live = [k for k in appr if k in keys]
            match = obj.get("plan_key") == plan.get("plan_key")
            detail = (f"{len(appr):,} approved ({len(live):,} in this plan)"
                      + ("" if match else "; plan_key differs -> needs "
                         "--force-plan-key"))
            done = bool(live)
        except (json.JSONDecodeError, OSError):
            detail = "unreadable"
    rows.append(("decisions", done, detail))

    logs = sorted(out_dir.glob("apply-log-*.json"))
    if logs:
        try:
            last = json.loads(logs[-1].read_text())
            w = last.get("worklist") or {}
            detail = (f"{logs[-1].name}: status={last.get('status','?')}, "
                      f"{len(w.get('dates') or []):,} date(s), "
                      f"{len(w.get('deletes') or []):,} delete(s)")
        except (json.JSONDecodeError, OSError):
            detail = f"{logs[-1].name}: unreadable"
        rows.append(("apply", True, detail))
    else:
        rows.append(("apply", False, "never run -- nothing written to Photos"))
    return rows


def cmd_status(args: argparse.Namespace) -> int:
    """Where am I, and what do I run next? The whole pipeline in one glance."""
    out_dir = Path(args.out)
    if not out_dir.exists():
        print(f"{out_dir} does not exist yet -- start with:\n"
              f"  osxphotos run {SCRIPT} scan --discover")
        return 0
    rows = _stage_rows(out_dir)
    print(f"Working directory: {out_dir}\n")
    for stage, done, detail in rows:
        print(f"  [{'x' if done else ' '}] {stage:<10} {detail}")

    state = {stage: done for stage, done, _ in rows}
    dec = out_dir / "decisions.json"
    print("\nNext:")
    if not state["scan"]:
        print(f"  osxphotos run {SCRIPT} scan --discover --out {out_dir}")
    elif not state["plan"]:
        print(f"  osxphotos run {SCRIPT} plan --out {out_dir}")
    elif not state["review"]:
        print(f"  osxphotos run {SCRIPT} review --out {out_dir} --serve --open")
    elif not state["decisions"]:
        print(f"  osxphotos run {SCRIPT} review --out {out_dir} --serve --open")
        print("  ...then approve tranches and click 'Export decisions'")
    else:
        force = "" if "force-plan-key" not in str(rows[3][2]) else " --force-plan-key"
        print(f"  osxphotos run {SCRIPT} apply --out {out_dir} "
              f"--decisions {dec}{force}          # dry run")
        print(f"  osxphotos run {SCRIPT} apply --out {out_dir} "
              f"--decisions {dec}{force} --apply  # write")
        print(f"  osxphotos run {SCRIPT} verify --out {out_dir} "
              f"--decisions {dec}{force}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    plan = json.loads((out_dir / "plan.json").read_text())
    decisions = load_decisions(Path(args.decisions), plan, args.force_plan_key)
    approved = approved_tranches(plan, decisions)
    if not approved:
        print("No approved tranches in the decisions file.")
        return 0
    library = Path(args.library) if args.library else Path(plan["library"])
    conn = sqlite_ro(library / "database" / "Photos.sqlite")
    try:
        live = read_live_rows(conn, {u for t, _ in approved for u in t["members"]})
    finally:
        conn.close()

    states: Dict[str, List[str]] = {}
    for t, keeper in approved:
        excluded = set((decisions.get(t["key"]) or {}).get("x") or [])
        states.setdefault(verify_tranche(t, keeper, live, excluded),
                          []).append(t["key"])
    print(f"Verify: {len(approved):,} approved tranche(s)")
    for state in sorted(states):
        print(f"  {state:<15} {len(states[state]):,}")
    residual = [k for s, keys in states.items() if s != "complete" for k in keys]
    for key in residual[:30]:
        print(f"  incomplete: {key}")
    if len(residual) > 30:
        print(f"  ... and {len(residual) - 30} more")
    return 0 if not residual else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def add_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=DEFAULT_OUT, metavar="DIR",
                   help=f"working directory (default {DEFAULT_OUT})")


def add_scan_args(p: argparse.ArgumentParser) -> None:
    add_out(p)
    p.add_argument("--library", metavar="PATH",
                   help="Photos library (default: last-opened)")
    p.add_argument("--reader", choices=("auto", "osxphotos", "sqlite"),
                   default="auto",
                   help="metadata reader (default auto = osxphotos, refused "
                   "while the WAL is huge; sqlite reads live, fewer fields)")
    p.add_argument("--discover", action="store_true",
                   help="sweep ALL local originals with czkawka, not just "
                   "Apple's groups: finds copies Apple missed, attaches them "
                   "to Apple's tranches, and surfaces czkawka-only tranches")
    p.add_argument("--include-bursts", action="store_true",
                   help="keep czkawka-only groups made entirely of one burst "
                   "(burst siblings; dropped by default)")
    p.add_argument("--limit", type=int, metavar="N",
                   help="only the first N groups (smoke tests)")
    p.add_argument("--near", type=int, default=DEFAULT_NEAR,
                   help=f"czkawka image max distance (default {DEFAULT_NEAR})")
    p.add_argument("--batch-size", type=int, default=0, metavar="N",
                   help="discovery only: hash in pausable batches of ~N files "
                   "(RETURN between batches, q to stop and resume later; "
                   "e.g. 20000). The final grouping pass reuses every "
                   "cached hash.")
    p.add_argument("--threads", type=int, default=0, metavar="N",
                   help="limit czkawka to N CPU threads (default: all cores)")
    p.add_argument("--skip-czkawka", action="store_true",
                   help="skip the czkawka runs entirely")
    p.add_argument("--czkawka-tools", default="dup,image,video", metavar="LIST",
                   help="which czkawka tools to run, comma-separated (default "
                   "dup,image,video). The video pass decodes every video via "
                   "ffmpeg and is by far the slowest; 'dup' already catches "
                   "byte-identical videos. Results from tools you skip are "
                   "reused from a previous run if their JSON is still in --out.")
    p.add_argument("--skip-exif", action="store_true",
                   help="skip the exiftool date harvest")
    p.add_argument("--farm-dir", metavar="PATH",
                   help="hardlink farm location (default <out>/farm)")
    p.add_argument("--max-wal-gb", type=float, default=DEFAULT_MAX_WAL_GB,
                   help="refuse the osxphotos reader above this WAL size "
                   f"(default {DEFAULT_MAX_WAL_GB:g})")


def add_plan_args(p: argparse.ArgumentParser) -> None:
    add_out(p)
    p.add_argument("--min-plausible-year", type=int,
                   default=DEFAULT_MIN_PLAUSIBLE_YEAR, metavar="YEAR",
                   help="dates before this year are implausible "
                   f"(default {DEFAULT_MIN_PLAUSIBLE_YEAR})")
    p.add_argument("--date-spread-warn-days", type=float,
                   default=DEFAULT_SPREAD_WARN_DAYS, metavar="DAYS",
                   help="warn when a tranche's plausible dates span more "
                   f"(default {DEFAULT_SPREAD_WARN_DAYS:g})")
    p.add_argument("--size-tolerance-pct", type=float,
                   default=DEFAULT_SIZE_TOL_PCT, metavar="PCT",
                   help="file sizes within this percentage of the biggest "
                   "count as equal quality, so the oldest copy wins instead "
                   f"of the one with a few more bytes (default "
                   f"{DEFAULT_SIZE_TOL_PCT:g}; 0 compares exact bytes)")


def add_review_args(p: argparse.ArgumentParser) -> None:
    add_out(p)
    p.add_argument("--open", action="store_true",
                   help="open the report in the default browser")
    p.add_argument("--thumb-size", type=int, default=DEFAULT_THUMB_PX,
                   help=f"thumbnail long edge in px (default {DEFAULT_THUMB_PX})")
    p.add_argument("--skip-thumbs", action="store_true",
                   help="reuse existing thumbnails only")
    p.add_argument("--thumb-workers", type=int, default=0, metavar="N",
                   help="parallel thumbnail workers (default: cores - 2)")
    p.add_argument("--serve", nargs="?", const=8942, type=int, default=None,
                   metavar="PORT",
                   help="serve the report on 127.0.0.1 (default port 8942) "
                   "with reveal-in-Photos buttons; Ctrl-C to stop")


def add_decisions_args(p: argparse.ArgumentParser) -> None:
    add_out(p)
    p.add_argument("--decisions", required=True, metavar="PATH",
                   help="decisions.json exported from the review page")
    p.add_argument("--library", metavar="PATH",
                   help="Photos library (default: the one recorded in the plan)")
    p.add_argument("--force-plan-key", action="store_true",
                   help="accept a decisions file exported for a different plan")


def add_apply_args(p: argparse.ArgumentParser) -> None:
    add_decisions_args(p)
    p.add_argument("--apply", action="store_true",
                   help="actually write; without this only the worklist and "
                   "manifests are produced")
    p.add_argument("--skip-deletes", action="store_true",
                   help="merge metadata but leave the losers in place")
    p.add_argument("--skip-photoscript", action="store_true",
                   help="skip album/keyword/title transfer (does not need "
                   "Photos running, but that context is not preserved)")
    p.add_argument("--helper", metavar="PATH",
                   help="merge-helper binary (default: merge-helper/merge-helper "
                   "next to this script)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Deterministic duplicate merge planning for Apple Photos: "
        "Apple's own Duplicates analysis, verified by czkawka, reviewed in "
        "HTML. Read-only; nothing in the library is modified.",
        epilog=f"""\
examples:
  osxphotos run {SCRIPT} all --open              # scan + plan + review
  osxphotos run {SCRIPT} scan --reader sqlite    # while the WAL is huge / mid-import
  osxphotos run {SCRIPT} scan --limit 5          # smoke test on 5 groups
  osxphotos run {SCRIPT} plan --min-plausible-year 1985
  osxphotos run {SCRIPT} review --open
  python3 {SCRIPT} --selftest                    # offline tests, safe anywhere
""")
    parser.add_argument("--selftest", action="store_true",
                        help="run offline self-tests and exit")
    sub = parser.add_subparsers(dest="command")
    add_scan_args(sub.add_parser("scan", help="read Apple groups + czkawka verify"))
    add_plan_args(sub.add_parser("plan", help="compute deterministic merge plan"))
    add_review_args(sub.add_parser("review", help="render the HTML review gallery"))
    add_apply_args(sub.add_parser(
        "apply", help="execute approved merges (dry-run without --apply)"))
    add_decisions_args(sub.add_parser(
        "verify", help="check approved tranches against the live library"))
    add_out(sub.add_parser(
        "status", help="where the pipeline stands and what to run next"))
    p_all = sub.add_parser("all", help="scan, plan, review in sequence")
    add_scan_args(p_all)
    p_all.add_argument("--min-plausible-year", type=int,
                       default=DEFAULT_MIN_PLAUSIBLE_YEAR)
    p_all.add_argument("--date-spread-warn-days", type=float,
                       default=DEFAULT_SPREAD_WARN_DAYS)
    p_all.add_argument("--size-tolerance-pct", type=float,
                       default=DEFAULT_SIZE_TOL_PCT)
    p_all.add_argument("--open", action="store_true")
    p_all.add_argument("--thumb-size", type=int, default=DEFAULT_THUMB_PX)
    p_all.add_argument("--skip-thumbs", action="store_true")
    p_all.add_argument("--thumb-workers", type=int, default=0)
    p_all.add_argument("--serve", nargs="?", const=8942, type=int, default=None,
                       metavar="PORT")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if not args.command:
        parser.print_help()
        return 2
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "review":
        return cmd_review(args)
    if args.command == "apply":
        return cmd_apply(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "all":
        rc = cmd_scan(args)
        if rc:
            return rc
        rc = cmd_plan(args)
        if rc:
            return rc
        return cmd_review(args)
    return 2


# --------------------------------------------------------------------------
# selftest (offline; no Photos library, no external binaries)
# --------------------------------------------------------------------------

def _member(uuid, filename="f.jpg", w=100, h=100, size=1000, ext=None,
            photos_date=None, exif=None, flags=(), burst_key=None) -> dict:
    return {
        "uuid": uuid, "filename": filename,
        # both readers lowercase the extension; mirror that here
        "ext": (ext if ext is not None else filename.rsplit(".", 1)[-1]).lower(),
        "path": None, "thumb_source": None, "size": size,
        "width": w, "height": h, "photos_date": photos_date,
        "date_added": None, "favorite": False, "flags": list(flags),
        "burst_key": burst_key, "albums": [], "keywords": [],
        "title": None, "description": None, "reader": "test",
        **({"exif": exif} if exif else {}),
    }


def selftest() -> None:
    now = datetime(2026, 7, 28, 12, 0, 0)

    # datetime plumbing
    assert parse_exif_dt("2016-08-19T09:12:44") == datetime(2016, 8, 19, 9, 12, 44)
    assert parse_exif_dt("2016:08:19 09:12:44") == datetime(2016, 8, 19, 9, 12, 44)
    off = parse_exif_dt("2016-08-19T09:12:44+00:00")
    assert off is not None and off.tzinfo is None  # converted to local naive
    assert parse_exif_dt("2016-08-19T09:12:44+0200") is not None
    assert parse_exif_dt("2016-08-19T09:12:44Z") is not None
    for bad in (None, "", "-", "0000:00:00 00:00:00", "garbage", 42):
        assert parse_exif_dt(bad) is None, bad
    assert core_data_to_local(0) is None
    assert core_data_to_local(None) is None
    utc_probe = core_data_to_local(86400)
    assert utc_probe is not None and utc_probe.tzinfo is None
    # a stored instant renders as wall clock AT THE CAPTURE LOCATION, so the
    # machine's own zone cannot shift it. 643811597 = 2021-05-27 12:33:17 UTC
    assert core_data_to_local(643811597, 3600) == datetime(2021, 5, 27, 13, 33, 17)
    assert core_data_to_local(643811597, 0) == datetime(2021, 5, 27, 12, 33, 17)
    assert core_data_to_local(643811597, 7200) == datetime(2021, 5, 27, 14, 33, 17)
    assert core_data_to_local(643811597, -18000) == datetime(2021, 5, 27, 7, 33, 17)
    assert core_data_to_local(0, 3600) is None
    assert iso(datetime(2020, 1, 2, 3, 4, 5)) == "2020-01-02T03:04:05"
    assert parse_iso("2020-01-02T03:04:05") == datetime(2020, 1, 2, 3, 4, 5)
    assert parse_iso(None) is None

    # suspect dates
    assert is_suspect_date(datetime(1970, 1, 1), 1990, now)
    assert is_suspect_date(datetime(1904, 1, 1), 1800, now)  # epoch even if old ok
    assert is_suspect_date(datetime(1989, 12, 31), 1990, now)
    assert not is_suspect_date(datetime(1990, 1, 1), 1990, now)
    assert is_suspect_date(datetime(2026, 7, 30), 1990, now)  # future
    assert not is_suspect_date(datetime(2026, 7, 29, 6, 0), 1990, now)  # <1d grace

    # keeper policy: pixels, size (material differences only), format, dates,
    # filename, uuid
    a = _member("B-BIG", "big.jpg", w=4000, h=3000, size=100)
    b = _member("A-SMALL", "small.jpg", w=100, h=100, size=999999)
    assert choose_keeper([a, b]) == "B-BIG"
    c = _member("C", "c.jpg", w=4000, h=3000, size=200)
    assert choose_keeper([a, c]) == "C"  # same pixels, 100% more bytes
    d = _member("D", "d.heic", w=4000, h=3000, size=200)
    assert choose_keeper([c, d]) == "D"  # same pixels+bytes, heic beats jpg
    e = _member("A-TIE", "e.heic", w=4000, h=3000, size=200)
    # filename order now decides before the uuid backstop: d.heic < e.heic
    assert choose_keeper([d, e]) == "D"
    # ...and with identical filenames, uuid is the final, stable backstop
    e_same = _member("A-TIE", "d.heic", w=4000, h=3000, size=200)
    assert choose_keeper([d, e_same]) == "A-TIE"
    assert format_rank("DNG") < format_rank("heic") < format_rank("png") \
        < format_rank("jpg") < format_rank("mov") < format_rank("xyz")

    # keeper_reason names the rung that actually decided
    big = _member("K1", "big.jpg", w=4000, h=3000, size=100)
    small = _member("K2", "small.jpg", w=100, h=100, size=999)
    assert "highest resolution (4000x3000)" == keeper_reason([big, small], "K1")
    # the screenshot case: same pixels, same format, larger file wins
    a = _member("K3", "a.jpg", w=1200, h=1600, size=324300)
    b = _member("K4", "b.jpg", w=1200, h=1600, size=328294)
    # 324300 vs 328294 is 1.2% apart -- a material difference, size still wins
    assert choose_keeper([a, b], 1990, now) == "K4"
    why = keeper_reason([a, b], "K4", 1990, now)
    assert why.startswith("materially larger file (") and "1200x1600" in why, why

    # A HANDFUL OF BYTES on a 320 KB file must NOT beat an older date: within
    # tolerance the sizes count as equal, so the ladder falls through to date.
    tiny_bigger = _member("AAA-NEWER", "e.jpg", w=1200, h=1600, size=328294,
                          photos_date="2017-10-29T16:06:16")
    older = _member("ZZZ-OLDER", "f.jpg", w=1200, h=1600, size=328294 - 9,
                    photos_date="2006-09-23T09:54:00")
    assert choose_keeper([tiny_bigger, older], 1990, now) == "ZZZ-OLDER"
    why_old = keeper_reason([tiny_bigger, older], "ZZZ-OLDER", 1990, now)
    assert why_old.startswith("oldest timestamp (2006-09-23T09:54:00)"), why_old
    # ...and with the tolerance switched off, exact bytes win again
    assert choose_keeper([tiny_bigger, older], 1990, now, 0.0) == "AAA-NEWER"

    heic = _member("K5", "c.heic", w=1200, h=1600, size=328294)
    assert keeper_reason([b, heic], "K5", 1990, now).startswith("better format (.heic")

    # THE HEIC CASE: at the same resolution a HEIC is about half the bytes of
    # the equivalent JPEG because HEVC is ~2x more efficient. Comparing the
    # two by size picks the JPEG export over the camera's own original, so
    # format is judged BEFORE size.
    iphone_heic = _member("HEIC", "IMG_1.HEIC", w=3024, h=4032, size=1037846)
    export_jpg = _member("JPG", "IMG_1.jpg", w=3024, h=4032, size=1656309)
    assert choose_keeper([export_jpg, iphone_heic], 1990, now) == "HEIC"
    why_h = keeper_reason([export_jpg, iphone_heic], "HEIC", 1990, now)
    assert why_h.startswith("better format (.heic over .jpg)"), why_h
    assert "2x more efficient" in why_h
    # ...but a HEIC far below even that efficiency is a degraded re-encode
    # and forfeits the format advantage (floor: 25% of the biggest)
    thumb_heic = _member("TINY", "IMG_1.HEIC", w=3024, h=4032, size=200000)
    assert choose_keeper([export_jpg, thumb_heic], 1990, now) == "JPG"
    # within one codec size is still meaningful: the bigger JPEG wins
    small_jpg = _member("J2", "IMG_2.jpg", w=3024, h=4032, size=1000000)
    assert choose_keeper([small_jpg, export_jpg], 1990, now) == "JPG"
    # RAW still outranks HEIC
    dng = _member("DNG", "IMG_1.dng", w=3024, h=4032, size=900000)
    assert choose_keeper([iphone_heic, dng], 1990, now) == "DNG"
    twin = _member("K6", "d.jpg", w=1200, h=1600, size=328294)
    assert "arbitrary" in keeper_reason([b, twin], "K4", 1990, now)
    assert keeper_reason([b], "K4") == "only member"

    # shortest filename beats a suffixed copy when all else ties
    plain = _member("ZZZ-PLAIN", "london.tif", w=1476, h=1000, size=3145728,
                    photos_date="2006-09-23T09:54:00")
    suffixed = _member("AAA-COPY", "london-2.tif", w=1476, h=1000, size=3145728,
                       photos_date="2006-09-23T09:54:00")
    assert choose_keeper([suffixed, plain], 1990, now) == "ZZZ-PLAIN"
    assert keeper_reason([suffixed, plain], "ZZZ-PLAIN", 1990, now
                         ).startswith("shortest filename (london.tif)")
    # keeper_pool reports which rung last narrowed the field
    assert keeper_pool([big, small], 1.0, 25.0)[1] == "resolution"
    assert keeper_pool([export_jpg, iphone_heic], 1.0, 25.0)[1] == "format"
    assert keeper_pool([a, b], 1.0, 25.0)[1] == "size"
    assert keeper_pool([plain, suffixed], 1.0, 25.0)[1] == ""

    # the real-world case: byte-identical copies, one a re-import whose date
    # drifted. The older one wins even though its UUID sorts LAST, so this
    # proves date beats the uuid backstop rather than coinciding with it.
    drifted = _member("AAA-REIMPORT", "x.jpg", w=1080, h=1920, size=143667,
                      photos_date="2025-12-22T01:45:18")
    drifted["date_added"] = "2026-07-27T11:54:40"
    original = _member("ZZZ-ORIGINAL", "x.jpg", w=1080, h=1920, size=143667,
                       photos_date="2024-12-18T21:21:58")
    original["date_added"] = "2024-12-18T21:22:00"
    pair = [drifted, original]
    assert choose_keeper(pair, 1990, now) == "ZZZ-ORIGINAL"
    why_old = keeper_reason(pair, "ZZZ-ORIGINAL", 1990, now)
    assert why_old.startswith("oldest timestamp (2024-12-18T21:21:58)"), why_old
    # quality still outranks date: a bigger file wins despite a newer date
    bigger_newer = _member("BIG", "y.jpg", w=1080, h=1920, size=200000,
                           photos_date="2025-12-22T01:45:18")
    assert choose_keeper([bigger_newer, original], 1990, now) == "BIG"
    # an implausible date never wins the tiebreak
    bogus = _member("BOGUS", "z.jpg", w=1080, h=1920, size=143667,
                    photos_date="1970-01-01T00:00:00")
    assert choose_keeper([bogus, original], 1990, now) == "ZZZ-ORIGINAL"
    # dates AND filename identical -> earliest import decides
    same_date = _member("AAA-LATER", "x.jpg", w=1080, h=1920, size=143667,
                        photos_date="2024-12-18T21:21:58")
    same_date["date_added"] = "2026-01-01T00:00:00"
    assert choose_keeper([same_date, original], 1990, now) == "ZZZ-ORIGINAL"
    assert keeper_reason([same_date, original], "ZZZ-ORIGINAL", 1990, now
                         ).startswith("imported first")
    # everything identical -> uuid backstop, still deterministic
    clone = _member("AAA-CLONE", "v.jpg", w=1080, h=1920, size=143667,
                    photos_date="2024-12-18T21:21:58")
    clone["date_added"] = original["date_added"]
    assert choose_keeper([original, clone], 1990, now) == "AAA-CLONE"
    assert "arbitrary" in keeper_reason([original, clone], "AAA-CLONE", 1990, now)
    # always explains whichever member choose_keeper actually returns
    for group in ([big, small], [a, b], [b, heic], [b, twin]):
        assert keeper_reason(group, choose_keeper(group))

    # merged date: oldest plausible across members and EXIF. A lone EXIF
    # date disagreeing with re-imported Photos dates is the NORMAL case the
    # merge fixes -- no spread warning for it.
    m1 = _member("U1", "a.jpg", photos_date="2019-06-02T14:11:03",
                 exif={"DateTimeOriginal": "2016-08-19T09:12:44"})
    m2 = _member("U2", "b.jpg", photos_date="2019-06-02T14:11:03")
    dt, src, spread, warns = merged_date([m1, m2], 1990, now, 2.0)
    assert dt == "2016-08-19T09:12:44" and src == "exif-dto:a.jpg"
    assert spread == 0.0 and warns == []

    # ...but capture metadata disagreeing with capture metadata warns
    m2x = _member("U2", "b.jpg", photos_date="2019-06-02T14:11:03",
                  exif={"DateTimeOriginal": "2019-01-01T00:00:00"})
    dt, src, spread, warns = merged_date([m1, m2x], 1990, now, 2.0)
    assert dt == "2016-08-19T09:12:44"
    assert spread > 800 and any(w.startswith("date-spread") for w in warns)
    # within one member too (DateTimeOriginal vs CreateDate mismatch)
    m_both = _member("U9", "c.jpg", exif={"DateTimeOriginal": "2016-08-19T09:12:44",
                                          "CreateDate": "2019-01-01T00:00:00"})
    _, _, spread, warns = merged_date([m_both], 1990, now, 2.0)
    assert spread > 800 and any(w.startswith("date-spread") for w in warns)

    m3 = _member("U3", "c.jpg", photos_date="2019-06-02T14:11:04")
    dt, src, spread, warns = merged_date([m2, m3], 1990, now, 2.0)
    assert dt == "2019-06-02T14:11:03" and src == "photos:b.jpg" and warns == []

    bogus = _member("U4", "d.jpg", photos_date="1970-01-01T00:00:00")
    dt, src, spread, warns = merged_date([bogus], 1990, now, 2.0)
    assert dt == "1970-01-01T00:00:00" and "all-dates-suspect" in warns
    dt, _, _, warns = merged_date([_member("U5", "e.jpg")], 1990, now, 2.0)
    assert dt is None and warns == ["no-dates"]

    # tranche building: shared groups merge, bridging works, singletons drop
    rows = [("U1", 10, None), ("U2", 10, None), ("U3", None, 20),
            ("U4", 11, 20), ("U5", 11, None), ("LONELY", 99, None)]
    tr = build_apple_tranches(rows)
    assert len(tr) == 2
    sizes = sorted(len(t["members"]) for t in tr)
    assert sizes == [2, 3]  # {U1,U2} and {U3,U4,U5} bridged via group 11+20
    bridged = next(t for t in tr if len(t["members"]) == 3)
    assert bridged["members"] == ["U3", "U4", "U5"]
    assert bridged["sources"] == ["apple-metadata", "apple-perceptual"]
    assert tranche_key(["b", "a"]) == tranche_key(["A", "B"])
    assert tr == sorted(tr, key=lambda t: t["key"])

    # discovery: czkawka groups join the same union-find. An Apple pair plus
    # a czkawka group holding a third copy fuse into one 3-member tranche;
    # czkawka-only groups become their own tranches.
    trd = build_apple_tranches([("U1", 10, None), ("U2", 10, None)],
                               [{"U2", "U3"}, {"X1", "X2"}, {"LONE"}])
    assert len(trd) == 2
    fused = next(t for t in trd if "U1" in t["members"])
    assert fused["members"] == ["U1", "U2", "U3"]
    assert fused["sources"] == ["apple-perceptual", "czkawka"]
    cz_only = next(t for t in trd if "X1" in t["members"])
    assert cz_only["sources"] == ["czkawka"]
    assert build_apple_tranches([("U1", 10, None), ("U2", 10, None)]) \
        == build_apple_tranches([("U1", 10, None), ("U2", 10, None)], None)

    # burst guard: czkawka-only single-burst groups drop; mixed or
    # Apple-flagged ones stay
    mem_b = {
        "X1": _member("X1", burst_key="BK1"), "X2": _member("X2", burst_key="BK1"),
        "Y1": _member("Y1", burst_key="BK2"), "Y2": _member("Y2"),
        "U1": _member("U1", burst_key="BK3"), "U2": _member("U2", burst_key="BK3"),
        "U3": _member("U3", burst_key="BK3"),
    }
    trb = build_apple_tranches([("U1", 10, None), ("U2", 10, None)],
                               [{"X1", "X2"}, {"Y1", "Y2"}, {"U2", "U3"}])
    kept, dropped = drop_burst_only_tranches(trb, mem_b)
    assert dropped == 1  # {X1,X2}: czkawka-only, one burst
    kept_sets = [set(t["members"]) for t in kept]
    assert {"Y1", "Y2"} in kept_sets          # mixed burst membership
    assert {"U1", "U2", "U3"} in kept_sets    # apple-sourced, kept regardless
    assert drop_burst_only_tranches([], {}) == ([], 0)

    # farm names round-trip; sharding is stable and uuid-derived
    assert farm_uuid(farm_name("ABC-123", "/x/y/IMG__weird__name.HEIC")) == "ABC-123"
    assert farm_uuid("abc-1__file.jpg") == "ABC-1"
    assert farm_shard("0fba8544-8aac-4b24-8d75-a4956e5efe9c") == "0F"
    assert farm_shard("ABC-1") == "AB"

    # shard chunking: batches reach the target size, remainder survives
    counts = [("a", 400), ("b", 400), ("c", 400), ("d", 400), ("e", 100)]
    batches = chunk_shards(counts, 700)
    assert [[s for s, _ in b] for b in batches] == [["a", "b"], ["c", "d"], ["e"]]
    assert parse_tools("dup,image,video") == ["dup", "image", "video"]
    assert parse_tools("video, dup") == ["dup", "video"]  # canonical order
    assert parse_tools("IMAGE") == ["image"]
    assert parse_tools("") == []
    try:
        parse_tools("dup,bogus")
    except SystemExit as err:
        assert "bogus" in str(err)
    else:
        raise AssertionError("expected SystemExit for unknown czkawka tool")
    assert _hms(45) == "45s" and _hms(3725) == "1h02m" and _hms(125) == "2m05s"

    assert chunk_shards([], 1000) == []
    assert chunk_shards([("a", 10)], 1000) == [[("a", 10)]]
    one_each = chunk_shards([("a", 500), ("b", 600)], 100)
    assert [[s for s, _ in b] for b in one_each] == [["a"], ["b"]]

    # czkawka JSON normalization: image (list) and dup (dict) shapes
    img_json = [[{"path": "/f/U1__a.jpg", "difference": 0},
                 {"path": "/f/U2__b.jpg", "difference": 3},
                 {"path": "/f/stray.jpg", "difference": 9}]]
    dup_json = {"1000": [[{"path": "/f/U1__a.jpg"}, {"path": "/f/U2__b.jpg"}]]}
    ig = czkawka_uuid_groups(img_json)
    dg = czkawka_uuid_groups(dup_json)
    assert len(ig) == 1 and set(ig[0]) == {"U1", "U2"}
    assert len(dg) == 1 and set(dg[0]) == {"U1", "U2"}

    # tiers
    mem = {"U1": _member("U1", w=100, h=100), "U2": _member("U2", w=100, h=100),
           "U3": _member("U3", w=200, h=100)}
    assert tranche_tier(["U1", "U2"], mem, dg, ig, []) == ("exact", None)
    assert tranche_tier(["U1", "U2"], mem, [], ig, []) == ("near", 3)
    ig0 = czkawka_uuid_groups([[{"path": "/f/U1__a.jpg", "difference": 0},
                                {"path": "/f/U2__b.jpg", "difference": 0}]])
    assert tranche_tier(["U1", "U2"], mem, [], ig0, []) == ("visual-0", 0)
    assert tranche_tier(["U1", "U3"], mem, [], ig0, []) == ("unverified", None)
    assert tranche_tier(["U1", "U2", "U3"], mem, [], ig, []) == ("partial", 3)
    vg = czkawka_uuid_groups([[{"path": "/f/U1__a.mov"}, {"path": "/f/U2__b.mov"}]])
    assert tranche_tier(["U1", "U2"], mem, [], [], vg) == ("video", None)
    assert suggest_approve("exact", []) and not suggest_approve("exact", ["x"])
    assert not suggest_approve("near", [])

    # WAL guard
    assert wal_refusal(1024**3, 2.0) is None
    assert "GB" in (wal_refusal(3 * 1024**3, 2.0) or "")

    # plan build: deterministic, warnings, uuid files
    scan = {
        "version": 1, "script": SCRIPT, "library": "/lib", "reader": "test",
        "generation": {"asset_count": 2, "max_pk": 2, "wal_bytes": 0},
        "near": 10,
        "apple_tranches": build_apple_tranches([("U1", 1, None), ("U2", 1, None),
                                                ("H1", 2, None), ("H2", 2, None)]),
        "members": {
            "U1": _member("U1", "a.jpg", w=200, h=200,
                          photos_date="2019-06-02T14:11:03"),
            "U2": _member("U2", "b.jpg", w=100, h=100,
                          photos_date="2019-06-02T14:11:04"),
            "H1": _member("H1", "h1.jpg", photos_date="2020-01-01T00:00:00",
                          flags=("hidden",)),
            "H2": _member("H2", "h2.jpg", photos_date="2020-01-01T00:00:01"),
        },
    }
    plan1 = build_plan(scan, dg, ig, [], 1990, 2.0, now=now)
    plan2 = build_plan(scan, dg, ig, [], 1990, 2.0, now=now)
    assert json.dumps(plan1, sort_keys=True) == json.dumps(plan2, sort_keys=True)
    by_members = {tuple(t["members"]): t for t in plan1["tranches"]}
    t12 = by_members[("U1", "U2")]
    assert t12["tier"] == "exact" and t12["keeper"] == "U1"
    assert t12["merged_date"] == "2019-06-02T14:11:03"
    assert t12["suggested"]
    th = by_members[("H1", "H2")]
    assert "unmergeable-member-hidden" in th["warnings"]
    assert "czkawka-unverified" in th["warnings"] and not th["suggested"]
    assert plan1["summary"]["tranches"] == 2
    assert plan1["summary"]["losers"] == 2
    assert plan1["tranches"][0]["id"] == 1  # exact sorts before unverified
    assert plan1["tranches"][0]["tier"] == "exact"
    losers = uuid_file_lines(plan1, "losers")
    keepers = uuid_file_lines(plan1, "keepers")
    assert "U2" in losers and "U1" in keepers
    assert any(line.startswith("# H") for line in losers)  # blocked, commented
    assert sum(1 for l in keepers if not l.startswith("#")) == 1

    # report rendering: data embedded safely, member data not inlined as HTML
    plan1_members = plan1["members"]
    plan1_members["U1"]["filename"] = '<img src=x onerror=alert(1)>.jpg'
    html_text = render_report(plan1, {"U1"})
    assert "__DATA__" not in html_text
    assert "<img src=x onerror" not in html_text  # every "<" is <-escaped
    assert "\\u003cimg src=x onerror=alert(1)>.jpg" in html_text
    assert 'type="application/json"' in html_text

    # apply worklist: decisions filtering, keeper override, live-state rules
    plan_a = build_plan(scan, dg, ig, [], 1990, 2.0, now=now)
    k12 = by_members[("U1", "U2")]["key"]
    kh = by_members[("H1", "H2")]["key"]
    live = {
        "U1": {"exists": True, "trashed": False,
               "date": "2019-06-02T14:11:03", "favorite": False},
        "U2": {"exists": True, "trashed": False,
               "date": "2019-06-02T14:11:04", "favorite": True},
        "H1": {"exists": True, "trashed": False, "date": None, "favorite": False},
        "H2": {"exists": True, "trashed": False, "date": None, "favorite": False},
    }
    plan_a["members"]["U2"]["album_uuids"] = [["AL-1", "Trip"]]
    plan_a["members"]["U2"]["keywords"] = ["beach"]
    plan_a["members"]["U2"]["title"] = "Sunset"
    dec = {k12: {"d": "approved"}, kh: {"d": "approved"}}
    work = build_apply_worklist(plan_a, dec, live)
    assert [t["key"] for t in work["tranches"]] == [k12]
    assert work["skipped"] == [{"key": kh, "reason": "unmergeable-member"}]
    assert work["dates"] == []  # keeper U1 already at the merged date
    assert [e["uuid"] for e in work["favorites"]] == ["U1"]  # U2 fav, U1 not
    assert [e["album_uuid"] for e in work["albums"]] == ["AL-1"]
    assert work["keywords"][0]["keywords"] == ["beach"]
    assert work["texts"][0]["title"] == "Sunset"
    assert [e["uuid"] for e in work["deletes"]] == ["U2"]

    # keeper drifted by >2s -> date write with old date recorded
    live2 = dict(live, U1={"exists": True, "trashed": False,
                           "date": "2019-06-02T14:11:09", "favorite": False})
    work2 = build_apply_worklist(plan_a, dec, live2)
    assert work2["dates"] == [{"tranche": k12, "uuid": "U1",
                               "date": "2019-06-02T14:11:03",
                               "utc_offset": None,
                               "old_date": "2019-06-02T14:11:09"}]
    # the keeper's own UTC offset rides along so the writer can rebuild the
    # correct instant instead of assuming the machine's zone
    live_tz = dict(live2)
    live_tz["U1"] = dict(live2["U1"], tz_offset=3600)
    assert build_apply_worklist(plan_a, dec, live_tz)["dates"][0]["utc_offset"] == 3600
    # within tolerance -> no write
    live3 = dict(live, U1={"exists": True, "trashed": False,
                           "date": "2019-06-02T14:11:04", "favorite": False})
    assert build_apply_worklist(plan_a, dec, live3)["dates"] == []
    # a member vanished since the plan -> tranche skipped
    live4 = dict(live, U2={"exists": False, "trashed": False,
                           "date": None, "favorite": False})
    work4 = build_apply_worklist(plan_a, dec, live4)
    assert work4["tranches"] == []
    assert any(s["key"] == k12 and "member-gone:U2" in s["reason"]
               for s in work4["skipped"])
    # keeper override via decisions; invalid override refuses
    work5 = build_apply_worklist(plan_a, {k12: {"d": "approved", "k": "U2"}}, live)
    assert work5["tranches"][0]["keeper"] == "U2"
    assert [e["uuid"] for e in work5["deletes"]] == ["U1"]
    try:
        build_apply_worklist(plan_a, {k12: {"d": "approved", "k": "NOPE"}}, live)
    except SystemExit as err:
        assert "not a member" in str(err)
    else:
        raise AssertionError("expected SystemExit for foreign keeper override")
    # rejected/undecided tranches are not selected at all
    assert build_apply_worklist(plan_a, {k12: {"d": "rejected"}}, live)["tranches"] == []
    assert build_apply_worklist(plan_a, {}, live)["tranches"] == []
    # excluded members: not deleted, and their albums/keywords stay out
    plan_x = json.loads(json.dumps(plan_a))
    tx = next(t for t in plan_x["tranches"] if t["key"] == k12)
    tx["members"] = ["U1", "U2", "U3"]
    plan_x["members"]["U3"] = _member("U3", "intruder.jpg", w=200, h=200,
                                      photos_date="2019-06-02T14:11:05")
    plan_x["members"]["U3"]["album_uuids"] = [["AL-9", "Wrong"]]
    live_x = dict(live, U3={"exists": True, "trashed": False,
                            "date": "2019-06-02T14:11:05", "favorite": False})
    wx = build_apply_worklist(plan_x, {k12: {"d": "approved", "x": ["U3"]}}, live_x)
    assert [e["uuid"] for e in wx["deletes"]] == ["U2"], wx["deletes"]
    assert wx["tranches"][0]["excluded"] == ["U3"]
    assert "AL-9" not in [e["album_uuid"] for e in wx["albums"]]
    # a vanished EXCLUDED member must not block the tranche
    live_gone = dict(live_x, U3={"exists": False, "trashed": False,
                                 "date": None, "favorite": False})
    assert build_apply_worklist(
        plan_x, {k12: {"d": "approved", "x": ["U3"]}}, live_gone)["tranches"]
    # excluding everything but the keeper collapses the tranche
    wc = build_apply_worklist(
        plan_x, {k12: {"d": "approved", "x": ["U2", "U3"]}}, live_x)
    assert wc["tranches"] == [] and wc["skipped"][0]["reason"] == "excluded-collapsed"
    # verify ignores excluded members when judging completeness
    assert verify_tranche(tx, "U1", live_x, {"U3"}) == "losers-pending"
    live_done = dict(live_x, U2={"exists": False, "trashed": False,
                                 "date": None, "favorite": False})
    assert verify_tranche(tx, "U1", live_done, {"U3"}) == "complete"
    assert verify_tranche(tx, "U1", live_done, set()) == "losers-pending"

    # mixed orientation: a duplicate keeps its shape
    port = _member("P", "p.mov", w=1080, h=1920)
    land = _member("L", "l.mp4", w=640, h=352)
    assert mixed_orientation([port, land])
    assert not mixed_orientation([port, _member("P2", "p2.mov", w=1308, h=1744)])
    assert not mixed_orientation([port, _member("N", "n.mov", w=0, h=0)])
    assert not mixed_orientation([_member("S", "s.jpg", w=100, h=100), port])

    # skip_photoscript drops album/keyword/text work but keeps deletes
    work6 = build_apply_worklist(plan_a, dec, live, skip_photoscript=True)
    assert work6["albums"] == [] and work6["keywords"] == [] and work6["texts"] == []
    assert [e["uuid"] for e in work6["deletes"]] == ["U2"]

    # verify states
    t12_t = next(t for t, _ in approved_tranches(plan_a, dec) if t["key"] == k12)
    assert verify_tranche(t12_t, "U1", live) == "losers-pending"
    live_done = dict(live, U2={"exists": False, "trashed": False,
                               "date": None, "favorite": False})
    assert verify_tranche(t12_t, "U1", live_done) == "complete"
    assert verify_tranche(t12_t, "U1", live2) == "pending"
    live_del = dict(live2, U2={"exists": True, "trashed": True,
                               "date": None, "favorite": False})
    assert verify_tranche(t12_t, "U1", live_del) == "date-pending"
    assert verify_tranche(t12_t, "U1",
                          dict(live, U1={"exists": False, "trashed": False,
                                         "date": None, "favorite": False})
                          ) == "keeper-missing"
    assert _dt_close("2020-01-01T00:00:00", "2020-01-01T00:00:02")
    assert not _dt_close("2020-01-01T00:00:00", "2020-01-01T00:00:03")
    assert _dt_close(None, None) and not _dt_close(None, "2020-01-01T00:00:00")

    # reveal endpoint gatekeeping + template tripwires for the UI features
    good = "0FBA8544-8AAC-4B24-8D75-A4956E5EFE9C"
    assert _reveal_allowed(good, {good})
    assert _reveal_allowed(good.lower() + "/l0/001", {good})  # AppleScript form
    assert not _reveal_allowed(good, set())  # well-formed but not in the plan
    assert not _reveal_allowed('"; do shell script "x"; "', {good})
    assert not _reveal_allowed("", {""})
    assert not _reveal_allowed(None, {good})
    # HTTP status lines are encoded latin-1, and AppleScript errors carry
    # curly quotes ("Can't get media item..."), which raised
    # UnicodeEncodeError and killed the request thread when passed as a
    # reason phrase. Detail must travel in a JSON body, which json.dumps
    # keeps ASCII-only, so it is always encodable.
    curly = "Photos got an error: Can’t get media item id “x”"
    assert json.dumps({"error": curly}).encode().decode("latin-1")
    try:
        curly.encode("latin-1")
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("expected the raw message to be latin-1 hostile")
    for needle in ('case "j"', "/reveal?uuid=", "keydown", 'id="help"',
                   "setKeeper", "focusTo", 'id="helpbtn"', "/decisions",
                   'id="status"', "downloadDecisions", "renderKeeperLine",
                   "saved_decisions", "keeper_reason", "function displayOrder"):
        assert needle in REPORT_TEMPLATE, needle

    # saved decisions carry across sessions; junk files degrade to {}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        assert load_saved_decisions(tdp) == {}  # no file yet
        (tdp / "decisions.json").write_text(json.dumps(
            {"version": 1, "plan_key": "abc", "decisions": {"t1": {"d": "approved"}}}))
        assert load_saved_decisions(tdp) == {"t1": {"d": "approved"}}
        (tdp / "decisions.json").write_text("not json{")
        assert load_saved_decisions(tdp) == {}
    assert _valid_decisions_payload({"version": 1, "decisions": {}})
    assert not _valid_decisions_payload({"version": 2, "decisions": {}})
    assert not _valid_decisions_payload({"version": 1, "decisions": []})
    assert not _valid_decisions_payload({"version": 1})
    assert not _valid_decisions_payload([])
    assert not _valid_decisions_payload(None)

    # false-positive guards: a static camera makes consecutive clips look
    # alike to a frame hasher, but they are a series, not duplicates
    grmn = [f"GRMN{n}.MP4" for n in range(1639, 1652)]
    assert sequential_filenames(grmn)
    assert sequential_filenames(["IMG_1001.jpg", "IMG_1002.jpg", "IMG_1003.jpg"])
    assert not sequential_filenames(["a.jpg", "a-2.jpg"])          # too few
    assert not sequential_filenames(["a.jpg", "a-2.jpg", "a-3.jpg"])  # same stem "a-"
    assert not sequential_filenames(["IMG_1.jpg", "DSC_2.jpg", "PIC_3.jpg"])
    assert not sequential_filenames(["x1.jpg", "x1.jpg", "x1.jpg"])  # repeats
    assert not sequential_filenames([])

    seq = [_member(f"U{i}", f"GRMN{1639+i}.MP4",
                   photos_date=f"2026-06-26T08:5{i}:03") for i in range(4)]
    assert distinct_capture_times(seq, 1990, now)
    same_moment = [_member(x, f"{x}.jpg", photos_date="2020-01-01T00:00:00")
                   for x in "ABC"]
    assert not distinct_capture_times(same_moment, 1990, now)  # true duplicates
    # a PAIR with differing dates is an ordinary re-import, not a series
    pair = [_member("A", "a.jpg", photos_date="2019-06-02T14:11:03"),
            _member("B", "b.jpg", photos_date="2021-01-01T00:00:00")]
    assert not distinct_capture_times(pair, 1990, now)
    assert not distinct_capture_times(
        [_member("A", "a.jpg"), _member("B", "b.jpg")], 1990, now)  # no dates

    scan_seq = {
        **scan,
        "apple_tranches": build_apple_tranches(
            [(f"U{i}", 7, None) for i in range(4)]),
        "members": {f"U{i}": seq[i] for i in range(4)},
    }
    plan_seq = build_plan(scan_seq, [], [], [], 1990, 2.0, now=now)
    wseq = plan_seq["tranches"][0]["warnings"]
    assert "sequential-filenames" in wseq and "distinct-capture-times" in wseq
    assert not plan_seq["tranches"][0]["suggested"]

    # burst + mixed-media warnings
    scan2 = {
        **scan,
        "apple_tranches": build_apple_tranches([("B1", 5, None), ("B2", 5, None)]),
        "members": {
            "B1": _member("B1", "b1.jpg", photos_date="2020-01-01T00:00:00",
                          burst_key="BK", flags=("video",)),
            "B2": _member("B2", "b2.jpg", photos_date="2020-01-01T00:00:01",
                          burst_key="BK"),
        },
    }
    planb = build_plan(scan2, [], [], [], 1990, 2.0, now=now)
    wb = planb["tranches"][0]["warnings"]
    assert "burst-mates" in wb and "mixed-media" in wb

    print("selftest OK")


if __name__ == "__main__":
    raise SystemExit(main())
