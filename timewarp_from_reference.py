"""Copy the date/time of one "reference" photo to the other selected photos.

For use with `osxphotos timewarp --function`
(https://github.com/RhetTbull/osxphotos, motivation in osxphotos issue #2179):

    osxphotos timewarp --function timewarp_from_reference.py::get_date_time_timezone --verbose

If a batch of photos ended up with bogus dates (e.g. a bad export/import),
select them in Photos together with one photo whose date is known good, and
this function sets every other selected photo to the reference photo's
date/time, spaced out by a fixed delta per photo so they keep a stable order.

By default the reference is the OLDEST selected photo (earliest current
date/time): when a bug stamps a batch with too-new dates, the one photo whose
date survived intact is the oldest of the batch. To pick the reference
yourself, name it explicitly:

    TIMEWARP_REF="IMG_1234.jpg" osxphotos timewarp \
        --function timewarp_from_reference.py::get_date_time_timezone --verbose

Dates and filenames are read straight from the Photos library database via
osxphotos' PhotosDB (fast, no per-photo AppleScript). AppleScript is used for
exactly one thing here: asking Photos which photos are selected (a single
call), because the selection is UI state that lives in no database. Note that
timewarp itself still writes the new dates back one photo at a time through
AppleScript -- that part belongs to osxphotos, not to this function.

Configuration (environment variables):

    TIMEWARP_REF    Which selected photo is the reference:
                    - unset or "oldest" (default): the photo with the earliest
                      date; ties go to the first as reported by Photos
                    - "first": the first photo of the selection as reported by
                      Photos -- that is library order, NOT the order you
                      clicked, so use with care
                    - anything else: filename ("IMG_1234.jpg", extension
                      optional) or UUID of the reference photo (if a photo is
                      literally named "oldest" or "first", use its UUID)

    TIMEWARP_DELTA  Spacing between successive photos: "1s" (default), "0",
                    "90" (plain numbers are seconds), "2m", "1h30m", "1d",
                    "-10s". With the default the photos land at reference
                    time +1s, +2s, ... so they sort stably right after the
                    reference photo; use 0 to give every photo exactly the
                    reference date/time.

    TIMEWARP_ORDER  Order in which the delta increments are handed out:
                    "filename" (default, natural sort so IMG_2 < IMG_10),
                    "date" (the photos' current, pre-fix date order), or
                    "selection" (the order Photos reports the selection).

    TIMEWARP_READER "db" (default): read dates/filenames from the Photos
                    library database via osxphotos, falling back to
                    AppleScript automatically if the database cannot be read
                    or does not contain the selected photos (e.g. a different
                    library is open in Photos than the last-opened one the
                    database reader finds). "applescript": force the old
                    per-photo AppleScript reads.

The reference photo itself is written back unchanged. Timezones are left
untouched for every photo; chain `osxphotos timewarp --timezone` afterwards if
those need fixing too. The selection is snapshotted on the first call and all
new dates are computed up front, keyed by photo UUID, so the order in which
timewarp walks the photos does not matter -- but don't change the selection
while it runs. Loading the library database takes a moment for very large
libraries; after that the plan is instant.

Run `python3 timewarp_from_reference.py` for offline self-tests (safe on any
machine; does not touch Photos).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from photoscript import Photo

REF_ENV = "TIMEWARP_REF"
DELTA_ENV = "TIMEWARP_DELTA"
ORDER_ENV = "TIMEWARP_ORDER"
READER_ENV = "TIMEWARP_READER"

DEFAULT_DELTA = "1s"

_DELTA_RE = re.compile(
    r"(?:(?P<days>\d+(?:\.\d+)?)d)?"
    r"(?:(?P<hours>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)m)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)s?)?"
)


def parse_delta(value: str) -> timedelta:
    """Parse "1s", "90", "2m", "1h30m", "1d", "-10s" into a timedelta."""
    text = value.strip().lower().replace(" ", "")
    sign = 1
    if text.startswith(("+", "-")):
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    match = _DELTA_RE.fullmatch(text) if text else None
    if not match or not any(match.groupdict().values()):
        raise ValueError(
            f"invalid {DELTA_ENV} value {value!r} "
            '(examples: "0", "1s", "90", "2m", "1h30m", "1d", "-10s")'
        )
    parts = {unit: float(num) for unit, num in match.groupdict().items() if num}
    return sign * timedelta(**parts)


def _natural_key(name: str) -> List[object]:
    return [
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", name.lower())
    ]


def _norm_uuid(raw: str) -> str:
    """AppleScript photo ids look like "UUID/L0/001"; reduce to the plain UUID."""
    return raw.split("/")[0].upper()


@dataclass
class _PhotoRec:
    """What the planner needs to know about one selected photo.

    uuid must already be normalized via _norm_uuid; date is naive local time
    (the same convention AppleScript and timewarp use).
    """

    uuid: str
    filename: str
    date: Optional[datetime]


def _find_reference(records: List[_PhotoRec], spec: str) -> _PhotoRec:
    spec_l = spec.lower()
    matches = [
        rec
        for rec in records
        if rec.uuid.lower() == spec_l
        or (rec.filename or "").lower() == spec_l
        or os.path.splitext(rec.filename or "")[0].lower() == spec_l
    ]
    if not matches:
        names = ", ".join(rec.filename or rec.uuid for rec in records)
        raise ValueError(
            f"{REF_ENV}={spec!r} does not match any selected photo; selected: {names}"
        )
    if len(matches) > 1:
        uuids = ", ".join(rec.uuid for rec in matches)
        raise ValueError(
            f"{REF_ENV}={spec!r} matches more than one selected photo ({uuids}); "
            "use the photo's UUID instead"
        )
    return matches[0]


@dataclass
class _Plan:
    ref_uuid: str
    ref_filename: str
    ref_date: datetime
    new_dates: Dict[str, datetime]  # target photo uuid -> new date
    filenames: Dict[str, str]  # photo uuid -> filename, for logging
    old_dates: Dict[str, Optional[datetime]]  # photo uuid -> pre-fix date


def _plan_from_records(records: List[_PhotoRec], verbose: Callable) -> _Plan:
    if not records:
        raise ValueError("no photos selected in Photos")

    ref_spec = os.environ.get(REF_ENV, "").strip()
    spec_l = ref_spec.lower()
    if not spec_l or spec_l == "oldest":
        dated = [rec for rec in records if rec.date is not None]
        if not dated:
            raise ValueError("none of the selected photos has a date")
        ref = min(dated, key=lambda rec: rec.date)
        verbose(
            f"Using the oldest selected photo as reference: {ref.filename} at {ref.date}"
        )
    elif spec_l == "first":
        ref = records[0]
        verbose(
            f"{REF_ENV}=first: using the first photo of the selection as "
            f"reported by Photos (library order, not click order): {ref.filename}"
        )
    else:
        ref = _find_reference(records, ref_spec)

    if ref.date is None:
        raise ValueError(f"reference photo {ref.filename} has no date")

    order = os.environ.get(ORDER_ENV, "").strip().lower() or "filename"
    targets = [rec for rec in records if rec.uuid != ref.uuid]
    if order == "filename":
        targets.sort(key=lambda rec: _natural_key(rec.filename or ""))
    elif order == "date":
        targets.sort(
            key=lambda rec: (
                rec.date is None,
                rec.date or datetime.min,
                _natural_key(rec.filename or ""),
            )
        )
    elif order != "selection":
        raise ValueError(
            f'invalid {ORDER_ENV} value {order!r} (use "filename", "date" or "selection")'
        )

    delta = parse_delta(os.environ.get(DELTA_ENV, "").strip() or DEFAULT_DELTA)

    new_dates = {
        rec.uuid: ref.date + i * delta for i, rec in enumerate(targets, start=1)
    }
    verbose(
        f"Reference {ref.filename} ({ref.uuid}) at {ref.date}; applying to "
        f"{len(targets)} photo(s) with delta {delta} per photo in {order} order"
    )
    return _Plan(
        ref_uuid=ref.uuid,
        ref_filename=ref.filename or "",
        ref_date=ref.date,
        new_dates=new_dates,
        filenames={rec.uuid: rec.filename for rec in records},
        old_dates={rec.uuid: rec.date for rec in records},
    )


def _selected_uuids(verbose: Callable) -> List[str]:
    """UUIDs of the photos selected in Photos, in the order Photos reports.

    Uses photoscript's single bulk AppleScript call when possible; building
    photoscript Photo objects instead would cost one AppleScript round trip
    per photo just to validate each UUID.
    """
    try:
        from photoscript.script_loader import run_script

        raw = run_script("photosLibraryGetSelection")
        if isinstance(raw, str):
            raw = [raw]
        return [_norm_uuid(item) for item in raw]
    except Exception as err:  # photoscript internals moved? fall back, slower
        verbose(f"bulk selection fetch failed ({err}); using photoscript objects")
        import photoscript

        return [_norm_uuid(photo.uuid) for photo in photoscript.PhotosLibrary().selection]


def _records_from_photosdb(verbose: Callable) -> Optional[List[_PhotoRec]]:
    """Read the selected photos' dates/filenames from the Photos library
    database (no per-photo AppleScript). Returns None if the database cannot
    be used, so the caller can fall back to AppleScript."""
    uuids = _selected_uuids(verbose)
    try:
        import osxphotos
    except ImportError as err:
        verbose(f"cannot import osxphotos ({err})")
        return None
    verbose("loading the Photos library database (may take a while for a big library)...")
    try:
        db = osxphotos.PhotosDB()
    except Exception as err:
        verbose(f"could not read the Photos library database ({err})")
        return None
    verbose(f"loaded {db.library_path}")

    records: List[_PhotoRec] = []
    for uuid in uuids:
        info = db.get_photo(uuid)
        if info is None:
            verbose(
                f"selected photo {uuid} not found in {db.library_path} -- is a "
                "different library open in Photos? Falling back to AppleScript."
            )
            return None
        date = info.date
        if date is not None and date.tzinfo is not None:
            # PhotosDB dates are timezone-aware; timewarp and AppleScript speak
            # naive local time
            date = date.astimezone().replace(tzinfo=None)
        records.append(
            _PhotoRec(uuid, info.original_filename or info.filename or "", date)
        )
    return records


def _records_from_applescript(verbose: Callable) -> List[_PhotoRec]:
    import photoscript

    verbose("reading dates via AppleScript, one photo at a time (slow on big selections)...")
    return [
        _PhotoRec(_norm_uuid(photo.uuid), photo.filename or "", photo.date)
        for photo in photoscript.PhotosLibrary().selection
    ]


def _build_plan(verbose: Callable) -> _Plan:
    reader = os.environ.get(READER_ENV, "").strip().lower() or "db"
    if reader not in ("db", "applescript"):
        raise ValueError(f'invalid {READER_ENV} value {reader!r} (use "db" or "applescript")')
    records = _records_from_photosdb(verbose) if reader == "db" else None
    if records is None:
        records = _records_from_applescript(verbose)
    return _plan_from_records(records, verbose)


_plan: Optional[_Plan] = None


def get_date_time_timezone(
    photo: "Photo", path: Optional[str], tz_sec: int, tz_name: str, verbose: Callable
) -> Tuple[datetime, int]:
    """Called by `osxphotos timewarp --function` once per selected photo.

    Returns (new naive local date/time, timezone offset from UTC in seconds).
    """
    global _plan
    if _plan is None:
        _plan = _build_plan(verbose)

    uuid = _norm_uuid(photo.uuid)
    if uuid == _plan.ref_uuid:
        verbose(f"{_plan.ref_filename}: reference photo, keeping {_plan.ref_date}")
        return photo.date, tz_sec

    new_date = _plan.new_dates.get(uuid)
    if new_date is None:
        # not in the selection snapshotted at start (selection changed mid-run?)
        verbose(f"{photo.filename}: not in the initial selection, leaving unchanged")
        return photo.date, tz_sec

    name = _plan.filenames.get(uuid) or photo.filename
    verbose(f"{name}: {_plan.old_dates.get(uuid)} -> {new_date}")
    return new_date, tz_sec


if __name__ == "__main__":
    quiet = lambda *args, **kwargs: None  # noqa: E731

    def fake(uuid: str, filename: str, date: Optional[datetime]) -> _PhotoRec:
        return _PhotoRec(uuid, filename, date)

    assert parse_delta("1s") == timedelta(seconds=1)
    assert parse_delta("0") == timedelta(0)
    assert parse_delta("90") == timedelta(seconds=90)
    assert parse_delta("2m") == timedelta(minutes=2)
    assert parse_delta("1h30m") == timedelta(hours=1, minutes=30)
    assert parse_delta("1.5h") == timedelta(minutes=90)
    assert parse_delta("1d") == timedelta(days=1)
    assert parse_delta("-10s") == timedelta(seconds=-10)
    assert parse_delta(" +2m ") == timedelta(minutes=2)
    for bad in ("", "abc", "1x", "s", "2m1h"):
        try:
            parse_delta(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")

    assert _natural_key("IMG_10.jpg") > _natural_key("IMG_2.jpg")
    assert _norm_uuid("abc-123/L0/001") == "ABC-123"
    assert _norm_uuid("ABC-123") == "ABC-123"

    ref_date = datetime(2024, 6, 1, 12, 0, 0)
    # filename order: IMG_0002 (b) before IMG_0010 (a); current-date order: a, b
    selection = [
        fake("uuid-b", "IMG_0002.jpg", datetime(2025, 12, 24, 4, 0, 0)),
        fake("uuid-r", "IMG_1234.jpg", ref_date),
        fake("uuid-a", "IMG_0010.jpg", datetime(2025, 12, 24, 3, 0, 0)),
    ]

    def clear_env() -> None:
        for var in (REF_ENV, DELTA_ENV, ORDER_ENV, READER_ENV):
            os.environ.pop(var, None)

    clear_env()  # defaults: reference is the oldest photo, 1s delta, filename order
    plan = _plan_from_records(selection, quiet)
    assert plan.ref_uuid == "uuid-r"
    assert plan.new_dates == {
        "uuid-b": ref_date + timedelta(seconds=1),  # IMG_0002 sorts before IMG_0010
        "uuid-a": ref_date + timedelta(seconds=2),
    }
    assert plan.filenames["uuid-a"] == "IMG_0010.jpg"
    assert plan.old_dates["uuid-a"] == datetime(2025, 12, 24, 3, 0, 0)

    # photos without a date are skipped when picking the oldest, sort last by filename
    plan = _plan_from_records(selection + [fake("uuid-n", "IMG_none.jpg", None)], quiet)
    assert plan.ref_uuid == "uuid-r"
    assert plan.new_dates["uuid-n"] == ref_date + timedelta(seconds=3)

    try:
        _plan_from_records([fake("uuid-n", "IMG_none.jpg", None)], quiet)
    except ValueError as err:
        assert "has a date" in str(err)
    else:
        raise AssertionError("expected ValueError when no selected photo has a date")

    # a tie for oldest goes to the first as reported by Photos
    tied = datetime(2025, 1, 1, 0, 0, 0)
    plan = _plan_from_records([fake("t1", "b.jpg", tied), fake("t2", "a.jpg", tied)], quiet)
    assert plan.ref_uuid == "t1"

    os.environ[REF_ENV] = "oldest"
    plan = _plan_from_records(selection, quiet)
    assert plan.ref_uuid == "uuid-r"

    os.environ[REF_ENV] = "first"
    plan = _plan_from_records(selection, quiet)
    assert plan.ref_uuid == "uuid-b"

    os.environ[REF_ENV] = "img_1234"  # stem match, case-insensitive
    plan = _plan_from_records(selection, quiet)
    assert plan.ref_uuid == "uuid-r"

    os.environ[DELTA_ENV] = "0"
    plan = _plan_from_records(selection, quiet)
    assert plan.new_dates == {"uuid-b": ref_date, "uuid-a": ref_date}

    os.environ[DELTA_ENV] = "1m"
    os.environ[ORDER_ENV] = "selection"
    plan = _plan_from_records(selection, quiet)
    assert plan.new_dates == {
        "uuid-b": ref_date + timedelta(minutes=1),
        "uuid-a": ref_date + timedelta(minutes=2),
    }

    os.environ[ORDER_ENV] = "date"  # a (03:00) is older than b (04:00)
    plan = _plan_from_records(selection, quiet)
    assert plan.new_dates == {
        "uuid-a": ref_date + timedelta(minutes=1),
        "uuid-b": ref_date + timedelta(minutes=2),
    }

    os.environ[ORDER_ENV] = "bogus"
    try:
        _plan_from_records(selection, quiet)
    except ValueError as err:
        assert "bogus" in str(err)
    else:
        raise AssertionError("expected ValueError for invalid TIMEWARP_ORDER")

    clear_env()
    os.environ[READER_ENV] = "bogus"
    try:
        _build_plan(quiet)
    except ValueError as err:
        assert "bogus" in str(err)
    else:
        raise AssertionError("expected ValueError for invalid TIMEWARP_READER")

    clear_env()
    os.environ[REF_ENV] = "nope.jpg"
    try:
        _plan_from_records(selection, quiet)
    except ValueError as err:
        assert "does not match" in str(err)
    else:
        raise AssertionError("expected ValueError for unmatched TIMEWARP_REF")

    os.environ[REF_ENV] = "IMG_1234.jpg"
    try:
        _plan_from_records(selection + [fake("uuid-d", "IMG_1234.jpg", None)], quiet)
    except ValueError as err:
        assert "more than one" in str(err)
    else:
        raise AssertionError("expected ValueError for ambiguous TIMEWARP_REF")

    print("selftest OK")
