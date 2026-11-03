"""In-memory run state for the live tour: one RunState per run folder.

The state is what the server answers /status from; the JSON files are only
the backup loaded after a restart. Every mutation happens under the run's
lock and is saved atomically (temp file + os.replace), so photos checked
at the same time never clash and no request reads a half-written file.

Merge order: results merge in the order photos arrived. A result from an
older photo never replaces one from a newer photo of the same item, and
"can't see it" never erases an earlier proven or problem.
"""

import json
import os
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from pot.checklist import build_checklist, load_property
from pot.inspector import Claim, OtherIssue
from pot.matcher import Assignment
from pot.photos import Photo, ingest_file, read_taken_at
from pot.proof import (
    NEEDS_PROOF,
    PROBLEM,
    PROVEN,
    ProofItem,
    card_image_bytes,
    prove_all,
    prune_other_issues,
    save_evidence,
    verdict,
)
from pot.verifier import SamCheck, SecondLook

NOT_YET = "not yet"
NEEDS_PHOTO = "needs photo"

NEEDS_INPUT = "needs_input"
AWAITING_SIGNOFF = "awaiting_signoff"
DONE = "done"
SKIPPED_REASON = "Skipped by cleaner — not photographed"


class OldPhotoError(ValueError):
    """The photo's camera time is before the run started (foto antigua)."""


def api_failure(record):
    """True when a call-log record is an AI-service failure (not a judgment).

    API reasons: 401/403 (key), 5xx, and failures with no HTTP status
    (timeouts and connection errors after the retry). Anything the model
    actually answered — even "unclear" — is a judgment, not a failure.
    """
    if record.get("source") != "failed":
        return False
    status = record.get("status")
    if status is None:
        return True
    if not isinstance(status, int):
        return False
    return status in (401, 403) or status >= 500


def _is_second_opinion(record):
    """Background Muse Wonder calls never affect the photo/API status."""
    return (record.get("purpose") or "").startswith("second opinion")


def api_failure_reason(record):
    """Short human reason for an api_failure() record."""
    status = record.get("status")
    if status in (401, 403):
        return f"key rejected ({status})"
    if isinstance(status, int) and status >= 500:
        return f"Meta API error ({status})"
    return "Meta API unreachable (timeout)"


def _write_json_atomically(path, data):
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class RunState:
    """One turnover run: in-memory proofs, requests and progress."""

    def __init__(self, run_dir, property, checklist, tour=None, started_at=None,
                 demo=False, language=None, test=False):
        self.run_dir = Path(run_dir)
        self.property = property
        self.language = language or property.get("cleaner_language", "es")
        self.checklist = checklist
        self.items_by_id = {item.id: item for item in checklist}
        self.rooms = {room["id"]: room for room in property["rooms"]}
        self.tour = tour or {"shots": []}
        self.demo = demo
        self.test = bool(test or demo)
        self.started_at = started_at or datetime.now()
        self.lock = threading.Lock()
        self.photos = []
        self.photos_by_id = {}
        self.photo_seq = {}
        self.photo_shot = {}
        self.photo_retake = {}
        self.assignments = {}
        self.coverage = {}
        self.claims = {}
        self.seconds = {}
        self.sams = {}
        self.problem_sams = {}
        self.decisions = {}
        self.proofs = {
            item.id: ProofItem(item.id, NEEDS_PROOF, "No photo yet", "")
            for item in checklist
        }
        self.decided_seq = {}
        self.requests = {}
        self.other_issues = []
        self.notes = []
        self.voice_notes = []
        self.note_seq = 0
        self.calls = []
        self.checking = set()
        self.unchecked = set()
        self.api_down = None
        self.round_ended = False
        self.skipped_shots = []
        self.photo_seconds = {}
        self.events = []
        self.evidence = set()
        self.resolutions = {}
        self.finished_at = None
        self.card_ready_items = set()
        self.round_done_emitted = False
        self.signoff = {
            "decision": None,
            "notes": "",
            "time": None,
            "items": [],
            "open_items": [],
        }

    @classmethod
    def create(cls, run_dir, property_id, tour=None, demo=False, language=None,
               test=False):
        """Start a fresh run: seed state and write the initial files."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        property = load_property(property_id)
        state = cls(run_dir, property, build_checklist(property), tour=tour,
                     demo=demo, language=language, test=test)
        with state.lock:
            state._save_locked()
        return state

    @classmethod
    def load(cls, run_dir):
        """Rebuild state from a run folder's files (after a restart)."""
        from pot.tour import fixup_tour

        run_dir = Path(run_dir)
        first_data = json.loads((run_dir / "first_look.json").read_text(encoding="utf-8"))
        property = load_property(first_data["property"])
        checklist = build_checklist(property)
        saved_shots = (first_data.get("tour") or {}).get("shots", [])
        if saved_shots:
            shots, _ = fixup_tour(property, checklist, saved_shots)
        else:
            shots = []  # batch runs predate tours; leave them tour-less
        started_at = first_data.get("started_at")
        state = cls(
            run_dir,
            property,
            checklist,
            tour={"shots": shots},
            started_at=datetime.fromisoformat(started_at) if started_at else None,
            demo=bool(first_data.get("demo", False)),
            language=first_data.get("language"),
            test=bool(first_data.get("test", first_data.get("demo", False))),
        )
        for entry in first_data.get("photos", []):
            photo = Photo(
                id=entry["id"],
                original_name=entry.get("file", entry["id"]),
                taken_at=None,
                received_at=state.started_at,
                check_path=run_dir / "photos" / f"{entry['id']}_check.jpg",
                overview_path=run_dir / "photos" / f"{entry['id']}_overview.jpg",
                width=0,
                height=0,
            )
            state.photos.append(photo)
            state.photos_by_id[photo.id] = photo
            state.photo_seq[photo.id] = len(state.photos) - 1
            if entry.get("shot_id"):
                state.photo_shot[photo.id] = entry["shot_id"]
            retake_items = entry.get("retake_items")
            if isinstance(retake_items, list) and retake_items:
                state.photo_retake[photo.id] = [
                    item_id for item_id in retake_items
                    if isinstance(item_id, str)
                ]
            state.assignments[photo.id] = Assignment(
                photo_id=entry["id"],
                room_id=entry.get("room_id", "unknown"),
                confidence=entry.get("confidence", "low"),
                seen=entry.get("seen", ""),
            )
        state.coverage = dict(first_data.get("coverage", {}))
        state.claims = {
            item_id: Claim(**claim) for item_id, claim in first_data.get("claims", {}).items()
        }
        state.other_issues = [
            OtherIssue(**issue) for issue in first_data.get("other_issues", [])
        ]
        state.notes = list(first_data.get("notes", []))
        state.resolutions = {
            item_id: dict(entry)
            for item_id, entry in (first_data.get("resolutions") or {}).items()
            if item_id in state.proofs
        }
        state.finished_at = first_data.get("finished_at")
        state.unchecked = {
            entry for entry in first_data.get("unchecked", [])
            if isinstance(entry, str)
        }
        state.round_ended = bool(first_data.get("round_ended", False))
        state.skipped_shots = [
            entry for entry in first_data.get("skipped_shots", [])
            if isinstance(entry, str)
        ]
        state.voice_notes = [dict(note) for note in first_data.get("voice_notes", [])]
        for note in state.voice_notes:
            try:
                number = int(str(note.get("id", ""))[1:])
            except (ValueError, TypeError):
                continue
            state.note_seq = max(state.note_seq, number)
        proof_path = run_dir / "proof.json"
        if proof_path.is_file():
            proof_data = json.loads(proof_path.read_text(encoding="utf-8"))
            for item_id, entry in proof_data.get("items", {}).items():
                if item_id not in state.proofs:
                    continue
                state.proofs[item_id] = ProofItem(
                    item_id=item_id,
                    status=entry.get("status", NEEDS_PROOF),
                    reason=entry.get("reason", ""),
                    photo_id=entry.get("photo_id", ""),
                )
                seq = state.photo_seq.get(entry.get("photo_id"), -1)
                state.decided_seq[item_id] = seq
                if entry.get("second_look") is not None:
                    state.seconds[item_id] = SecondLook(**entry["second_look"])
                if entry.get("sam") is not None:
                    sam = entry["sam"]
                    state.sams[item_id] = SamCheck(
                        item_id, entry.get("photo_id", ""), sam["prompt"],
                        sam["count"], sam["boxes"],
                    )
                if entry.get("problem_sam") is not None:
                    sam = entry["problem_sam"]
                    state.problem_sams[item_id] = SamCheck(
                        item_id, entry.get("photo_id", ""), sam["prompt"],
                        sam["count"], sam["boxes"],
                    )
        requests_path = run_dir / "requests.json"
        if requests_path.is_file():
            stored = json.loads(requests_path.read_text(encoding="utf-8"))
            for request in stored.get("requests", []):
                state.requests[request["item_id"]] = request
        signoff_path = run_dir / "signoff.json"
        if signoff_path.is_file():
            state.signoff.update(json.loads(signoff_path.read_text(encoding="utf-8")))
        calls_path = run_dir / "calls.jsonl"
        if calls_path.is_file():
            for line in calls_path.read_text(encoding="utf-8").splitlines():
                try:
                    state.calls.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for record in reversed(state.calls):
            if _is_second_opinion(record):
                continue
            if api_failure(record):
                state.api_down = api_failure_reason(record)
                break
            if record.get("source") in ("live", "recorded"):
                break
        evidence_dir = run_dir / "evidence"
        if evidence_dir.is_dir():
            state.evidence = {
                path.name[:-len(".jpg")] for path in evidence_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".jpg"
                and not path.name.endswith(".card.jpg")
            }
        timeline_path = run_dir / "timeline.jsonl"
        if timeline_path.is_file():
            for line in timeline_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "card_ready" and event.get("item_id"):
                    state.card_ready_items.add(event["item_id"])
                elif event.get("event") == "round_done":
                    state.round_done_emitted = True
        if state.tour["shots"] and all(
            shot["shot_id"] in state.photo_shot.values()
            for shot in state.tour["shots"]
        ):
            state.round_done_emitted = True
        return state

    # -- phase (a): claim a photo number and save it, under the lock --------

    def _shot(self, shot_id):
        for shot in self.tour["shots"]:
            if shot["shot_id"] == shot_id:
                return shot
        raise ValueError(f"Unknown shot '{shot_id}'")

    def _next_photo_id(self):
        taken = set()
        for photo in self.photos:
            if photo.id.startswith("P") and photo.id[1:].isdigit():
                taken.add(int(photo.id[1:]))
        return f"P{(max(taken) + 1) if taken else 1:02d}"

    def claim_photo(self, upload_path, shot_id, retake_items=None):
        """Number and save an uploaded tour photo; returns (photo, seq).

        Runs under the lock, so two uploads at the same moment get
        different ids. Refuses photos taken before the run started.
        """
        with self.lock:
            shot = self._shot(shot_id)
            room_id = shot["room_id"]
            if room_id not in self.rooms:
                raise ValueError(f"Shot '{shot_id}' names unknown room '{room_id}'")
            taken = read_taken_at(upload_path)
            # 15 minutes of grace: the phone's clock may differ from the PC's.
            if taken is not None and taken < self.started_at - timedelta(minutes=15):
                raise OldPhotoError(
                    "foto antigua: this photo was taken before the tour started, "
                    "take a new one"
                )
            return self._ingest_locked(upload_path, room_id, shot_id,
                                       retake_items=retake_items)

    def claim_photo_for_room(self, upload_path, room_id):
        """Number and save a photo for a room (batch/CLI path, no tour)."""
        with self.lock:
            if room_id not in self.rooms:
                raise ValueError(f"Unknown room '{room_id}'")
            return self._ingest_locked(upload_path, room_id, "")

    def _ingest_locked(self, upload_path, room_id, shot_id, retake_items=None):
        photo_id = self._next_photo_id()
        photo = ingest_file(
            upload_path, self.run_dir / "photos", photo_id, keep_bytes=self.demo
        )
        seq = len(self.photos)
        self.photos.append(photo)
        self.photos_by_id[photo_id] = photo
        self.photo_seq[photo_id] = seq
        if shot_id:
            self.photo_shot[photo_id] = shot_id
        if retake_items:
            self.photo_retake[photo_id] = list(retake_items)
        self.assignments[photo_id] = Assignment(photo_id, room_id, "high", "")
        self.coverage[room_id] = "covered"
        coverage_id = f"{room_id}.coverage"
        if coverage_id in self.proofs:
            self.proofs[coverage_id] = ProofItem(
                coverage_id, PROVEN, "Room photographed", photo_id
            )
            self.decided_seq[coverage_id] = seq
        self.checking.add(photo_id)
        self.notes.append(f"Photo {photo_id} ({photo.original_name}) added for {room_id}")
        self._timeline_locked(
            "photo_received", photo_id=photo_id, shot_id=shot_id, room_id=room_id
        )
        if (
            self.tour["shots"]
            and not self.round_done_emitted
            and all(
                shot["shot_id"] in self.photo_shot.values()
                for shot in self.tour["shots"]
            )
        ):
            self.round_done_emitted = True
            self._timeline_locked("round_done", shots=len(self.tour["shots"]))
        self._save_locked()
        return photo, seq

    # -- phase (c): merge results in arrival order, under the lock ----------

    def snapshot(self):
        """Copies of the accumulated check maps for a lock-free worker."""
        with self.lock:
            return {
                "claims": dict(self.claims),
                "seconds": dict(self.seconds),
                "sams": dict(self.sams),
                "decisions": dict(self.decisions),
                "coverage": dict(self.coverage),
                "assignments": dict(self.assignments),
            }

    def merge_result(self, photo_id, fresh):
        """Merge one photo's results; returns {changes, failed}.

        fresh holds claims, seconds, sams, problem_sams, issues, notes and
        fresh_ids. Results apply in arrival order: an older photo finishing
        last never replaces a newer photo's result, and needs-proof never
        erases a proven or problem. failed lists this photo's shot items
        that still need a request.
        """
        with self.lock:
            seq = self.photo_seq[photo_id]
            fresh_ids = [i for i in fresh["fresh_ids"] if i in self.items_by_id]
            # Candidate verdicts come from temporary copies: the claim,
            # second look and SAM are stored only together with a verdict,
            # never next to another photo's kept verdict.
            acc_claims = dict(self.claims)
            acc_seconds = dict(self.seconds)
            acc_sams = dict(self.sams)
            for item_id in fresh_ids:
                if item_id in fresh["claims"]:
                    acc_claims[item_id] = fresh["claims"][item_id]
                if item_id in fresh["seconds"]:
                    acc_seconds[item_id] = fresh["seconds"][item_id]
                if item_id in fresh["sams"]:
                    acc_sams[item_id] = fresh["sams"][item_id]
            acc_decisions = dict(self.decisions)
            acc_decisions.update(fresh.get("decisions", {}))
            computed = prove_all(
                self.checklist, acc_claims, acc_seconds, acc_sams,
                self.assignments, self.coverage,
                [photo.id for photo in self.photos], acc_decisions, self.rooms,
            )
            self.decisions.update(fresh.get("decisions", {}))
            changes = {}
            for item_id in fresh_ids:
                new = computed[item_id]
                current = self.proofs[item_id]
                if seq < self.decided_seq.get(item_id, -1):
                    continue
                if new.status in (PROVEN, PROBLEM) or current.status not in (PROVEN, PROBLEM):
                    if item_id in fresh["claims"]:
                        self.claims[item_id] = fresh["claims"][item_id]
                    if item_id in fresh["seconds"]:
                        self.seconds[item_id] = fresh["seconds"][item_id]
                    if item_id in fresh["sams"]:
                        self.sams[item_id] = fresh["sams"][item_id]
                    self.proofs[item_id] = new
                    self.decided_seq[item_id] = seq
                    if new.status == PROVEN:
                        # A retake that proves the item clears its resolution.
                        self.resolutions.pop(item_id, None)
                    if new.status != current.status:
                        changes[item_id] = (current.status, new.status)
                        self._event_locked("status", item_id, new.status,
                                           new.photo_id, new.reason)
            shot_id = self.photo_shot.get(photo_id, "")
            if shot_id:
                try:
                    wanted = set(self._shot(shot_id).get("item_ids", []))
                except ValueError:
                    wanted = set()
                for item_id in wanted:
                    proof = self.proofs.get(item_id)
                    if (
                        proof is not None and proof.status == NEEDS_PROOF
                        and proof.reason == "No photo yet"
                        and item_id not in self.decided_seq
                    ):
                        self.proofs[item_id] = ProofItem(
                            item_id, NEEDS_PROOF, "No photo shows it", photo_id
                        )
            # Problem boxes belong to the photo the proof cites: take
            # this photo's boxes only for items citing this photo, and
            # drop a stale photo's boxes when the proof moves on.
            for item_id, sam in fresh.get("problem_sams", {}).items():
                proof = self.proofs.get(item_id)
                if (
                    proof is not None and proof.status == PROBLEM
                    and proof.photo_id == photo_id
                    and sam.photo_id == proof.photo_id
                ):
                    self.problem_sams[item_id] = sam
            self.problem_sams = {
                item_id: sam for item_id, sam in self.problem_sams.items()
                if self.proofs[item_id].status == PROBLEM
                and sam.photo_id == self.proofs[item_id].photo_id
            }
            self.other_issues = prune_other_issues(
                self.other_issues + fresh.get("issues", []), self.proofs, self.claims
            )
            self.notes.extend(fresh.get("notes", []))
            elapsed = fresh.get("elapsed_seconds")
            if elapsed is not None:
                self.photo_seconds[photo_id] = elapsed
                self.notes.append(f"Photo {photo_id} checked in {elapsed:.1f} s")
            if changes:
                evidence_dir = self.run_dir / "evidence"
                is_retake = bool(self.photo_retake.get(photo_id))
                for item_id, (old, new) in changes.items():
                    if is_retake and old == PROBLEM and new == PROVEN:
                        before = evidence_dir / f"{item_id}.before.jpg"
                        current = evidence_dir / f"{item_id}.jpg"
                        if not before.exists() and current.is_file():
                            before.write_bytes(current.read_bytes())
                            self.evidence.add(f"{item_id}.before")
                saved = save_evidence(
                    self.run_dir, self.proofs, self.items_by_id, self.photos_by_id,
                    self.sams, self.problem_sams, item_ids=set(changes),
                )
                self.evidence.update(saved)
            self._save_locked()
            shot_id = self.photo_shot.get(photo_id)
            failed = self._shot_failed_locked(shot_id) if shot_id else []
            return {"changes": changes, "failed": failed}

    def _shot_failed_locked(self, shot_id):
        try:
            wanted = set(self._shot(shot_id).get("item_ids", []))
        except ValueError:
            return []
        return [
            item_id for item_id in wanted
            if item_id in self.items_by_id
            and self.items_by_id[item_id].critical
            and self.proofs[item_id].status != PROVEN
        ]

    def _skipped_only_locked(self):
        """Critical item ids excused by skipped shots (callers hold the lock).

        A shot item is excused when every shot showing it was skipped; a
        room-coverage item is excused when the round ended early and its
        room was never photographed.
        """
        taken = self._taken_shots_locked()
        skipped = {
            shot_id for shot_id in self.skipped_shots if shot_id not in taken
        }
        if not skipped and not self.round_ended:
            return set()
        rooms_taken = {
            shot["room_id"] for shot in self.tour["shots"]
            if shot["shot_id"] in taken
        }
        room_shots = {}
        for shot in self.tour["shots"]:
            room_shots.setdefault(shot["room_id"], []).append(shot["shot_id"])
        item_shots = {}
        for shot in self.tour["shots"]:
            for item_id in shot.get("item_ids", []):
                item_shots.setdefault(item_id, []).append(shot["shot_id"])
        found = set()
        for item in self.checklist:
            if not item.critical:
                continue
            if item.key == "coverage":
                shots = room_shots.get(item.room_id, [])
                if shots and all(s in skipped for s in shots):
                    found.add(item.id)
                elif self.round_ended and item.room_id not in rooms_taken:
                    found.add(item.id)
                continue
            shots = item_shots.get(item.id, [])
            if shots and all(shot_id in skipped for shot_id in shots):
                found.add(item.id)
        return found

    def end_round(self):
        """Test/demo early finish: skip every shot with no photo yet."""
        with self.lock:
            taken = self._taken_shots_locked()
            self.skipped_shots = [
                shot["shot_id"] for shot in self.tour["shots"]
                if shot["shot_id"] not in taken
            ]
            self.round_ended = True
            self._timeline_locked("round_ended", skipped=len(self.skipped_shots))
            self._save_locked()
            return list(self.skipped_shots)

    def skip_shot(self, shot_id):
        """Test/demo skip: one shot counts as skipped without a photo."""
        with self.lock:
            known = {shot["shot_id"] for shot in self.tour["shots"]}
            if shot_id not in known:
                raise ValueError(f"Unknown shot '{shot_id}'")
            if shot_id in self._taken_shots_locked():
                raise ValueError(f"Shot '{shot_id}' already has a photo")
            if shot_id not in self.skipped_shots:
                self.skipped_shots.append(shot_id)
            self._timeline_locked("shot_skipped", shot_id=shot_id)
            self._save_locked()
            return list(self.skipped_shots)

    def skip_room(self, room_id):
        """Test/demo skip: every unphotographed shot of one room is skipped."""
        with self.lock:
            rooms = {room["id"] for room in self.property["rooms"]}
            if room_id not in rooms:
                raise ValueError(f"Unknown room '{room_id}'")
            taken = self._taken_shots_locked()
            skipped = []
            for shot in self.tour["shots"]:
                shot_id = shot["shot_id"]
                if shot.get("room_id") != room_id:
                    continue
                if shot_id in taken or shot_id in self.skipped_shots:
                    continue
                self.skipped_shots.append(shot_id)
                self._timeline_locked("shot_skipped", shot_id=shot_id)
                skipped.append(shot_id)
            self._save_locked()
            return skipped

    def unskip_room(self, room_id):
        """Put a skipped room's unphotographed shots back into the tour."""
        with self.lock:
            rooms = {room["id"] for room in self.property["rooms"]}
            if room_id not in rooms:
                raise ValueError(f"Unknown room '{room_id}'")
            taken = self._taken_shots_locked()
            restored = [
                shot["shot_id"] for shot in self.tour["shots"]
                if shot.get("room_id") == room_id
                and shot["shot_id"] in self.skipped_shots
                and shot["shot_id"] not in taken
            ]
            restored_set = set(restored)
            self.skipped_shots = [
                shot_id for shot_id in self.skipped_shots
                if shot_id not in restored_set
            ]
            self._timeline_locked("room_unskipped", room_id=room_id,
                                  shots=len(restored))
            self._save_locked()
            return restored

    def _cards_locked(self):
        """Open problem-card item ids in tour order (callers hold the lock).

        One card per critical item that is not proven, not resolved, and
        whose shots are all taken with every photo checked. Items of a
        photo that could not be checked, and items shown only in skipped
        shots, make no cards.
        """
        taken = self._taken_shots_locked()
        busy_shots = {self.photo_shot.get(photo_id, "") for photo_id in self.checking}
        blind_shots = {self.photo_shot.get(photo_id, "") for photo_id in self.unchecked}
        skipped = {
            shot_id for shot_id in self.skipped_shots if shot_id not in taken
        }
        skipped_only = self._skipped_only_locked()
        item_shots = {}
        for shot in self.tour["shots"]:
            for item_id in shot.get("item_ids", []):
                item_shots.setdefault(item_id, []).append(shot["shot_id"])
        cards, seen = [], set()
        for shot in self.tour["shots"]:
            for item_id in shot.get("item_ids", []):
                if item_id in seen:
                    continue
                seen.add(item_id)
                item = self.items_by_id.get(item_id)
                if item is None or not item.critical:
                    continue
                if self.proofs[item_id].status == PROVEN:
                    continue
                if item_id in self.resolutions:
                    continue
                if item_id in skipped_only:
                    continue
                shots = [
                    shot_id for shot_id in item_shots.get(item_id, [])
                    if shot_id not in skipped
                ]
                if not shots or any(shot_id in blind_shots for shot_id in shots):
                    continue
                if all(
                    shot_id in taken and shot_id not in busy_shots for shot_id in shots
                ):
                    cards.append(item_id)
        if taken and not self.checking:
            for item in self.checklist:
                if (
                    item.id not in seen
                    and item.critical
                    and item.key != "coverage"
                    and self.proofs[item.id].status != PROVEN
                    and item.id not in self.resolutions
                ):
                    cards.append(item.id)
        return cards

    def _card_groups_locked(self):
        """Cards grouped by cited photo (callers hold the lock).

        Items whose proof cites the same photo share one group; an
        item with no photo is its own group (key = item id). Groups
        are ordered by the tour index of the group's shot, ties by
        the first item's card order.
        """
        cards = self._cards_locked()
        shot_index = {
            shot["shot_id"]: i for i, shot in enumerate(self.tour["shots"])
        }

        def fallback_shot(item_id):
            listing = [
                shot for shot in self.tour["shots"]
                if item_id in shot.get("item_ids", [])
            ]
            if listing:
                best = min(
                    listing,
                    key=lambda s: (
                        len(s.get("item_ids", [])),
                        shot_index[s["shot_id"]],
                    ),
                )
                return best["shot_id"]
            item = self.items_by_id.get(item_id)
            if item is not None:
                for shot in self.tour["shots"]:
                    if shot.get("room_id") == item.room_id:
                        return shot["shot_id"]
            return ""

        groups = []
        by_key = {}
        for card_pos, item_id in enumerate(cards):
            photo_id = self.proofs[item_id].photo_id or ""
            key = photo_id or item_id
            if key not in by_key:
                shot_id = (
                    self.photo_shot.get(photo_id, "") if photo_id
                    else fallback_shot(item_id)
                )
                by_key[key] = {
                    "key": key,
                    "photo_id": photo_id,
                    "shot_id": shot_id,
                    "item_ids": [],
                    "_pos": card_pos,
                    "_shot": shot_index.get(shot_id, len(shot_index)),
                }
                groups.append(by_key[key])
            by_key[key]["item_ids"].append(item_id)
        groups.sort(key=lambda g: (g["_shot"], g["_pos"]))
        return [
            {
                "key": g["key"],
                "photo_id": g["photo_id"],
                "shot_id": g["shot_id"],
                "item_ids": g["item_ids"],
            }
            for g in groups
        ]

    def claim_rechecks(self):
        """Unchecked photos to retry: back to checking, with a timeline event."""
        with self.lock:
            todo = sorted(self.unchecked - self.checking)
            for photo_id in todo:
                self.checking.add(photo_id)
                self._timeline_locked("photo_recheck", photo_id=photo_id)
            return todo

    def mark_unchecked(self, photo_id, note):
        """The photo's AI check failed: saved, not judged, retryable later."""
        with self.lock:
            self.checking.discard(photo_id)
            self.unchecked.add(photo_id)
            self.notes.append(note)
            self._timeline_locked("photo_finished", photo_id=photo_id, outcome="unchecked")
            self._maybe_all_checked_locked()
            self._save_locked()

    def finish_photo(self, photo_id):
        """The photo's check, including its requests, is fully stored."""
        with self.lock:
            self.checking.discard(photo_id)
            self.unchecked.discard(photo_id)
            self._timeline_locked("photo_finished", photo_id=photo_id, outcome="ok")
            for item_id in self._cards_locked():
                if item_id not in self.card_ready_items:
                    self.card_ready_items.add(item_id)
                    self._timeline_locked("card_ready", item_id=item_id)
            self._maybe_all_checked_locked()
            self._save_locked()

    def mark_failed(self, photo_id, note):
        """Drop a photo from checking after a worker error."""
        with self.lock:
            self.checking.discard(photo_id)
            self.unchecked.discard(photo_id)
            self.notes.append(note)
            self._timeline_locked("photo_finished", photo_id=photo_id, outcome="failed")
            self._maybe_all_checked_locked()
            self._save_locked()

    def store_requests(self, requests, notes):
        """Merge per-shot requests; drop requests for proven items."""
        with self.lock:
            for request in requests:
                self.requests[request["item_id"]] = request
                proof = self.proofs.get(request["item_id"])
                self._event_locked("request", request["item_id"], request["status"],
                                   proof.photo_id if proof else "",
                                   f"request sent: {request['host']}")
            self.requests = {
                item_id: request for item_id, request in self.requests.items()
                if self.proofs.get(item_id) is not None
                and self.proofs[item_id].status != PROVEN
            }
            self.notes.extend(notes)
            self._save_locked()

    def record_call(self, entry):
        """Append one model-call log entry (memory side of calls.jsonl)."""
        with self.lock:
            self.calls.append(entry)
            if _is_second_opinion(entry):
                return
            if api_failure(entry):
                self.api_down = api_failure_reason(entry)
            elif entry.get("source") in ("live", "recorded"):
                self.api_down = None

    def call_count(self):
        """How many call-log entries exist (a scan start mark for workers)."""
        with self.lock:
            return len(self.calls)

    def photo_had_api_failure(self, photo_id, before):
        """True when the photo's calls since `before` hit an API failure.

        Failed box-pick calls do not count: the pick falls back to keeping
        all boxes, so the photo was still judged. Failed second opinions
        do not count either: they run in the background after the photo
        was judged and never change its proof.
        """
        with self.lock:
            return any(
                record.get("photo_id") == photo_id
                and api_failure(record)
                and not (record.get("purpose") or "").startswith(
                    ("pick problem boxes", "pick problem area",
                     "second opinion"))
                for record in self.calls[before:]
            )

    def timeline(self, event, **fields):
        """Append one ms-timestamped event to timeline.jsonl."""
        with self.lock:
            self._timeline_locked(event, **fields)

    def _timeline_locked(self, event, **fields):
        """Append a timeline event; callers hold the lock."""
        record = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
        }
        record.update(fields)
        with open(self.run_dir / "timeline.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _maybe_all_checked_locked(self):
        if not self.checking:
            self._timeline_locked("all_checked", photos=len(self.photos))

    def next_note_id(self):
        """Claim the next voice-note id (N01, N02, ...)."""
        with self.lock:
            self.note_seq += 1
            return f"N{self.note_seq:02d}"

    def store_voice_note(self, record, notes=()):
        """Store a translated voice note with its news event."""
        with self.lock:
            self.voice_notes.append(dict(record))
            self._event_locked("note", text=record["title"],
                               room_id=record["room_id"],
                               room_name=record["room_name"])
            self.notes.extend(notes)
            self._save_locked()

    def _event_locked(self, kind, item_id=None, status=None, photo_id=None, text="",
                      room_id=None, room_name=None, extra=None):
        """Append a news event (callers hold the lock); keeps the last 50."""
        item = self.items_by_id.get(item_id) if item_id else None
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,  # status | request | signoff | note | resolution | finish
            "room_id": item.room_id if item else (room_id or ""),
            "room_name": item.room_name if item else (room_name or ""),
            "item_id": item_id or "",
            "item_label": item.label if item else "",
            "status": status or "",
            "photo_id": photo_id or "",
            "text": text or "",
        }
        if extra:
            event.update(extra)
        self.events.append(event)
        del self.events[:-50]

    # -- sign-off ---------------------------------------------------------

    def approve(self):
        """Host approves: state becomes done."""
        with self.lock:
            host_verdict = verdict(self.checklist, self.proofs)
            self.signoff = {
                "decision": "approve",
                "notes": "",
                "time": datetime.now().isoformat(timespec="seconds"),
                "items": [],
                "open_items": host_verdict.problems + host_verdict.need_photo,
            }
            self.notes.append("Host approved the turnover")
            self._event_locked("signoff", text="Host approved the turnover")
            self._save_locked()
            return dict(self.signoff)

    def send_back(self, item_ids, notes):
        """Host sends items back: they go to needs-photo with the note."""
        with self.lock:
            self.finished_at = None
            known = [i for i in item_ids if i in self.items_by_id]
            for item_id in known:
                self.proofs[item_id] = ProofItem(
                    item_id, NEEDS_PROOF, notes or "Host asked for a new photo", ""
                )
                self.decided_seq[item_id] = len(self.photos)
                self.requests.pop(item_id, None)
                self.resolutions.pop(item_id, None)
                self.evidence.discard(item_id)
                self._event_locked("status", item_id, NEEDS_PROOF, "",
                                   notes or "Host asked for a new photo")
                for suffix in (".jpg", ".card.jpg"):
                    stale = self.run_dir / "evidence" / f"{item_id}{suffix}"
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            host_verdict = verdict(self.checklist, self.proofs)
            self.signoff = {
                "decision": "sendback",
                "notes": notes,
                "time": datetime.now().isoformat(timespec="seconds"),
                "items": known,
                "open_items": host_verdict.problems + host_verdict.need_photo,
            }
            self.notes.append(f"Host sent back {len(known)} items: {notes}")
            self._event_locked(
                "signoff", text=f"Host sent back {len(known)} items: {notes}")
            self._save_locked()
            return dict(self.signoff)

    # -- problem cards: cleaner resolutions + finish ------------------------

    def resolve_item(self, item_id, action, reason, note_id):
        """Resolve one open critical item as reported or disputed."""
        with self.lock:
            self.resolutions[item_id] = {
                "action": action,  # cant_fix | its_fine
                "reason": reason,  # broken | before | supplies | wont_clean | other | None
                "note_id": note_id,
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            if action == "cant_fix":
                text = f"Cleaner reported {item_id} ({reason or 'no reason'})"
            else:
                text = f"Cleaner says {item_id} is fine"
            self.notes.append(text)
            self._event_locked("resolution", item_id, action,
                               self.proofs[item_id].photo_id, text,
                               extra={"reason": reason})
            self._timeline_locked("card_resolved", item_id=item_id, action=action)
            self._save_locked()
            return dict(self.resolutions[item_id])

    def mark_wonder_pending(self, item_id):
        """A Muse Wonder second opinion is running for this resolution."""
        with self.lock:
            if item_id in self.resolutions:
                self.resolutions[item_id]["wonder"] = {"status": "pending"}
                self._save_locked()

    def store_wonder(self, item_id, wonder):
        """Save a finished second opinion (+ news event when it landed)."""
        phrases = {
            "agree_with_cleaner": "agrees with the cleaner",
            "still_a_problem": "still a problem",
            "unclear": "can't tell from this photo",
        }
        with self.lock:
            if item_id not in self.resolutions:
                return
            self.resolutions[item_id]["wonder"] = dict(wonder)
            if wonder.get("status") == "done":
                phrase = phrases.get(wonder.get("opinion"), wonder.get("opinion"))
                self._event_locked(
                    "request", item_id=item_id,
                    text=f"Muse Wonder: {phrase} - {wonder.get('reason', '')}",
                )
            self._save_locked()

    def finish(self):
        """Cleaner sends the turnover: all checked, all critical proven/resolved."""
        with self.lock:
            self.finished_at = datetime.now().isoformat(timespec="seconds")
            self.notes.append("Cleaner finished the turnover")
            self._event_locked("finish", text="Cleaner finished the turnover")
            self._save_locked()
            return self.finished_at

    # -- reads (all from memory, never from files) --------------------------

    def agent_state(self):
        """needs_input / awaiting_signoff / done."""
        with self.lock:
            if self.signoff.get("decision") == "approve":
                return DONE
            return self._agent_state_locked()

    def _agent_state_locked(self):
        # Skipped-only items are NOT excused: they stay needs-proof, so
        # the run is needs_input while any critical item is unproven.
        for item in self.checklist:
            if (
                item.critical
                and self.proofs[item.id].status != PROVEN
                and item.id not in self.resolutions
            ):
                return NEEDS_INPUT
        return AWAITING_SIGNOFF

    def _taken_shots_locked(self):
        return {self.photo_shot[photo_id] for photo_id in self.photo_shot}


    def _skipped_areas_locked(self, skipped):
        """Skipped shots grouped into areas, in tour order."""
        names = {room["id"]: room["name"] for room in self.property["rooms"]}
        skipped_only = self._skipped_only_locked()
        checks_by_room = {}
        for item in self.checklist:
            if item.id in skipped_only and item.key != "coverage":
                checks_by_room.setdefault(item.room_id, []).append(item)
        areas = []
        by_room = {}
        for shot in self.tour["shots"]:
            if shot["shot_id"] not in skipped:
                continue
            room_id = shot.get("room_id", "")
            if room_id not in by_room:
                checks = checks_by_room.get(room_id, [])
                by_room[room_id] = {
                    "room_id": room_id,
                    "room_name": names.get(room_id, room_id),
                    "shots": 0,
                    "items": [item.label for item in checks],
                    "item_ids": [item.id for item in checks],
                    "checks": [item.label for item in checks],
                }
                areas.append(by_room[room_id])
            by_room[room_id]["shots"] += 1
        return areas

    def _not_photographed_locked(self):
        """Critical items never photographed because their shots were skipped."""
        skipped_only = self._skipped_only_locked()
        items = [
            item for item in self.checklist
            if item.id in skipped_only
            and self.proofs[item.id].status != PROVEN
            and item.id not in self.resolutions
        ]
        names = {room["id"]: room["name"] for room in self.property["rooms"]}
        areas = []
        for item in items:
            name = names.get(item.room_id, item.room_name)
            if name not in areas:
                areas.append(name)
        return items, areas

    def status(self):
        """Everything both pages need, built from memory under the lock."""
        with self.lock:
            taken = self._taken_shots_locked()
            shot_of_item = {}
            for shot in self.tour["shots"]:
                for item_id in shot.get("item_ids", []):
                    shot_of_item.setdefault(item_id, []).append(shot["shot_id"])
            rooms_out = []
            for room in self.property["rooms"]:
                items_out = []
                for item in self.checklist:
                    if item.room_id != room["id"]:
                        continue
                    items_out.append(self._item_status_locked(item, shot_of_item, taken))
                rooms_out.append({
                    "id": room["id"],
                    "name": room["name"],
                    "floor": room["floor"],
                    "items": items_out,
                })
            house_out = [
                self._item_status_locked(item, shot_of_item, taken)
                for item in self.checklist if item.room_id == "house"
            ]
            if house_out:
                rooms_out.append({"id": "house", "name": "Whole house", "floor": None, "items": house_out})
            host_verdict = verdict(self.checklist, self.proofs)
            skipped = [
                shot_id for shot_id in self.skipped_shots
                if shot_id not in taken
            ]
            np_items, np_areas = self._not_photographed_locked()
            current = next(
                (shot for shot in self.tour["shots"] if shot["shot_id"] not in taken),
                None,
            )
            rooms_done = 0
            for room in self.property["rooms"]:
                room_shots = [s for s in self.tour["shots"] if s["room_id"] == room["id"]]
                if room_shots and all(s["shot_id"] in taken for s in room_shots):
                    rooms_done += 1
            recent = []
            seen_shots = set()
            for photo in reversed(self.photos):
                shot_id = self.photo_shot.get(photo.id, "")
                if shot_id and shot_id not in seen_shots:
                    seen_shots.add(shot_id)
                    recent.append({"shot_id": shot_id, "photo_id": photo.id})
            return {
                "run_id": self.run_dir.name,
                "property": self.property["id"],
                "language": self.language,
                "agent_state": self._agent_state_locked()
                if self.signoff.get("decision") != "approve" else DONE,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "progress": {
                    "shots_taken": len(taken) + len(skipped),
                    "shots_total": len(self.tour["shots"]),
                    "rooms_done": rooms_done,
                    "rooms_total": len(self.property["rooms"]),
                    "photos": len(self.photos),
                },
                "current_shot": current,
                "rooms": rooms_out,
                "verdict": {
                    "ready": host_verdict.ready,
                    "problems": host_verdict.problems,
                    "need_photo": host_verdict.need_photo,
                    "worth_a_look": host_verdict.worth_a_look,
                },
                "open": host_verdict.problems + host_verdict.need_photo,
                "cards": self._cards_locked(),
                "card_groups": self._card_groups_locked(),
                "finished_at": self.finished_at,
                "checking": sorted(self.checking),
                "unchecked": sorted(self.unchecked),
                "api_status": {
                    "ok": self.api_down is None,
                    "message": self.api_down or "",
                },
                "round_ended": self.round_ended,
                "skipped_shots": list(skipped),
                "skipped_areas": self._skipped_areas_locked(skipped),
                "not_photographed_count": len(np_items),
                "not_photographed_items": [item.id for item in np_items],
                "not_photographed_areas": np_areas,
                "photo_seconds": dict(self.photo_seconds),
                "recent": recent,
                "news": list(reversed(self.events[-8:])),
                "signoff": dict(self.signoff),
                "voice_notes": [self._voice_note_out(note) for note in reversed(self.voice_notes)],
            }

    def _item_status_locked(self, item, shot_of_item, taken):
        proof = self.proofs[item.id]
        skipped_only = self._skipped_only_locked()
        is_skipped = item.id in skipped_only and proof.status == NEEDS_PROOF
        if proof.status == PROVEN:
            display = PROVEN
        elif proof.status == PROBLEM:
            display = PROBLEM
        elif is_skipped:
            display = NEEDS_PHOTO
        else:
            shots = shot_of_item.get(item.id)
            if shots is None:
                if item.key == "coverage":
                    room_shots = [
                        s["shot_id"] for s in self.tour["shots"] if s["room_id"] == item.room_id
                    ]
                    display = NEEDS_PHOTO if any(s in taken for s in room_shots) else NOT_YET
                else:
                    display = NEEDS_PHOTO if taken else NOT_YET
            else:
                display = NEEDS_PHOTO if any(s in taken for s in shots) else NOT_YET
        request = self.requests.get(item.id)
        return {
            "id": item.id,
            "label": item.label,
            "critical": item.critical,
            "status": display,
            "reason": SKIPPED_REASON if is_skipped else proof.reason,
            "photo_id": proof.photo_id,
            "has_evidence": item.id in self.evidence,
            "has_before": f"{item.id}.before" in self.evidence,
            "resolution": self.resolutions.get(item.id),
            "request": request,
        }

    def call_log(self):
        """The model-call log, newest last."""
        with self.lock:
            return [dict(entry) for entry in self.calls]

    def taken_shots(self):
        """Sorted ids of shots with at least one photo."""
        with self.lock:
            return sorted(self._taken_shots_locked())

    def read_evidence(self, item_id, card=False):
        """Evidence JPEG bytes, read under the lock (never mid-replace).

        With card, the caption-free copy for the phone cards; falls back
        to the captioned photo when no card copy was saved.
        """
        with self.lock:
            if card:
                card_path = self.run_dir / "evidence" / f"{item_id}.card.jpg"
                if card_path.is_file():
                    return card_path.read_bytes()
            path = self.run_dir / "evidence" / f"{item_id}.jpg"
            if not path.is_file():
                return None
            return path.read_bytes()

    def read_overview(self, photo_id):
        """A photo's 768 px overview bytes, read under the lock."""
        with self.lock:
            photo = self.photos_by_id.get(photo_id)
            if photo is None or not photo.overview_path.is_file():
                return None
            return photo.overview_path.read_bytes()

    def read_card_photo(self, photo_id, item_ids):
        """Combined card JPEG: red boxes of every listed PROBLEM item
        citing this photo; plain photo when none is PROBLEM."""
        with self.lock:
            photo = self.photos_by_id.get(photo_id)
            if photo is None or not photo.check_path.is_file():
                return None
            problem = [
                item_id for item_id in item_ids
                if self.proofs[item_id].status == PROBLEM
                and self.proofs[item_id].photo_id == photo_id
            ]
            if not problem:
                return photo.check_path.read_bytes()
            boxes = []
            for item_id in problem:
                sam = self.problem_sams.get(item_id)
                if sam is not None:
                    boxes.extend(sam.boxes)
            return card_image_bytes(
                photo.check_path, boxes, frame=not boxes)

    def _voice_note_out(self, note):
        """A voice note for /status, newest first, with its audio URL."""
        out = dict(note)
        out["audio_url"] = (
            f"/api/runs/{self.run_dir.name}/notes/{note['id']}/audio"
        )
        return out

    def read_note_audio(self, note_id):
        """A stored note's WAV bytes, read under the lock."""
        with self.lock:
            note = next(
                (n for n in self.voice_notes if n["id"] == note_id), None
            )
            if note is None:
                return None
            path = self.run_dir / "notes" / note["audio"]
            if not path.is_file():
                return None
            return path.read_bytes()

    # -- persistence (callers hold the lock) --------------------------------

    def _save_locked(self):
        from pot.turnover import build_proof_result

        first_data = {
            "property": self.property["id"],
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "demo": self.demo,
            "test": self.test,
            "language": self.language,
            "tour": self.tour,
            "photos": [
                {
                    "id": photo.id,
                    "file": photo.original_name,
                    "shot_id": self.photo_shot.get(photo.id, ""),
                    "retake_items": list(self.photo_retake.get(photo.id, [])),
                    "room_id": self.assignments[photo.id].room_id,
                    "confidence": self.assignments[photo.id].confidence,
                    "seen": self.assignments[photo.id].seen,
                }
                for photo in self.photos
            ],
            "coverage": self.coverage,
            "claims": {item_id: asdict(claim) for item_id, claim in self.claims.items()},
            "other_issues": [asdict(issue) for issue in self.other_issues],
            "notes": list(self.notes),
            "resolutions": {item_id: dict(entry) for item_id, entry in self.resolutions.items()},
            "finished_at": self.finished_at,
            "voice_notes": [dict(note) for note in self.voice_notes],
            "unchecked": sorted(self.unchecked),
            "round_ended": self.round_ended,
            "skipped_shots": list(self.skipped_shots),
        }
        _write_json_atomically(self.run_dir / "first_look.json", first_data)
        host_verdict = verdict(self.checklist, self.proofs)
        proof_data = build_proof_result(
            self.checklist, self.photos, self.proofs, self.claims, self.seconds,
            self.sams, self.problem_sams, host_verdict, self.other_issues, self.notes,
        )
        _write_json_atomically(self.run_dir / "proof.json", proof_data)
        room_order = {room["id"]: i for i, room in enumerate(self.property["rooms"])}
        ordered = sorted(
            self.requests.values(),
            key=lambda r: (room_order.get(r["room_id"], len(room_order)), r["item_id"]),
        )
        _write_json_atomically(
            self.run_dir / "requests.json", {"requests": ordered, "notes": []}
        )
        _write_json_atomically(self.run_dir / "signoff.json", self.signoff)
