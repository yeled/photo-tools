# photo-tools
bits bin of photo scripts 

## timewarp_from_reference.py

Copy the date/time of one "reference" photo to all the other photos selected in
Photos.app, spaced out by a fixed delta so they keep a stable order. For use
with [`osxphotos timewarp --function`](https://github.com/RhetTbull/osxphotos)
(motivation: [osxphotos#2179](https://github.com/RhetTbull/osxphotos/issues/2179)).

1. In Photos, select the photos to fix together with one photo whose date is
   still correct.
2. Run:

   ```sh
   osxphotos timewarp \
       --function timewarp_from_reference.py::get_date_time_timezone --verbose
   ```

By default the reference is the **oldest** selected photo: when a bug stamps a
batch with too-new dates, the one photo whose date survived intact is the
oldest of the batch. Every other selected photo gets the reference's date/time
+1s, +2s, +3s, ...; the reference itself is left unchanged. To pick the
reference yourself, set `TIMEWARP_REF="IMG_1234.jpg"` (or a UUID) in front of
the command.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TIMEWARP_REF` | `oldest` | Which photo is the reference: `oldest` (earliest date, ties go to the first Photos reports), `first` (first as Photos reports the selection — **library order, not the order you clicked**), or a filename (`IMG_1234.jpg`, extension optional) / UUID. |
| `TIMEWARP_DELTA` | `1s` | Spacing per photo: `0` (identical times), `90` (plain numbers are seconds), `2m`, `1h30m`, `1d`, `-10s`, ... |
| `TIMEWARP_ORDER` | `filename` | Order the increments are handed out in: `filename` (natural sort, so `IMG_2` < `IMG_10`), `date` (current, pre-fix date order), or `selection` (as Photos reports it). |
| `TIMEWARP_READER` | `db` | `db` reads dates/filenames straight from the Photos library database via osxphotos (fast, no per-photo AppleScript), falling back to AppleScript automatically if the database can't be used; `applescript` forces the slow per-photo reads. |
| `TIMEWARP_UUID_FILE` | – | Path to a file of photo UUIDs (one per line, `#` comments ignored — the format `graph_photo_dates.py --uuid-file` writes). When set, these photos are the working set instead of the Photos selection: pair it with `timewarp --uuid-from-file` on the same file and nothing needs to be selected at all. |

Notes:

- Try it on two or three photos first; `osxphotos timewarp --inspect` prints
  current values without changing anything.
- AppleScript is used for exactly one read here — asking Photos which photos
  are selected (a single bulk call); dates and filenames come from the library
  database. Loading that database takes a moment on very large libraries, then
  the plan is instant. `timewarp` itself still writes the new dates back one
  photo at a time via AppleScript — that's the remaining slow part, and it
  lives in osxphotos, not in this script.
- Timezones are left untouched; chain `osxphotos timewarp --timezone ...` if
  those need fixing too.
- If your filenames contain the full original date/time, also look at
  `osxphotos timewarp --parse-date DATE_PATTERN` (strptime pattern) — it can
  restore each photo's own true date instead of flattening a batch to one
  reference date.
- `python3 timewarp_from_reference.py` runs offline self-tests (safe on any
  machine; does not touch Photos).

## graph_photo_dates.py

ASCII histogram of the dates in your Photos library, for spotting bulk
bad-date spikes — a batch of photos all stamped with the same wrong day (like
the Dec 24/25 export bug) shows up as one huge bar. Read-only: everything
comes from the library database via osxphotos, no AppleScript, nothing is
modified. Output is plain deterministic text, so you can save a run and diff
it against a later one.

```sh
osxphotos run graph_photo_dates.py                        # monthly overview + top spike days
osxphotos run graph_photo_dates.py --bucket day --year 2025
osxphotos run graph_photo_dates.py --day 2025-12-24 --uuid-file spike.txt
```

`--day` lists every photo on that day (time, filename, import date, UUID) and
`--uuid-file` writes the UUIDs, one per line. From there, without selecting
anything in Photos:

```sh
# collect them into an album you can open in Photos.app
osxphotos query --uuid-from-file spike.txt --add-to-album "Spike 2025-12-24"

# or fix their dates directly (oldest photo becomes the reference)
TIMEWARP_UUID_FILE=spike.txt osxphotos timewarp --uuid-from-file spike.txt \
    --function timewarp_from_reference.py::get_date_time_timezone --verbose
```

Note: the library **database holds more than the library grid shows** —
hidden photos, shared-album photos, and "Shared with You" items that Messages
feeds into Photos without them ever being saved to your library (they keep
their original EXIF dates, so they scatter across history). Those are
invisible to AppleScript, which is why `osxphotos show UUID` reports "could
not find asset" for them. The drill-down flags each such photo (e.g.
`[shared-with-you]`, `[hidden]`), `--uuid-file` writes them commented out so
`--uuid-from-file` consumers skip them, and `--visible-only` excludes them
from all output.

Other flags: `--field added` graphs import (added) dates instead of photo
dates, `--log` log-scales the bars so normal months stay visible next to a
giant spike, `--top N` sizes the spike-day list, `--from`/`--to` restrict the
range, `--library PATH` reads another library, and `--selftest` runs the
offline tests (safe on any machine).
