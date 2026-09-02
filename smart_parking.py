"""
Smart Parking Slot Detection System
------------------------------------
Hardware:
- Entry IR sensor   -> GPIO 23 (physical pin 16)
- Exit IR sensor    -> GPIO 24 (physical pin 18)
- Shared Gate Servo -> GPIO 18 (physical pin 12)
- 3x Parking Slot IR sensors:
    Slot 1 -> GPIO 17 (physical pin 11)
    Slot 2 -> GPIO 27 (physical pin 13)
    Slot 3 -> GPIO 22 (physical pin 15)
- IMX519 Camera (Arducam, AF lens) -> CSI, lens subdev /dev/v4l-subdev3

Gate behavior:
- Idle (no car at entry or exit)  -> servo at 0 degrees
- Car detected at ENTRY or EXIT   -> servo moves to 90 degrees
- After hold time, returns to 0 degrees automatically
- Entry and exit share the same physical gate/servo

Camera:
- Autofocus tuning is not available for this IMX519/AK7375 combo on this
  OS image (no rpi.af block in the libcamera IPA tuning file), so focus is
  locked to a fixed value confirmed sharp at both the entry and exit
  sensor positions. Set once at startup via set_camera_focus().

Plate OCR + exit matching:
- On entry, the plate is OCR'd and stored on the new vehicle_logs record
  (falls back to "UNKNOWN" if OCR fails - admin corrects it via the
  dashboard shortly after entry).
- On exit, the plate is OCR'd again and fuzzy-matched (difflib) against
  every currently "parked" vehicle_logs record, since multiple cars can be
  parked at once and we can't just trust "whichever entered last." If a
  match clears PLATE_MATCH_THRESHOLD, that record is closed out. If not,
  the gate still opens (physical access shouldn't block on OCR), but the
  exit is logged as "unresolved_exit" for admin to manually reconcile.

NOTE: If ENTRY or EXIT sensors are still unreliable on your hardware,
set ENTRY_ENABLED / EXIT_ENABLED to False below rather than commenting
out code. Run sensor_test.py on that pin first to confirm it's fixed
before flipping it back to True.
"""

from gpiozero import DigitalInputDevice, AngularServo
from time import sleep
from signal import pause
from datetime import datetime, timezone
from firebase_helper import (
    update_slot_status,
    log_vehicle_entry,
    get_all_parked_vehicles,
    log_vehicle_exit_by_id,
    log_unresolved_exit,
)
from plate_ocr import read_plate_string
import difflib
import threading
import subprocess
import os

# ---------------- CONFIGURATION ----------------

ENTRY_IR_PIN = 23      # GPIO23 / physical pin 16
EXIT_IR_PIN = 24       # GPIO24 / physical pin 18
GATE_SERVO_PIN = 18    # GPIO18 / physical pin 12

SLOT_PINS = {
    "Slot 1": 17,       # GPIO17 / physical pin 11
    "Slot 2": 27,       # GPIO27 / physical pin 13
    "Slot 3": 22,       # GPIO22 / physical pin 15
}

# Maps the human-readable slot names above to the Firestore document IDs
# used by firebase_helper / the dashboards (slot_1, slot_2, slot_3).
SLOT_FIRESTORE_IDS = {
    "Slot 1": "slot_1",
    "Slot 2": "slot_2",
    "Slot 3": "slot_3",
}

# Flip these to False if a sensor is still misbehaving in the lab.
# Test with sensor_test.py first before re-enabling.
ENTRY_ENABLED = True
EXIT_ENABLED = True

GATE_CLOSED_ANGLE = 0
GATE_OPEN_ANGLE = 90

GATE_OPEN_HOLD_SECONDS = 5      # how long the gate stays open
GATE_COOLDOWN_SECONDS = 3       # ignore re-triggers right after closing
SLOT_POLL_INTERVAL = 0.5        # how often to check slot sensors
ENTRY_EXIT_POLL_INTERVAL = 0.2  # how often to check entry/exit sensors

# A car must be continuously detected for this long before we trust it's
# really there (filters out IR noise/flicker). Only after this holds do we
# take a photo, and only after the photo is taken does the gate open.
DETECTION_CONFIRM_SECONDS = 5

# Minimum difflib similarity ratio (0-1) for an exit-photo OCR plate to be
# considered a match against a parked vehicle's entry plate_number. Below
# this, the exit is logged as unresolved rather than guessing which parked
# car actually left.
PLATE_MATCH_THRESHOLD = 0.6

PHOTO_DIR = "captures"  # created if missing; entry_*.jpg / exit_*.jpg saved here

# ---------------- CAMERA CONFIGURATION ----------------

CAMERA_LENS_DEVICE = "/dev/v4l-subdev3"  # AK7375 VCM subdev (IMX519 lens)
FOCUS_VALUE = 2000  # confirmed sharp at both entry and exit sensor positions
CAPTURE_TIMEOUT_MS = 1000  # rpicam-still capture delay


def set_camera_focus(value=FOCUS_VALUE):
    """Lock the IMX519's lens motor to a fixed focus position.

    Autofocus isn't usable here (no AF algorithm in the current libcamera
    tuning file for this sensor), so focus is set once, manually, via the
    lens subdevice directly. Both the entry and exit sensor positions were
    tested and confirmed sharp at this value, so no per-capture switching
    is needed.
    """
    subprocess.run(
        ["v4l2-ctl", "-d", CAMERA_LENS_DEVICE, "-c", f"focus_absolute={value}"],
        check=True,
    )


def capture_photo(filename):
    """Capture a still image using the currently locked focus position."""
    subprocess.run(
        ["rpicam-still", "-t", str(CAPTURE_TIMEOUT_MS), "-o", filename],
        check=True,
    )


def capture_timestamped_photo(source_name):
    """Capture a photo for ENTRY or EXIT, saved with a timestamped filename.

    Returns (filepath, capture_time) on success - capture_time is a
    timezone-aware UTC datetime marking the moment of capture, meant to be
    passed straight into log_vehicle_entry()/log_vehicle_exit_by_id() so
    the Firestore record reflects when the car was actually confirmed and
    photographed, not whenever the Firestore write happens to run.

    Returns (None, None) if the capture failed (caller decides how to
    handle that - e.g. whether to still open the gate).
    """
    os.makedirs(PHOTO_DIR, exist_ok=True)
    capture_time = datetime.now(timezone.utc)
    timestamp = capture_time.strftime("%Y%m%d_%H%M%S")
    prefix = "entry" if source_name == "ENTRY" else "exit"
    filepath = os.path.join(PHOTO_DIR, f"{prefix}_{timestamp}.jpg")

    try:
        capture_photo(filepath)
        print(f"[CAMERA] {source_name} photo captured -> {filepath}")
        return filepath, capture_time
    except Exception as e:
        print(f"[CAMERA] ERROR capturing {source_name} photo: {e}")
        return None, None


def find_best_plate_match(ocr_text, parked_vehicles):
    """Fuzzy-match ocr_text against the plate_number of each currently
    parked vehicle, using difflib's SequenceMatcher ratio (0-1).

    Returns (doc_id, score) for the best match, or (None, 0.0) if
    ocr_text is empty/UNKNOWN, no parked vehicles exist, or no parked
    vehicle has a usable plate_number to compare against.
    """
    if not ocr_text or ocr_text == "UNKNOWN" or not parked_vehicles:
        return None, 0.0

    best_id = None
    best_score = 0.0
    for vehicle in parked_vehicles:
        candidate_plate = vehicle.get("plate_number", "")
        if not candidate_plate or candidate_plate == "UNKNOWN":
            continue
        score = difflib.SequenceMatcher(None, ocr_text.upper(), candidate_plate.upper()).ratio()
        if score > best_score:
            best_score = score
            best_id = vehicle["id"]

    return best_id, best_score


# ---------------- HARDWARE SETUP ----------------

# NOTE: pigpio is not available on this Raspberry Pi OS version, so we use
# gpiozero's default pin factory, which uses lgpio on modern Raspberry Pi OS.
# bounce_time=0.3 gives software debounce on top of any hardware fix
# (pot adjustment / wiring) already done at the sensor itself.

entry_ir = DigitalInputDevice(ENTRY_IR_PIN, pull_up=True, bounce_time=0.3) if ENTRY_ENABLED else None
exit_ir = DigitalInputDevice(EXIT_IR_PIN, pull_up=True, bounce_time=0.3) if EXIT_ENABLED else None

gate_servo = AngularServo(
    GATE_SERVO_PIN,
    min_angle=0,
    max_angle=90,
    min_pulse_width=0.0010,   # 1.0 ms - conservative starting point
    max_pulse_width=0.0018,   # 1.8 ms - conservative starting point
)

slot_sensors = {
    name: DigitalInputDevice(pin, pull_up=True, bounce_time=0.3) for name, pin in SLOT_PINS.items()
}

slot_last_state = {name: None for name in SLOT_PINS}

gate_busy = False
gate_lock = threading.Lock()


# ---------------- DETECTION CONFIRMATION ----------------

def confirm_still_detected(sensor, hold_seconds=DETECTION_CONFIRM_SECONDS,
                            poll_interval=ENTRY_EXIT_POLL_INTERVAL):
    """Re-poll `sensor` for `hold_seconds`, bailing out early if it drops.

    Returns True only if the sensor stayed active the whole time (real car,
    not noise/flicker). Returns False the moment it goes inactive.
    """
    elapsed = 0.0
    while elapsed < hold_seconds:
        if not sensor.is_active:
            return False
        sleep(poll_interval)
        elapsed += poll_interval
    return sensor.is_active


# ---------------- GATE LOGIC ----------------

def trigger_gate(source_name):
    """Open the gate to 90 degrees, hold, then return to 0 degrees.

    Returns True if this call actually opened the gate, False if it was
    skipped because the gate was already busy (so callers can log it).
    """
    global gate_busy

    with gate_lock:
        if gate_busy:
            return False
        gate_busy = True

    print(f"[GATE] Car detected at {source_name} -> opening gate (90 deg)")
    gate_servo.angle = GATE_OPEN_ANGLE
    sleep(GATE_OPEN_HOLD_SECONDS)

    print("[GATE] Closing gate (0 deg)")
    gate_servo.angle = GATE_CLOSED_ANGLE
    sleep(GATE_COOLDOWN_SECONDS)

    gate_busy = False
    return True


def watch_entry():
    was_active = False
    while True:
        active = entry_ir.is_active
        if active and not was_active:
            # Rising edge: sensor just went from clear -> detected.
            print(f"[ENTRY] Object detected, confirming for {DETECTION_CONFIRM_SECONDS}s...")

            if not confirm_still_detected(entry_ir):
                print("[ENTRY] Detection dropped before confirm window - treated as noise, ignored")
                was_active = entry_ir.is_active
                sleep(ENTRY_EXIT_POLL_INTERVAL)
                continue

            print("[ENTRY] Confirmed - capturing photo before opening gate")
            photo_path, capture_time = capture_timestamped_photo("ENTRY")

            # Run OCR on the entry photo. Falls back to "UNKNOWN" if OCR
            # finds nothing or the photo capture failed - admin corrects
            # any UNKNOWN plates via the dashboard shortly after entry.
            plate_text = "UNKNOWN"
            if photo_path:
                try:
                    plate_text = read_plate_string(photo_path)
                    print(f"[ENTRY] OCR read: {plate_text}")
                except Exception as e:
                    print(f"[OCR] ERROR reading entry plate: {e}")

            if trigger_gate("ENTRY"):
                try:
                    # capture_time (when the photo was actually taken) is used
                    # as entry_time so the dashboard reflects the confirmed
                    # moment, not whenever this Firestore call happens to run.
                    log_vehicle_entry(plate_text, entry_time=capture_time)
                except Exception as e:
                    print(f"[Firestore] ERROR logging entry: {e}")
            else:
                print("[ENTRY] Confirmed, but gate busy (likely EXIT holding it) - skipped")
        was_active = entry_ir.is_active
        sleep(ENTRY_EXIT_POLL_INTERVAL)


def watch_exit():
    was_active = False
    while True:
        active = exit_ir.is_active
        if active and not was_active:
            # Rising edge: sensor just went from clear -> detected.
            print(f"[EXIT] Object detected, confirming for {DETECTION_CONFIRM_SECONDS}s...")

            if not confirm_still_detected(exit_ir):
                print("[EXIT] Detection dropped before confirm window - treated as noise, ignored")
                was_active = exit_ir.is_active
                sleep(ENTRY_EXIT_POLL_INTERVAL)
                continue

            print("[EXIT] Confirmed - capturing photo before opening gate")
            photo_path, capture_time = capture_timestamped_photo("EXIT")

            # OCR the exit photo - this result is used to find which parked
            # car is leaving (fuzzy match below), and is also stored as an
            # audit field on whichever record gets closed.
            exit_plate_text = "UNKNOWN"
            if photo_path:
                try:
                    exit_plate_text = read_plate_string(photo_path)
                    print(f"[EXIT] OCR read: {exit_plate_text}")
                except Exception as e:
                    print(f"[OCR] ERROR reading exit plate: {e}")

            if trigger_gate("EXIT"):
                try:
                    parked_vehicles = get_all_parked_vehicles()
                    match_id, match_score = find_best_plate_match(exit_plate_text, parked_vehicles)

                    if match_id and match_score >= PLATE_MATCH_THRESHOLD:
                        print(f"[EXIT] Matched to parked vehicle (doc {match_id}, score {match_score:.2f})")
                        log_vehicle_exit_by_id(
                            doc_id=match_id,
                            exit_time=capture_time,
                            exit_plate_ocr=exit_plate_text,
                        )
                    else:
                        print(f"[EXIT] No confident plate match (best score {match_score:.2f}) "
                              f"- logging as unresolved for admin review")
                        log_unresolved_exit(
                            exit_time=capture_time,
                            exit_plate_ocr=exit_plate_text,
                        )
                except Exception as e:
                    print(f"[Firestore] ERROR logging exit: {e}")
            else:
                print("[EXIT] Confirmed, but gate busy (likely ENTRY holding it) - skipped")
        was_active = exit_ir.is_active
        sleep(ENTRY_EXIT_POLL_INTERVAL)


# ---------------- SLOT MONITORING LOGIC ----------------

def watch_slots():
    """Continuously poll all slot sensors and report occupied/empty changes."""
    while True:
        for name, sensor in slot_sensors.items():
            occupied = sensor.is_active
            if occupied != slot_last_state[name]:
                status = "OCCUPIED" if occupied else "EMPTY"
                print(f"[SLOT] {name} -> {status}")
                slot_last_state[name] = occupied

                # ---- Firestore sync ----
                firestore_slot_id = SLOT_FIRESTORE_IDS[name]
                firestore_status = "occupied" if occupied else "empty"
                try:
                    update_slot_status(firestore_slot_id, firestore_status)
                except Exception as e:
                    print(f"[Firestore] ERROR updating {firestore_slot_id}: {e}")

        sleep(SLOT_POLL_INTERVAL)


# ---------------- MAIN ----------------

def main():
    print("Smart Parking System starting...")
    print("Entry IR pin:", ENTRY_IR_PIN, f"({'enabled' if ENTRY_ENABLED else 'DISABLED'})")
    print("Exit IR pin:", EXIT_IR_PIN, f"({'enabled' if EXIT_ENABLED else 'DISABLED'})")
    print("Gate servo pin:", GATE_SERVO_PIN)
    print("Slot pins:", SLOT_PINS)

    gate_servo.angle = GATE_CLOSED_ANGLE  # ensure gate starts closed
    sleep(1)
    print("Gate initialized at 0 degrees (closed).\n")

    try:
        set_camera_focus()
        print(f"Camera focus locked at {FOCUS_VALUE}.\n")
    except Exception as e:
        print(f"[Camera] WARNING: could not set focus at startup: {e}\n")

    threads = [threading.Thread(target=watch_slots, daemon=True)]

    if ENTRY_ENABLED:
        threads.append(threading.Thread(target=watch_entry, daemon=True))
    else:
        print("[INFO] ENTRY sensor disabled in config - not monitoring.")

    if EXIT_ENABLED:
        threads.append(threading.Thread(target=watch_exit, daemon=True))
    else:
        print("[INFO] EXIT sensor disabled in config - not monitoring.")

    for t in threads:
        t.start()

    print("\nSystem running. Press Ctrl+C to stop.\n")
    pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutting down. Returning gate to 0 degrees.")
        gate_servo.angle = GATE_CLOSED_ANGLE
        sleep(1)

smart_parking.py