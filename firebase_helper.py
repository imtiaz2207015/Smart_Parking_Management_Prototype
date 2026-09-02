"""
firebase_helper.py
Handles all Firestore read/write operations for the Smart Parking System.

Firestore structure:
  slots (collection): slot_1, slot_2, slot_3
    -> { status: "occupied" | "empty", updated_at: <timestamp> }

  vehicle_logs (collection): auto-id
    -> { plate_number, entry_time, exit_time, status: "parked" | "exited" | "unresolved_exit" }
    -> exit records may also carry exit_plate_ocr (raw OCR text from the exit
       photo, kept for audit/cross-checking - never used to overwrite
       plate_number)

Exit matching:
  Multiple cars can be parked (entered but not yet exited) at the same time,
  so exits are resolved by fuzzy-matching the exit photo's OCR'd plate
  against all currently "parked" vehicle_logs records (see
  get_all_parked_vehicles() + smart_parking.py's find_best_plate_match()).
  If no confident match is found, the exit is logged as "unresolved_exit"
  via log_unresolved_exit() rather than guessing which parked car left -
  the gate still opens, and admin reconciles it manually via the dashboard.
"""

import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timezone

# ---- Initialize Firebase ----
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()


def initialize_slots(slot_ids=("slot_1", "slot_2", "slot_3")):
    """
    Create/reset all slot documents to 'empty'.
    Run this once at system startup or manually to seed Firestore.
    """
    for slot_id in slot_ids:
        db.collection("slots").document(slot_id).set({
            "status": "empty",
            "updated_at": datetime.now(timezone.utc)
        })
    print(f"Initialized {len(slot_ids)} slots: {', '.join(slot_ids)}")


def update_slot_status(slot_id, status):
    """
    Update a single slot's status.
    slot_id: e.g. "slot_1"
    status: "occupied" or "empty"
    """
    db.collection("slots").document(slot_id).set({
        "status": status,
        "updated_at": datetime.now(timezone.utc)
    }, merge=True)
    print(f"[Firestore] {slot_id} -> {status}")


def log_vehicle_entry(plate_number="UNKNOWN", entry_time=None):
    """
    Create a new vehicle_logs entry when a car enters.

    entry_time: optional datetime (should be timezone-aware UTC) marking the
    moment the car was actually confirmed/photographed. If omitted, falls
    back to the moment this function runs.

    Returns the new document ID.
    """
    if entry_time is None:
        entry_time = datetime.now(timezone.utc)

    doc_ref = db.collection("vehicle_logs").document()
    doc_ref.set({
        "plate_number": plate_number,
        "entry_time": entry_time,
        "exit_time": None,
        "status": "parked"
    })
    print(f"[Firestore] Logged entry for {plate_number} (doc: {doc_ref.id}) at {entry_time.isoformat()}")
    return doc_ref.id


def get_all_parked_vehicles():
    """
    Returns a list of all vehicle_logs records with status == 'parked'.
    Each dict includes 'id' plus the document fields.
    Used for fuzzy plate-matching on exit, since multiple cars can be
    parked (entered but not yet exited) at the same time.
    """
    query = db.collection("vehicle_logs").where("status", "==", "parked")
    results = list(query.stream())

    records = []
    for doc in results:
        data = doc.to_dict()
        data["id"] = doc.id
        records.append(data)
    return records


def log_vehicle_exit_by_id(doc_id, exit_time=None, exit_plate_ocr=None):
    """
    Mark a specific vehicle_logs record as exited. Used once the caller has
    already determined which doc_id to close (e.g. via plate matching in
    smart_parking.py).

    exit_plate_ocr: optional OCR reading from the exit photo, stored purely
    for audit/cross-checking against the entry plate_number. Does NOT
    overwrite plate_number.
    """
    if exit_time is None:
        exit_time = datetime.now(timezone.utc)

    update_data = {
        "exit_time": exit_time,
        "status": "exited"
    }
    if exit_plate_ocr is not None:
        update_data["exit_plate_ocr"] = exit_plate_ocr

    db.collection("vehicle_logs").document(doc_id).update(update_data)
    print(f"[Firestore] Logged exit for doc {doc_id} at {exit_time.isoformat()}")


def log_unresolved_exit(exit_time=None, exit_plate_ocr=None):
    """
    Create a standalone record for an exit that couldn't be matched to any
    parked vehicle (OCR failed, or no fuzzy match cleared the threshold).
    The gate still opens (handled by the caller) - this just leaves a
    breadcrumb so admin can manually reconcile which parked car actually left.
    """
    if exit_time is None:
        exit_time = datetime.now(timezone.utc)

    doc_ref = db.collection("vehicle_logs").document()
    doc_ref.set({
        "plate_number": "UNKNOWN",
        "entry_time": None,
        "exit_time": exit_time,
        "exit_plate_ocr": exit_plate_ocr,
        "status": "unresolved_exit"
    })
    print(f"[Firestore] Logged UNRESOLVED exit (doc: {doc_ref.id}) at {exit_time.isoformat()} "
          f"- admin needs to manually match this to a parked car")
    return doc_ref.id

firebase_helper.py