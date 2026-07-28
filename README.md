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

## dedupe_photos.py

Deterministic duplicate merging for Photos, phase 1: **read-only** scan +
plan + review. Apple's own Duplicates analysis is harvested straight from the
library database — `ZASSET.ZDUPLICATEPERCEPTUALMATCHINGALBUM` /
`ZDUPLICATEMETADATAMATCHINGALBUM` carry the grouping the Duplicates album
shows, so nothing needs to be selected or exported — then every group is
cross-checked with czkawka (`brew install czkawka`; the Homebrew build decodes
HEIC) by hardlinking the originals into a scratch farm and running its exact
(BLAKE3), perceptual-image, and video-signature tools over it.

**Assumption: all assets are stored on the Mac** (no iCloud
"Optimize Storage"). Scan still counts anything it can't find on disk, but
the pipeline is designed and tested for a fully local library.

```sh
osxphotos run dedupe_photos.py all --open       # scan + plan + review report
osxphotos run dedupe_photos.py scan --discover  # sweep the ENTIRE library
osxphotos run dedupe_photos.py scan --limit 5   # smoke test on 5 groups
python3 dedupe_photos.py --selftest             # offline tests, safe anywhere
```

Without `--discover`, tranches are exactly Apple's groups and czkawka only
*verifies* them. With `--discover`, czkawka sweeps **every local original**
and its groups feed the same union-find as Apple's: copies Apple missed
attach to their tranches (3-, 4-, N-member tranches), czkawka links merge
Apple groups that are really one photo, and czkawka-only tranches appear
(filter by source in the review page). czkawka-only groups that are entirely
one burst are dropped as burst siblings (`--include-bursts` keeps them).
The first sweep perceptually hashes the whole library — hours, cached and
incremental afterwards.

The plan fixes what Apple's Merge button gets wrong: the **keeper** is chosen
by resolution → file size → format (RAW > HEIC > PNG > JPEG) → UUID, never by
date, and the **merged date** is the oldest *plausible* timestamp found
anywhere in the tranche (every member's Photos date plus its file's
EXIF/QuickTime dates via exiftool; epoch placeholders, pre-1990 and future
dates are excluded but shown). Same inputs, same answer, every time. The
`date-spread` warning fires only when **EXIF/QuickTime candidates disagree
with each other** — duplicates re-imported on different days routinely carry
different Photos dates, and silently fixing that is the tool's job, not a
reason for scrutiny.

`review` renders a static HTML gallery: members side by side with thumbnails,
every date candidate (implausible ones struck through), czkawka verification
tier per tranche (`exact` / `visual-0` / `near` / `video` / `partial` /
`unverified` — the last two are Apple-only claims czkawka could not confirm,
so look closely). Approve/reject per tranche or in bulk, click anywhere on a
member card to make it the keeper, then *Export decisions* — the downloaded
`decisions.json` is what `apply` executes. Vim-style keys throughout
(`?` shows the map): `j`/`k`/`gg`/`G` navigate, `a`/`x` approve/reject and
advance, `u` clears, `n` jumps to the next undecided, `h`/`l` cycle the
keeper, `o` reveals the keeper in Photos.app. Reveal needs
`review --serve` (127.0.0.1, default port 8942), which adds per-member
*Photos* buttons backed by a `/reveal` endpoint (AppleScript `spotlight`,
uuids validated against the plan). In server mode *Export decisions* saves
straight into the out dir as `decisions-YYYYMMDD-HHMMSS.json` and refreshes
a stable `decisions.json` (opened as `file://` it downloads instead).
scan/plan/review never modify the library.

```sh
make -C merge-helper                       # build the PhotoKit helper (once)
osxphotos run dedupe_photos.py apply --decisions ~/Downloads/decisions.json
osxphotos run dedupe_photos.py apply --decisions ~/Downloads/decisions.json --apply
osxphotos run dedupe_photos.py verify --decisions ~/Downloads/decisions.json
```

`apply` executes only approved tranches, dry-run by default, in a
safety-ordered sequence: album/keyword/title transfer to the keeper via
photoscript (Photos running; `--skip-photoscript` to forgo), then merged
dates + favorites via `merge-helper` (PhotoKit `PHAssetChangeRequest` — the
supported change API, so edits sync to iCloud like hand edits), then keeper
dates are **re-verified against the live database**, and only then losers are
deleted through **one** batched PhotoKit call — a single system confirmation
dialog for the whole run, everything into Recently Deleted (30-day recovery).
Every member is re-validated against the live library first; tranches whose
members changed since the plan, or whose metadata transfer failed, are held
back and retried next run. Each apply writes an `apply-log-*.json` undo
record (old dates, deleted uuids; date changes are also revertible with
`osxphotos timewarp --reset`). `verify` reports per-tranche completeness and
exits nonzero while anything is pending.

**WAL caveat:** the default scan reader loads the library via osxphotos,
which copies `Photos.sqlite` *and its write-ahead log* to a temp dir. After a
huge import the WAL can be enormous (143 GB here), so scan refuses that
reader above `--max-wal-gb` (default 2). Quit Photos and everything else
holding the database (Messages, widgets — reopening Photos once, or a reboot,
lets macOS checkpoint), or use `--reader sqlite`, which reads the live
database in place at any WAL size and works mid-import, at the cost of
albums/keywords and derivative-based (fast) thumbnails.

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
