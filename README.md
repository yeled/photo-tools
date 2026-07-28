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
- `python3 timewarp_from_reference.py --help` prints CLI usage including the
  environment-variable reference (a bare run instead dry-runs the current
  selection — see [Direct apply mode](#direct-apply-mode-photokit--no-timewarp-needed));
  `--selftest` runs the offline self-tests (safe on any machine; does not
  touch Photos).

### Direct apply mode (PhotoKit) — no timewarp needed

The same script can write the dates itself, through Apple's PhotoKit
(`PHAssetChangeRequest`) — the *supported* change API, so the edits sync to
iCloud exactly like edits made by hand in Photos, and thousands of photos take
seconds instead of an AppleScript crawl. A **bare run dry-runs against the
current Photos selection** (like `graph_photo_dates.py`'s bare overview);
nothing is written without `--apply`:

```sh
osxphotos run timewarp_from_reference.py            # dry run: prints the plan for the selection
osxphotos run timewarp_from_reference.py --apply    # write the selection via PhotoKit
```

Or work from a saved UUID list (from `graph_photo_dates.py --uuid-file`), with
Photos.app not even running:

```sh
osxphotos run timewarp_from_reference.py --uuid-file spike.txt           # dry run
osxphotos run timewarp_from_reference.py --uuid-file spike.txt --apply   # write
```

- Needs macOS ≥ 13.5 and [photokit](https://github.com/RhetTbull/photokit)
  (alpha: `pipx inject osxphotos photokit`). The first run prompts for Photos
  library access. System (default) library only.
- Reading the current selection uses one AppleScript call, so **Photos must be
  running for a bare run**; a `--uuid-file` needs no selection and no Photos.
- `--ref`, `--delta`, `--order` mirror the environment variables above; dry
  run is the default and nothing is written without `--apply`; timezones are
  never touched.
- `--engine applescript` writes via photoscript instead (slow, Photos must be
  open) if you'd rather not install photokit.
- Revert any time: `osxphotos timewarp --reset --uuid-from-file spike.txt` (or
  with the photos selected) restores the import-time originals regardless of
  which engine wrote the dates.

### Direct apply mode (PhotoKit) — no timewarp, no selection

The same script can write the dates itself, through Apple's PhotoKit
(`PHAssetChangeRequest`) — the *supported* change API, so the edits sync to
iCloud exactly like edits made by hand in Photos, Photos.app doesn't need to
be running, and thousands of photos take seconds instead of an AppleScript
crawl:

```sh
osxphotos run timewarp_from_reference.py --uuid-file spike.txt           # dry run: prints the plan
osxphotos run timewarp_from_reference.py --uuid-file spike.txt --apply   # write via PhotoKit
```

- Needs macOS ≥ 13.5 and [photokit](https://github.com/RhetTbull/photokit)
  (alpha: `pipx inject osxphotos photokit`). The first run prompts for Photos
  library access. System (default) library only.
- `--ref`, `--delta`, `--order` mirror the environment variables above; dry
  run is the default and nothing is written without `--apply`; timezones are
  never touched.
- `--engine applescript` writes via photoscript instead (slow, Photos must be
  open) if you'd rather not install photokit.
- Revert any time: `osxphotos timewarp --reset --uuid-from-file spike.txt`
  restores the import-time originals regardless of which engine wrote the
  dates.

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
range, `--color always|never` overrides the flag coloring (auto colors only on
a terminal and honors `NO_COLOR`, so redirected output stays plain and
diffable), `--library PATH` reads another library, and `--selftest` runs the
offline tests (safe on any machine).

## flatten-photos (Swift / PhotoKit)

Collapse single-album "wrapper" folders in Photos.app. For a hierarchy like

    import by hand > LR > 2024 > 2024-02-20 > [album]

each date folder holds exactly one album; `flatten-photos` moves that album up
into the year folder and deletes the emptied date folder:

    import by hand > LR > 2024 > [album]

A folder counts as a wrapper only if it contains **exactly one album and no
subfolders** — year folders, `LR`, and anything with real structure are left
alone. Wrapper folders are deleted only after re-verifying they are empty
(deleting a non-empty folder in Photos would delete its contents).

Why PhotoKit and not AppleScript: Photos' AppleScript dictionary has no `move`
command and `parent` is read-only (and buggy — it errors or returns bogus
references, error `-10008`), so the best a script can do is recreate the album
elsewhere, copy the media references, and delete the original — losing the
album's identity, key photo, and manual sort order. PhotoKit's
`PHCollectionListChangeRequest.removeChildCollections`/`addChildCollections`
does a true move: same album, nothing lost. Parent folders are never queried;
the tool walks the tree downward and remembers where it found each wrapper.

Build (needs Xcode Command Line Tools; the linker flags embed `Info.plist` so
the bare binary can present the Photos permission prompt):

```sh
cd flatten-photos && make
```

Usage:

```sh
./flatten-photos                       # dry run, whole library
./flatten-photos --scope LR            # dry run, only paths containing "LR"
./flatten-photos --scope LR --apply    # actually move + delete wrappers
./flatten-photos --apply --keep-empty  # move albums, keep the emptied folders
```

Notes:

- First run pops the Photos access dialog — grant full access (or System
  Settings > Privacy & Security > Photos afterwards).
- `--scope` is a case-insensitive substring match against the wrapper's full
  path, so `--scope "LR > 2024"` limits a run to one year. Do a year first,
  eyeball it in Photos, then run the rest.
- Dry run is the default; nothing changes without `--apply`.
