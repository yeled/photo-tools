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

Notes:

- Try it on two or three photos first; `osxphotos timewarp --inspect` prints
  current values without changing anything.
- `oldest` and `date` modes read every selected photo's date before starting,
  so a selection of thousands takes a while before the first change appears.
- Timezones are left untouched; chain `osxphotos timewarp --timezone ...` if
  those need fixing too.
- If your filenames contain the full original date/time, also look at
  `osxphotos timewarp --parse-date DATE_PATTERN` (strptime pattern) — it can
  restore each photo's own true date instead of flattening a batch to one
  reference date.
- `python3 timewarp_from_reference.py` runs offline self-tests (safe on any
  machine; does not touch Photos).
