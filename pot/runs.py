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
from datetime import datetime
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


class OldPhotoError(ValueError):
    """The photo's camera time is before the run started (foto antigua)."""


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

    def __init__(self, run_dir, property, checklist, tour=None, started_at=None, demo=False):
        self.run_dir = Path(run_dir)
        self.property = property
        self.checklist = checklist
        self.items_by_id = {item.id: item for item in checklist}
        self.rooms = {room["id"]: room for room in property["rooms"]}
        self.tour = tour or {"shots": []}
        self.demo = demo
        self.started_at = started_at or datetime.now()
        self.lock = threading.Lock()
        self.photos = []
        self.photos_by_id = {}
        self.photo_seq = {}
        self.photo_shot = {}
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
        self.calls = []
        self.checking = set()
        self.photo_seconds = {}
        self.signoff = {
            "decision": None,
            "notes": "",
            "time": None,
            "items": [],
            "open_items": [],
        }

    @classmethod
    def create(cls, run_dir, property_id, tour=None, demo=False):
        """Start a fresh run: seed state and write the initial files."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        property = load_property(property_id)
        state = cls(run_dir, property, build_checklist(property), tour=tour, demo=demo)
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

    def claim_photo(self, upload_path, shot_id):
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
            if taken is not None and taken < self.started_at:
                raise OldPhotoError(
                    "foto antigua: this photo was taken before the tour started, "
                    "take a new one"
                )
            return self._ingest_locked(upload_path, room_id, shot_id)

    def claim_photo_for_room(self, upload_path, room_id):
        """Number and save a photo for a room (batch/CLI path, no tour)."""
        with self.lock:
            if room_id not in self.rooms:
                raise ValueError(f"Unknown room '{room_id}'")
            return self._ingest_locked(upload_path, room_id, "")

    def _ingest_locked(self, upload_path, room_id, shot_id):
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
            for item_id in fresh_ids:
                if item_id in fresh["claims"]:
                    self.claims[item_id] = fresh["claims"][item_id]
                if item_id in fresh["seconds"]:
                    self.seconds[item_id] = fresh["seconds"][item_id]
                if item_id in fresh["sams"]:
                    self.sams[item_id] = fresh["sams"][item_id]
            self.decisions.update(fresh.get("decisions", {}))
            computed = prove_all(
                self.checklist, self.claims, self.seconds, self.sams,
                self.assignments, self.coverage,
                [photo.id for photo in self.photos], self.decisions, self.rooms,
            )
            changes = {}
            for item_id in fresh_ids:
                new = computed[item_id]
                current = self.proofs[item_id]
                if seq < self.decided_seq.get(item_id, -1):
                    continue
                if new.status in (PROVEN, PROBLEM) or current.status not in (PROVEN, PROBLEM):
                    self.proofs[item_id] = new
                    self.decided_seq[item_id] = seq
                    if new.status != current.status:
                        changes[item_id] = (current.status, new.status)
            for item_id, sam in fresh.get("problem_sams", {}).items():
                if self.proofs.get(item_id) is not None and self.proofs[item_id].status == PROBLEM:
                    self.problem_sams[item_id] = sam
            self.problem_sams = {
                item_id: sam for item_id, sam in self.problem_sams.items()
                if self.proofs[item_id].status == PROBLEM
            }
            self.other_issues = prune_other_issues(
                self.other_issues + fresh.get("issues", []), self.proofs, self.claims
            )
            self.notes.extend(fresh.get("notes", []))
            self.checking.discard(photo_id)
            elapsed = fresh.get("elapsed_seconds")
            if elapsed is not None:
                self.photo_seconds[photo_id] = elapsed
                self.notes.append(f"Photo {photo_id} checked in {elapsed:.1f} s")
            if changes:
                save_evidence(
                    self.run_dir, self.proofs, self.items_by_id, self.photos_by_id,
                    self.sams, self.problem_sams, item_ids=set(changes),
                )
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

    def mark_failed(self, photo_id, note):
        """Drop a photo from checking after a worker error."""
        with self.lock:
            self.checking.discard(photo_id)
            self.notes.append(note)
            self._save_locked()

    def store_requests(self, requests, notes):
        """Merge per-shot requests; drop requests for proven items."""
        with self.lock:
            for request in requests:
                self.requests[request["item_id"]] = request
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
            self._save_locked()
            return dict(self.signoff)

    def send_back(self, item_ids, notes):
        """Host sends items back: they go to needs-photo with the note."""
        with self.lock:
            known = [i for i in item_ids if i in self.items_by_id]
            for item_id in known:
                self.proofs[item_id] = ProofItem(
                    item_id, NEEDS_PROOF, notes or "Host asked for a new photo", ""
                )
                self.decided_seq[item_id] = len(self.photos)
                self.requests.pop(item_id, None)
                stale = self.run_dir / "evidence" / f"{item_id}.jpg"
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
            self._save_locked()
            return dict(self.signoff)

    # -- reads (all from memory, never from files) --------------------------

    def agent_state(self):
        """needs_input / awaiting_signoff / done."""
        with self.lock:
            if self.signoff.get("decision") == "approve":
                return DONE
            return self._agent_state_locked()

    def _agent_state_locked(self):
        for item in self.checklist:
            if item.critical and self.proofs[item.id].status != PROVEN:
                return NEEDS_INPUT
        return AWAITING_SIGNOFF

    def _taken_shots_locked(self):
        return {self.photo_shot[photo_id] for photo_id in self.photo_shot}

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
            current = next(
                (shot for shot in self.tour["shots"] if shot["shot_id"] not in taken),
                None,
            )
            rooms_done = 0
            for room in self.property["rooms"]:
                room_shots = [s for s in self.tour["shots"] if s["room_id"] == room["id"]]
                if room_shots and all(s["shot_id"] in taken for s in room_shots):
                    rooms_done += 1
            return {
                "run_id": self.run_dir.name,
                "property": self.property["id"],
                "agent_state": self._agent_state_locked()
                if self.signoff.get("decision") != "approve" else DONE,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "progress": {
                    "shots_taken": len(taken),
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
                "checking": sorted(self.checking),
                "photo_seconds": dict(self.photo_seconds),
                "signoff": dict(self.signoff),
            }

    def _item_status_locked(self, item, shot_of_item, taken):
        proof = self.proofs[item.id]
        if proof.status == PROVEN:
            display = PROVEN
        elif proof.status == PROBLEM:
            display = PROBLEM
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
            "reason": proof.reason,
            "photo_id": proof.photo_id,
            "has_evidence": proof.status in (PROVEN, PROBLEM) and bool(proof.photo_id),
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

    def read_evidence(self, item_id):
        """Evidence JPEG bytes, read under the lock (never mid-replace)."""
        with self.lock:
            path = self.run_dir / "evidence" / f"{item_id}.jpg"
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
            "tour": self.tour,
            "photos": [
                {
                    "id": photo.id,
                    "file": photo.original_name,
                    "shot_id": self.photo_shot.get(photo.id, ""),
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
