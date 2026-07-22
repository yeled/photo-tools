"""ASCII histogram of Photos library dates, for hunting bulk bad-date spikes.

A batch of photos all stamped with the same wrong day (like an export bug
writing Dec 24/25 on thousands of images) shows up as a giant spike in the
per-day counts. This tool graphs the distribution and lets you drill into a
day to get the photo UUIDs, which osxphotos can turn into a Photos.app album
or feed straight into timewarp.

Read-only: everything comes from the Photos library database via osxphotos --
no AppleScript, and nothing is modified. Output is plain deterministic text,
so runs can be saved and diffed over time.

Note the database holds MORE than the library grid shows: hidden photos,
shared-album photos, and "Shared with You" items that Messages feeds into
Photos without them ever being saved to the library (those keep their original
EXIF dates, so they scatter across history). Such photos are invisible to
AppleScript, which is why `osxphotos show UUID` says "could not find asset"
for them and timewarp cannot address them. The drill-down flags each one
(e.g. [shared-with-you], [hidden]), --uuid-file writes them commented out so
downstream commands skip them, and --visible-only excludes them everywhere.

Run it with osxphotos' own Python so the package is importable:

    osxphotos run graph_photo_dates.py                       # monthly overview + top spike days
    osxphotos run graph_photo_dates.py --bucket day --year 2025
    osxphotos run graph_photo_dates.py --day 2025-12-24      # list that day's photos
    osxphotos run graph_photo_dates.py --day 2025-12-24 --uuid-file spike.txt

From a UUID file you can then, without selecting anything in Photos:

    # collect the photos into an album to look at in Photos.app
    osxphotos query --uuid-from-file spike.txt --add-to-album "Spike 2025-12-24"

    # or fix their dates directly (oldest photo becomes the reference)
    TIMEWARP_UUID_FILE=spike.txt osxphotos timewarp --uuid-from-file spike.txt \
        --function timewarp_from_reference.py::get_date_time_timezone --verbose

Options: --field added graphs import (added) dates instead of photo dates;
--bucket day|month|year sets chart granularity; --year / --from / --to
restrict the range; --top N sizes the spike-day list; --log log-scales the
bars so normal months stay visible next to a huge spike; --width N sets bar
width; --color always|never overrides flag coloring (auto colors only on a
terminal and honors NO_COLOR, so redirected output stays plain and diffable);
--library PATH reads a specific library instead of the default; --selftest
runs the offline tests (safe on any machine).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

BAR_CHAR = "█"  # full block
DEFAULT_WIDTH = 50
DEFAULT_TOP = 15
SCRIPT = "graph_photo_dates.py"


# reasons a photo does not appear in the main library grid (and so cannot be
# addressed via AppleScript by osxphotos show / timewarp); "missing" is not one
VISIBILITY_FLAGS = ("shared-with-you", "hidden", "shared-album", "trash")

ANSI_RESET = "\x1b[0m"
FLAG_COLORS = {
    "shared-with-you": "\x1b[36m",  # cyan
    "hidden": "\x1b[33m",  # yellow
    "shared-album": "\x1b[34m",  # blue
    "trash": "\x1b[31m",  # red
    "missing": "\x1b[35m",  # magenta
}


def color_enabled(mode: str, stream) -> bool:
    """--color always/never wins; auto means "stream is a terminal and
    NO_COLOR is unset", so redirected output stays plain and diffable."""
    if mode == "always":
        return True
    if mode == "never" or os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def colorize_flag(flag: str, enabled: bool) -> str:
    if not enabled:
        return flag
    color = FLAG_COLORS.get(flag)
    return f"{color}{flag}{ANSI_RESET}" if color else flag


@dataclass
class PhotoRow:
    uuid: str
    filename: str
    date: Optional[datetime]
    date_added: Optional[datetime]
    flags: Tuple[str, ...] = ()


def photo_flags(photo) -> Tuple[str, ...]:
    """Classify why a PhotoInfo may not be where you expect it.

    getattr guards keep this working across osxphotos versions that predate
    some of the properties."""
    flags = []
    if getattr(photo, "syndicated", None) and not getattr(photo, "saved_to_library", True):
        flags.append("shared-with-you")
    if getattr(photo, "hidden", False):
        flags.append("hidden")
    if getattr(photo, "shared", False):
        flags.append("shared-album")
    if getattr(photo, "intrash", False):
        flags.append("trash")
    if getattr(photo, "ismissing", False):
        flags.append("missing")
    return tuple(flags)


def uuid_file_lines(items: List[Tuple["PhotoRow", datetime]]) -> List[str]:
    """UUID-file body: photos AppleScript can't address are commented out so
    `--uuid-from-file` consumers skip them."""
    lines = []
    for row, _ in items:
        unaddressable = [flag for flag in row.flags if flag in VISIBILITY_FLAGS]
        if unaddressable:
            lines.append(f"# {row.uuid}  ({', '.join(unaddressable)} -- not addressable in Photos)")
        else:
            lines.append(row.uuid)
    return lines


def parse_day(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {text!r} (expected YYYY-MM-DD)")


def bucket_key(day: date, bucket: str) -> date:
    if bucket == "day":
        return day
    if bucket == "month":
        return day.replace(day=1)
    if bucket == "year":
        return date(day.year, 1, 1)
    raise ValueError(f"unknown bucket {bucket!r}")


def bucket_label(key: date, bucket: str) -> str:
    if bucket == "day":
        return key.isoformat()
    if bucket == "month":
        return f"{key.year:04d}-{key.month:02d}"
    return f"{key.year:04d}"


def next_bucket(key: date, bucket: str) -> date:
    if bucket == "day":
        return key + timedelta(days=1)
    if bucket == "month":
        if key.month == 12:
            return date(key.year + 1, 1, 1)
        return date(key.year, key.month + 1, 1)
    return date(key.year + 1, 1, 1)


def bar_length(count: int, max_count: int, width: int, log_scale: bool) -> int:
    """Bar length in characters; any nonzero count gets at least one."""
    if count <= 0 or max_count <= 0:
        return 0
    if log_scale:
        frac = math.log10(count + 1) / math.log10(max_count + 1)
    else:
        frac = count / max_count
    return max(1, min(width, round(frac * width)))


def render_histogram(
    counts: Dict[date, int], bucket: str, width: int, log_scale: bool
) -> List[str]:
    """One line per bucket from first to last, empty buckets included --
    gaps are information when you're looking at import behavior."""
    if not counts:
        return []
    max_count = max(counts.values())
    count_w = len(f"{max_count:,}")
    lines = []
    key = min(counts)
    last = max(counts)
    while key <= last:
        count = counts.get(key, 0)
        bar = BAR_CHAR * bar_length(count, max_count, width, log_scale)
        lines.append(f"{bucket_label(key, bucket)}  {bar:<{width}}  {count:>{count_w},}")
        key = next_bucket(key, bucket)
    return lines


def top_days(day_counts: Dict[date, int], n: int) -> List[Tuple[date, int]]:
    return sorted(day_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def load_photos(library: Optional[str]) -> Tuple[str, List[PhotoRow]]:
    import osxphotos  # deferred so --selftest runs anywhere

    db = osxphotos.PhotosDB(dbfile=library) if library else osxphotos.PhotosDB()
    rows = [
        PhotoRow(
            uuid=photo.uuid,
            filename=photo.original_filename or photo.filename or "",
            date=photo.date,
            date_added=photo.date_added,
            flags=photo_flags(photo),
        )
        for photo in db.photos()
    ]
    return db.library_path, rows


def field_datetime(row: PhotoRow, field: str) -> Optional[datetime]:
    return row.date if field == "date" else row.date_added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="ASCII histogram of Photos library dates; see the module "
        "docstring for the full workflow.",
    )
    parser.add_argument(
        "--field",
        choices=("date", "added"),
        default="date",
        help="timestamp to graph: photo date (default) or import (added) date",
    )
    parser.add_argument(
        "--bucket",
        choices=("day", "month", "year"),
        default="month",
        help="chart granularity (default: month)",
    )
    parser.add_argument("--year", type=int, help="shorthand for --from/--to of one year")
    parser.add_argument("--from", dest="from_date", type=parse_day, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=parse_day, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--day",
        type=parse_day,
        metavar="YYYY-MM-DD",
        help="drill down: list every photo on this day instead of charting",
    )
    parser.add_argument(
        "--uuid-file",
        metavar="PATH",
        help="with --day: write the day's photo UUIDs to PATH (one per line)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"how many spike days to list under the chart (default {DEFAULT_TOP}, 0 to disable)",
    )
    parser.add_argument(
        "--visible-only",
        action="store_true",
        help="only photos visible in the main library grid (exclude hidden, "
        "shared-album, and unsaved Shared-with-You items)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="color the annotation flags (default auto: only on a terminal, "
        "honoring NO_COLOR)",
    )
    parser.add_argument("--log", action="store_true", help="log-scale the bars")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="bar width in characters")
    parser.add_argument("--library", metavar="PATH", help="path to a Photos library (default: last opened)")
    parser.add_argument("--selftest", action="store_true", help="run offline self-tests and exit")
    return parser


def drill_down(
    items: List[Tuple[PhotoRow, datetime]],
    day: date,
    field: str,
    uuid_file: Optional[str],
    color: bool = False,
) -> None:
    if not items:
        print(f"No photos on {day.isoformat()} (field: {field}).")
        return
    items.sort(key=lambda item: (item[1], item[0].filename))
    print(f"{len(items):,} photo(s) on {day.isoformat()} (field: {field})\n")
    name_w = min(36, max(len(row.filename) for row, _ in items))
    for row, dt in items:
        added = row.date_added.date().isoformat() if row.date_added else "-"
        flag_str = (
            f"  [{', '.join(colorize_flag(flag, color) for flag in row.flags)}]"
            if row.flags
            else ""
        )
        print(f"{dt:%H:%M:%S}  {row.filename:<{name_w}}  added {added}  {row.uuid}{flag_str}")
    addressable = [
        (row, dt)
        for row, dt in items
        if not any(flag in VISIBILITY_FLAGS for flag in row.flags)
    ]
    if len(addressable) < len(items):
        print(
            f"\n{len(items) - len(addressable):,} of these are outside the main library "
            "grid (see flags) -- osxphotos show/timewarp cannot address those."
        )
    if addressable:
        print(f"\nReveal one in Photos.app:  osxphotos show {addressable[0][0].uuid}")
    if uuid_file:
        with open(uuid_file, "w") as fh:
            fh.write(f"# {len(items)} photos dated {day.isoformat()} (field: {field})\n")
            for line in uuid_file_lines(items):
                fh.write(line + "\n")
        print(
            f"\nWrote {len(addressable):,} usable UUID(s) to {uuid_file}"
            + (
                f" ({len(items) - len(addressable):,} unaddressable ones commented out)"
                if len(addressable) < len(items)
                else ""
            )
            + ". Next steps:"
        )
        print(
            f'  osxphotos query --uuid-from-file {uuid_file} '
            f'--add-to-album "Spike {day.isoformat()}"'
        )
        print(
            f"  TIMEWARP_UUID_FILE={uuid_file} osxphotos timewarp "
            f"--uuid-from-file {uuid_file} \\\n"
            "      --function timewarp_from_reference.py::get_date_time_timezone --verbose"
        )
    else:
        print("Tip: add --uuid-file spike.txt to write these UUIDs for osxphotos query/timewarp.")


def overview(pairs: List[Tuple[PhotoRow, datetime]], args: argparse.Namespace) -> None:
    counts = Counter(bucket_key(dt.date(), args.bucket) for _, dt in pairs)
    for line in render_histogram(counts, args.bucket, args.width, args.log):
        print(line)
    if args.top <= 0:
        return
    day_counts = Counter(dt.date() for _, dt in pairs)
    tops = top_days(day_counts, args.top)
    total = len(pairs)
    count_w = len(f"{tops[0][1]:,}")
    print(f"\nTop {len(tops)} day(s) by count (field: {args.field}):")
    for rank, (day, count) in enumerate(tops, start=1):
        print(f"{rank:>3}. {day.isoformat()}  {count:>{count_w},}  ({count / total * 100:4.1f}%)")
    field_arg = " --field added" if args.field == "added" else ""
    print(f"\nDrill down:  osxphotos run {SCRIPT} --day {tops[0][0].isoformat()}{field_arg}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if args.uuid_file and not args.day:
        parser.error("--uuid-file requires --day")
    if args.year and (args.from_date or args.to_date):
        parser.error("--year cannot be combined with --from/--to")
    if args.year:
        args.from_date = date(args.year, 1, 1)
        args.to_date = date(args.year, 12, 31)

    library, rows = load_photos(args.library)
    print(f"Library: {library}")

    pairs = []
    undated = 0
    for row in rows:
        dt = field_datetime(row, args.field)
        if dt is None:
            undated += 1
            continue
        day = dt.date()
        if args.from_date and day < args.from_date:
            continue
        if args.to_date and day > args.to_date:
            continue
        if args.visible_only and any(flag in VISIBILITY_FLAGS for flag in row.flags):
            continue
        pairs.append((row, dt))

    if undated:
        print(f"{undated:,} photo(s) with no {args.field} value skipped")
    if not pairs:
        print(f"No photos in range (field: {args.field}).")
        return 0
    first = min(dt for _, dt in pairs).date().isoformat()
    last = max(dt for _, dt in pairs).date().isoformat()
    print(f"{len(pairs):,} photo(s) from {first} to {last} (field: {args.field})")
    color = color_enabled(args.color, sys.stdout)
    flag_totals = Counter(flag for row, _ in pairs for flag in row.flags)
    if flag_totals:
        summary = ", ".join(
            f"{count:,} {colorize_flag(flag, color)}"
            for flag, count in sorted(flag_totals.items(), key=lambda kv: -kv[1])
        )
        print(f"Flagged in range: {summary}")
        print(
            "(all but 'missing' live outside the main library grid -- invisible "
            "to osxphotos show/timewarp; --visible-only excludes them)"
        )
    print()

    if args.day:
        drill_down([(row, dt) for row, dt in pairs if dt.date() == args.day],
                   args.day, args.field, args.uuid_file, color)
    else:
        overview(pairs, args)
    return 0


def selftest() -> None:
    assert bucket_key(date(2025, 12, 24), "day") == date(2025, 12, 24)
    assert bucket_key(date(2025, 12, 24), "month") == date(2025, 12, 1)
    assert bucket_key(date(2025, 12, 24), "year") == date(2025, 1, 1)
    assert bucket_label(date(2025, 12, 1), "month") == "2025-12"
    assert bucket_label(date(2025, 12, 24), "day") == "2025-12-24"
    assert bucket_label(date(2025, 1, 1), "year") == "2025"
    assert next_bucket(date(2025, 12, 1), "month") == date(2026, 1, 1)
    assert next_bucket(date(2025, 11, 1), "month") == date(2025, 12, 1)
    assert next_bucket(date(2025, 12, 31), "day") == date(2026, 1, 1)
    assert next_bucket(date(2025, 1, 1), "year") == date(2026, 1, 1)

    assert bar_length(0, 100, 50, False) == 0
    assert bar_length(1, 10000, 50, False) == 1  # nonzero always visible
    assert bar_length(100, 100, 50, False) == 50
    assert bar_length(100, 100, 50, True) == 50
    assert 1 <= bar_length(10, 10000, 50, True) <= 50
    assert bar_length(10, 10000, 50, True) >= bar_length(10, 10000, 50, False)

    counts = {date(2025, 11, 1): 2, date(2026, 1, 1): 5}
    lines = render_histogram(counts, "month", 10, False)
    assert len(lines) == 3  # empty December included
    assert lines[0].startswith("2025-11") and lines[1].startswith("2025-12")
    assert lines[1].split()[-1] == "0"
    assert lines[2].startswith("2026-01") and BAR_CHAR * 10 in lines[2]
    assert render_histogram({}, "month", 10, False) == []

    tops = top_days({date(2025, 1, 2): 5, date(2025, 1, 1): 5, date(2025, 1, 3): 9}, 2)
    assert tops == [(date(2025, 1, 3), 9), (date(2025, 1, 1), 5)]  # tie -> earlier date

    assert parse_day(" 2025-12-24 ") == date(2025, 12, 24)
    try:
        parse_day("24/12/2025")
    except argparse.ArgumentTypeError:
        pass
    else:
        raise AssertionError("expected ArgumentTypeError for bad date")

    row = PhotoRow("U", "f.jpg", datetime(2025, 1, 1), None)
    assert field_datetime(row, "date") == datetime(2025, 1, 1)
    assert field_datetime(row, "added") is None

    from types import SimpleNamespace

    unsaved = SimpleNamespace(
        syndicated=True, saved_to_library=False, hidden=False,
        shared=False, intrash=False, ismissing=False,
    )
    assert photo_flags(unsaved) == ("shared-with-you",)
    saved = SimpleNamespace(
        syndicated=True, saved_to_library=True, hidden=True,
        shared=True, intrash=False, ismissing=True,
    )
    assert photo_flags(saved) == ("hidden", "shared-album", "missing")
    assert photo_flags(SimpleNamespace()) == ()  # older osxphotos: no such attrs

    when = datetime(2025, 1, 1)
    items = [
        (PhotoRow("U1", "a.jpg", when, None, ("shared-with-you",)), when),
        (PhotoRow("U2", "b.jpg", when, None, ()), when),
        (PhotoRow("U3", "c.jpg", when, None, ("missing",)), when),
    ]
    lines = uuid_file_lines(items)
    assert lines[0].startswith("# U1") and "shared-with-you" in lines[0]
    assert lines[1] == "U2"
    assert lines[2] == "U3"  # missing originals are still addressable in Photos

    assert colorize_flag("hidden", False) == "hidden"
    assert colorize_flag("hidden", True) == f"\x1b[33mhidden{ANSI_RESET}"
    assert colorize_flag("unknown-flag", True) == "unknown-flag"
    tty = SimpleNamespace(isatty=lambda: True)
    pipe = SimpleNamespace(isatty=lambda: False)
    no_color_before = os.environ.pop("NO_COLOR", None)
    try:
        assert color_enabled("always", pipe) is True
        assert color_enabled("never", tty) is False
        assert color_enabled("auto", tty) is True
        assert color_enabled("auto", pipe) is False
        os.environ["NO_COLOR"] = "1"
        assert color_enabled("auto", tty) is False
        assert color_enabled("always", tty) is True
    finally:
        os.environ.pop("NO_COLOR", None)
        if no_color_before is not None:
            os.environ["NO_COLOR"] = no_color_before

    print("selftest OK")


if __name__ == "__main__":
    raise SystemExit(main())
