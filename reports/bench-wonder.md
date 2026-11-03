# Wonder second-opinion benchmark (Step 51b)

Live benchmark: the 20 second-opinion calls from run-20261102-151935 and run-20261102-150612, each re-asked independently (room, item, requirement and the run photo only; no cleaner answer, no first/second look) with scripts/bench_wonder.py. Throwaway model cache, so every call was live.

Truth (Alex): strict = must not answer met; flex rows (shown with /) accept either listed answer. Guard = evidence empty, over 40 words, or mostly non-Latin.

| Case | Truth | Wonder low | Wonder medium | Wonder high | Spark medium |
|---|---|---|---|---|---|
| 151935 porch.area_tidy P01 | met/cannot_tell | met (7.7s) | not_met (10.6s) | met (8.8s) | met (8.4s) |
| 151935 hall-bathroom.shower P25 | not_met | not_met (4.1s) | not_met (4.6s) | not_met (5.0s) | not_met (7.2s) |
| 151935 hall-bathroom.no_guest_items P25 | not_met | not_met (3.2s) | not_met (4.2s) | not_met (4.7s) | not_met (4.0s) |
| 151935 powder-room.toilet_paper P27 | met/cannot_tell | not_met (4.5s) | not_met (8.2s) | not_met (11.7s) | not_met (5.5s) |
| 151935 powder-room.spare_toilet_paper P05 | not_met/cannot_tell | not_met (6.3s) | not_met (10.8s) | not_met (11.2s) | cannot_tell (8.6s) |
| 151935 hall-bathroom.shelves P26 | not_met | not_met (2.5s) | not_met (3.3s) | not_met (3.6s) | not_met (3.2s) |
| 151935 hall-bathroom.towels P26 | not_met | not_met (4.2s) | not_met (12.8s) | cannot_tell (15.6s) | not_met (3.6s) |
| 151935 hall-bathroom.toilet_paper P30 | met/cannot_tell | cannot_tell (3.9s) | cannot_tell (7.4s) | cannot_tell (10.9s) | met (13.3s) |
| 151935 hall-bathroom.spare_toilet_paper P30 | not_met | not_met (3.7s) | not_met (4.7s) | not_met (6.3s) | not_met (5.6s) |
| 150612 kitchen.fridge P13 | report | not_met (3.6s) | not_met (4.9s) | not_met (4.3s) | not_met (3.6s) |
| 150612 master-bathroom.toilet_paper P20 | report | not_met (6.1s) | not_met (10.8s) | not_met (10.7s) | not_met (4.6s) |
| 150612 master-bedroom.bed_2 P17 | report | met (4.1s) | met (3.0s) | met (6.0s) | met (4.4s) |
| 150612 master-bathroom.shelves P23 | not_met | not_met (3.5s) | not_met (2.9s) | not_met (3.6s) | not_met (3.2s) |
| 150612 master-bathroom.towels P23 | not_met | cannot_tell (3.6s) | cannot_tell (6.0s) | cannot_tell (6.3s) | cannot_tell (12.9s) |
| 150612 hall-bathroom.no_guest_items P28 | report | not_met (3.2s) | not_met (4.7s) | not_met (4.9s) | not_met (3.2s) |
| 150612 hall-bathroom.shelves P29 | not_met | not_met (3.3s) | not_met (3.3s) | not_met (4.2s) | not_met (3.8s) |
| 150612 hall-bathroom.shower P28 | report | not_met (3.8s) | not_met (3.7s) | not_met (4.8s) | not_met (9.7s) |
| 150612 hall-bathroom.towels P29 | not_met | not_met (8.3s) | cannot_tell (6.9s) | not_met (10.5s) | not_met (5.8s) |
| 150612 hall-bathroom.spare_toilet_paper P26 | not_met | not_met (3.8s) | not_met (4.0s) | not_met (8.4s) | not_met (5.4s) |
| 150612 hall-bathroom.toilet_paper P26 | report | cannot_tell (7.2s) | cannot_tell (8.7s) | cannot_tell (10.3s) | not_met (7.9s) |

## Summary

| Model | Effort | Wrong met (strict) | Guard fails | Flex fails | Avg sec |
|---|---|---:|---:|---:|---:|
| muse-wonder-1.0 | low | 0 | 0 | 1 | 4.5 |
| muse-wonder-1.0 | medium | 0 | 0 | 2 | 6.3 |
| muse-wonder-1.0 | high | 0 | 0 | 1 | 7.6 |
| muse-spark-1.3-contributor | medium | 0 | 0 | 1 | 6.2 |

## Choice

Every Wonder effort has zero wrong met answers on the strict NOT-met cases and zero guard failures. The fastest passing Wonder effort is low, so WONDER_EFFORT now defaults to low. Spark medium is shown only as a comparison and is not used for second opinions.
