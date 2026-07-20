# photo-tools
bits bin of photo scripts 

## timewarp_from_reference.py

Copy the date/time of one "reference" photo to all the other photos selected in
Photos.app, spaced out by a fixed delta so they keep a stable order. For use
with [`osxphotos timewarp --function`](https://github.com/RhetTbull/osxphotos)
(motivation: [osxphotos#2179](https://github.com/RhetTbull/osxphotos/issues/2179)).

1. In Photos, select the reference photo plus all the photos to fix.
2. Run:

   ```sh
   TIMEWARP_REF="IMG_1234.jpg" osxphotos timewarp \
       --function timewarp_from_reference.py::get_date_time_timezone --verbose
   ```

Every selected photo except the reference gets the reference's date/time +1s,
+2s, +3s, ... The reference photo itself is left unchanged.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TIMEWARP_REF` | first photo Photos reports | Filename (`IMG_1234.jpg`, extension optional) or UUID of the reference photo. Photos reports the selection in **library order, not the order you clicked**, so set this explicitly to be safe. |
| `TIMEWARP_DELTA` | `1s` | Spacing per photo: `0` (identical times), `90` (plain numbers are seconds), `2m`, `1h30m`, `1d`, `-10s`, ... |
| `TIMEWARP_ORDER` | `filename` | Order the increments are handed out in: `filename` (natural sort, so `IMG_2` < `IMG_10`) or `selection` (as Photos reports it). |

Notes:

- Try it on two or three photos first; `osxphotos timewarp --inspect` prints
  current values without changing anything.
- Timezones are left untouched; chain `osxphotos timewarp --timezone ...` if
  those need fixing too.
- If your filenames contain the full original date/time, also look at
  `osxphotos timewarp --parse-date DATE_PATTERN` (strptime pattern) — it can
  restore each photo's own true date instead of flattening a batch to one
  reference date.
- `python3 timewarp_from_reference.py` runs offline self-tests (safe on any
  machine; does not touch Photos).
