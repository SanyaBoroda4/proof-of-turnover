# Setup draft benchmark: bedroom and bathroom

Ran 2026-11-02 with `scripts/bench_setup_draft.py --preset bedroom` and
`--preset bathroom` (live API, throwaway cache and throwaway setups,
deleted afterwards). Same five combinations as Step 43. “sam/min ok”
means the drafted rule items carry the expected sam_prompt/min_count:
pillow 4, folded towels 2 (bedroom); toilet paper rolls 3, bath towels
4, hand towel 1 (bathroom).

## Bedroom

Photo: `data/photos/Airbnb/Bedroom_3_1.jpeg`. Rules: bed made with 4
pillows; two folded towels at the foot of the bed; no guest belongings,
closet empty; desk and nightstand wiped; floor vacuumed, nothing under
the bed.

| Model | Effort | Seconds | Rule items | Missing host rules | sam/min checks |
|---|---|---:|---:|---|---|
| muse-spark-1.3-contributor | medium | 34.9 | 6 | none | ok |
| muse-spark-1.3-contributor | low | 25.5 | 6 | none | ok |
| muse-spark-1.3-contributor | minimal | 15.6 | 6 | none | ok |
| muse-wonder-1.0 | low | 19.4 | 8 | none | ok |
| muse-wonder-1.0 | minimal | 9.1 | 7 | none | ok |

## Bathroom

Photos: `data/photos/Clean House/Half Bath Vanity.jpeg` and
`data/photos/Clean House/Half Bath Toilet.jpeg`. Rules: toilet lid open,
bowl clean; at least 2 spare toilet paper rolls besides the one on the
holder; 4 bath towels folded on the shelf; hand towel on the ring; no
guest toiletries left.

| Model | Effort | Seconds | Rule items | Missing host rules | sam/min checks |
|---|---|---:|---:|---|---|
| muse-spark-1.3-contributor | medium | 28.4 | 5 | none | ok |
| muse-spark-1.3-contributor | low | 17.8 | 5 | none | ok |
| muse-spark-1.3-contributor | minimal | 10.1 | 5 | none | ok |
| muse-wonder-1.0 | low | 16.0 | 5 | none | ok |
| muse-wonder-1.0 | minimal | 8.4 | 5 | none | ok |

## Recommendation

Keep the Step 43 default, **Muse Spark at minimal effort**, and do not
change it now:

- Minimal effort no longer loses counts: in both new rooms every
  combination, including Spark minimal, produced all host rules with
  the expected sam_prompt/min_count. The Step 26 worry (“low effort
  weakened sam_prompts”) did not show up here at minimal either.
- Muse Wonder minimal is faster in these two rooms (9.1 s / 8.4 s vs
  15.6 s / 10.1 s), but in the Step 43 porch benchmark it merged two
  host rules into one item (4 rule items for 5 rules), while Spark
  minimal kept all five separate (11.4 s). A global default must be
  safe on the porch too.
- If a per-room-type default is ever added, Wonder minimal is the
  candidate for bedrooms and bathrooms; until then, one predictable
  default (Spark minimal, roughly 10-16 s per room) beats a faster but
  inconsistent one. Revisit after the deadline with a bigger sample —
  these are single live runs and timings vary.
