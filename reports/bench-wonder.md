# Wonder second-opinion benchmark (Step 51b)

Live benchmark: the 20 second-opinion calls from run-20261102-151935 and run-20261102-150612, each re-asked independently (room, item, requirement and the run photo only; no cleaner answer, no first/second look) with scripts/bench_wonder.py. Throwaway model cache, so every call was live.

Truth (Alex): strict = must not answer met; flex rows accept the listed answers. Guard = evidence empty, over 40 words, or mostly non-Latin.

| Case | Truth | Wonder low | Wonder medium | Wonder high | Spark medium |
|---|---|---|---|---|---|
| 151935 porch.area_tidy P01 | met | - | - | - | - |
| 151935 hall-bathroom.shower P25 | not_met | not_met (2.8s) | not_met (4.0s) | not_met (3.9s) | not_met (7.1s) |
| 151935 hall-bathroom.no_guest_items P25 | not_met | not_met (3.1s) | not_met (5.0s) | not_met (3.7s) | not_met (5.3s) |
| 151935 powder-room.toilet_paper P27 | met | - | - | - | - |
| 151935 powder-room.spare_toilet_paper P05 | not_met | - | - | - | - |
| 151935 hall-bathroom.shelves P26 | not_met | not_met (3.3s) | not_met (3.6s) | not_met (4.5s) | not_met (3.8s) |
| 151935 hall-bathroom.towels P26 | not_met | cannot_tell (7.8s) | not_met (6.5s) | not_met (6.6s) | not_met (3.8s) |
| 151935 hall-bathroom.toilet_paper P30 | met | - | - | - | - |
| 151935 hall-bathroom.spare_toilet_paper P30 | not_met | not_met (4.3s) | not_met (4.9s) | not_met (4.9s) | not_met (4.8s) |
| 150612 kitchen.fridge P13 | report | not_met (2.3s) | not_met (5.5s) | not_met (3.8s) | not_met (3.5s) |
| 150612 master-bathroom.toilet_paper P20 | report | not_met (5.9s) | not_met (7.2s) | not_met (9.7s) | not_met (6.0s) |
| 150612 master-bedroom.bed_2 P17 | report | met (4.1s) | met (3.2s) | met (3.9s) | met (4.0s) |
| 150612 master-bathroom.shelves P23 | not_met | not_met (2.7s) | not_met (3.7s) | not_met (4.7s) | not_met (3.9s) |
| 150612 master-bathroom.towels P23 | not_met | cannot_tell (4.1s) | cannot_tell (4.3s) | cannot_tell (4.9s) | not_met (12.9s) |
| 150612 hall-bathroom.no_guest_items P28 | report | not_met (3.1s) | not_met (4.3s) | not_met (3.1s) | not_met (5.6s) |
| 150612 hall-bathroom.shelves P29 | not_met | not_met (4.7s) | not_met (3.8s) | not_met (3.2s) | not_met (3.6s) |
| 150612 hall-bathroom.shower P28 | report | not_met (3.7s) | not_met (4.1s) | not_met (3.8s) | not_met (6.8s) |
| 150612 hall-bathroom.towels P29 | not_met | not_met (6.9s) | not_met (6.1s) | not_met (9.7s) | not_met (3.9s) |
| 150612 hall-bathroom.spare_toilet_paper P26 | not_met | not_met (3.1s) | not_met (4.8s) | not_met (5.1s) | not_met (7.8s) |
| 150612 hall-bathroom.toilet_paper P26 | report | cannot_tell (6.2s) | cannot_tell (6.5s) | cannot_tell (8.1s) | cannot_tell (11.5s) |

## Summary

| Model | Effort | Wrong met (strict) | Guard fails | Flex fails | Avg sec |
|---|---|---:|---:|---:|---:|
| muse-wonder-1.0 | low | 0 | 0 | 2 | 4.7 |
| muse-wonder-1.0 | medium | 0 | 0 | 2 | 5.4 |
| muse-wonder-1.0 | high | 0 | 0 | 1 | 6.4 |
| muse-spark-1.3-contributor | medium | 0 | 0 | 1 | 6.3 |

## Choice

Every Wonder effort has zero wrong met answers on the strict NOT-met cases and zero guard failures. The fastest passing Wonder effort is low, so WONDER_EFFORT now defaults to low. Spark medium is shown only as a comparison and is not used for second opinions.
