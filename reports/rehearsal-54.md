# Step 54 live rehearsal (Nov 2 photos, new card rules)

Run **run-20261103-183920**, left open with its cards unresolved so Alex
can walk them himself.

- Photos: Alex's Nov 2 photos from `data/runs/run-20261102-151935/photos`
  (the `_check.jpg` copies), mapped by `data/inputs/rehearsal_map_nov2.json`
  (gitignored, not committed). 13 shots uploaded at `--pace 4`; every room
  not in the map (kitchen, patio, master bedroom, master bathroom, hall
  bathroom, bedroom-1, bedroom-2) was skipped with the skip-room API
  before the first upload. P16, P19 and P21 were never uploaded.
- Command: `python scripts/rehearsal.py --base http://127.0.0.1:8003 --code <cleaner code> --map data/inputs/rehearsal_map_nov2.json --leave-cards --pace 4 --language en`
  (the cleaner code is not reproduced here).
- Server note: ports 8000-8002 were already held by servers started on
  Nov 1-2 (before Steps 51-53), so a fresh server with the current code
  was started on port 8003 for this rehearsal; the older processes were
  left untouched. Startup lines: Meta API: OK; Muse Wonder: OK; Voice:
  Microsoft neural voices (edge-tts).
- Result: 4 card groups for 6 items. The powder-room overview (P03) and
  the laundry overview (P07) each produced one grouped card with two
  items, and no separate close-up cards for items the overview decided.
- Open the run: host page `/host?run=run-20261103-183920` and cleaner
  page `/clean` on this server (add your access code as usual).

## Printed output

```
Staged 13 photos in C:\Users\alex\AppData\Local\Temp\rehearsal-hjmyl_9p (metadata removed)
Run run-20261103-183920 started (42 shots, language=en)
Skipped room kitchen (no mapped photos)
Skipped room patio (no mapped photos)
Skipped room master-bedroom (no mapped photos)
Skipped room master-bathroom (no mapped photos)
Skipped room hall-bathroom (no mapped photos)
Skipped room bedroom-1 (no mapped photos)
Skipped room bedroom-2 (no mapped photos)
[1/13] 18:39:20 porch-01 -> P01
[2/13] 18:39:24 foyer-01 -> P02
[3/13] 18:39:28 powder-00 -> P03
[4/13] 18:39:32 powder-01 -> P04
[5/13] 18:39:36 powder-02 -> P05
[6/13] 18:39:40 powder-03 -> P06
[7/13] 18:39:44 laundry-04 -> P07
[8/13] 18:39:48 laundry-01 -> P08
[9/13] 18:39:52 laundry-02 -> P09
[10/13] 18:39:56 laundry-03 -> P10
[11/13] 18:40:00 living-02 -> P11
[12/13] 18:40:05 living-01 -> P12
[13/13] 18:40:09 dining-01 -> P13
Last upload at 18:40:09.698
[round] 18:40:09 still checking: 8 (P05, P07, P08, P09, P10, P11...)
[round] 18:40:19 still checking: 6 (P08, P09, P10, P11, P12, P13)
[round] 18:40:24 still checking: 5 (P09, P10, P11, P12, P13)
[round] 18:40:29 still checking: 2 (P12, P13)
[round] 18:40:34 still checking: 1 (P12)
All checked at 18:40:39.871
agent_state=needs_input
--- 4 card groups ---
  photo=P03 shot=powder-00 items=powder-room.toilet_paper, powder-room.spare_toilet_paper
    powder-room.toilet_paper [problem] reason: Holder has one thin partially used roll, not a new full roll.
      request: Put a new full toilet paper roll on the holder next to the toilet and send a new photo.
    powder-room.spare_toilet_paper [needs photo] reason: Checks disagree
      request: Send a photo showing at least two toilet paper rolls including the spare roll in the powder room.
  photo=P07 shot=laundry-04 items=laundry.floor_clear, laundry.no_dirty_linens
    laundry.floor_clear [problem] reason: Laundry room floor has pile of clothes by door.
      request: Remove the pile of clothes by the laundry room door leaving the floor clear and send a new photo.
    laundry.no_dirty_linens [problem] reason: Pile of white clothes left on floor near door.
      request: Remove the pile of white clothes on the floor near the door and send a new photo.
  photo=P11 shot=living-02 items=living-room.pillows
    living-room.pillows [needs photo] reason: Second look says it is not done
      request: Send a photo of the throw pillows on the couch and armchairs with none on the floor and the TV console doors closed.
  photo=P13 shot=dining-01 items=dining-room.table
    dining-room.table [needs photo] reason: Second look says it is not done
      request: Send a photo of the clear and wiped dining room table.
Run left open: run-20261103-183920
Pages: http://127.0.0.1:8003/clean and http://127.0.0.1:8003/host?run=run-20261103-183920
```
