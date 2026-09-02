
# Smart Parking Management — Prototype

A small prototype for a smart parking system that combines Raspberry Pi GPIO sensors, a CSI camera (IMX519), OCR-based license plate recognition, and Firestore for realtime telemetry and vehicle logs. The project includes a lightweight admin dashboard and a public live-status page.

This README explains the project layout, hardware and software requirements, setup steps, how to run the system, and troubleshooting/tuning tips.

---

Table of contents
- Project overview
- Features
- Repository layout
- Hardware (wiring / pins)
- Firestore structure (data model)
- Software prerequisites
- Installation & setup
- Running the system
- Useful scripts & tools
- Tuning OCR and camera
- Troubleshooting
- Contributing
- License

---

Project overview
----------------
This prototype monitors three parking slots and controls a shared entry/exit gate. When a vehicle arrives:
- Entry: capture a photo, OCR the plate, log an entry record to Firestore and open the gate.
- Exit: capture a photo, OCR the plate, attempt to fuzzy-match against parked vehicles; if matched the vehicle record is closed, otherwise an unresolved exit is logged for manual reconciliation.

A simple admin dashboard (admin.html) provides authentication-backed access to vehicle logs and allows manual corrections. A public live view (index.html) displays slot occupancy in realtime.

Features
--------
- Realtime slot occupancy updates synchronized with Firestore
- Plate OCR using Tesseract (pytesseract + OpenCV)
- Entry/Exit capture with timestamped photos
- Fuzzy matching for exit plate resolution
- Web admin UI for reviewing and editing logs
- Simple public live status page

Repository layout
-----------------
- firebase_helper.py — Firestore read/write helpers (initialize slots, log entry/exit, query parked vehicles)
- plate_ocr.py — OpenCV + Tesseract-based plate detection and recognition utilities
- smart_parking.py — Main Raspberry Pi program: GPIO sensors, gate servo control, camera capture, higher-level logic
- admin.html — Admin dashboard (Firebase Auth + Firestore)
- index.html — Public live status page (Firestore)
- captures/ — runtime directory where entry/exit photos are stored (created automatically)

Hardware (GPIO mapping)
----------------------
This mapping is used in smart_parking.py:
- Entry IR sensor -> GPIO 23 (physical pin 16)
- Exit IR sensor  -> GPIO 24 (physical pin 18)
- Gate servo      -> GPIO 18 (physical pin 12)
- Slot 1 sensor   -> GPIO 17 (physical pin 11)
- Slot 2 sensor   -> GPIO 27 (physical pin 13)
- Slot 3 sensor   -> GPIO 22 (physical pin 15)

Camera:
- IMX519 (Arducam) connected to CSI
- Lens VCM sub-device: `/dev/v4l-subdev3` (used to set focus via `v4l2-ctl`)

Firestore structure
-------------------
Collection: `slots`
- Documents: `slot_1`, `slot_2`, `slot_3`
- Document fields:
  - `status`: "occupied" | "empty"
  - `updated_at`: timestamp

Collection: `vehicle_logs`
- Documents: auto-id
- Fields:
  - `plate_number` (string) — entry plate, or "UNKNOWN"
  - `entry_time` (timestamp) | null
  - `exit_time` (timestamp) | null
  - `status`: "parked" | "exited" | "unresolved_exit"
  - `exit_plate_ocr`: optional raw OCR text from exit photo (for audit)

Important: unresolved exits are written with `status: "unresolved_exit"` so admin can reconcile later.

Software prerequisites
----------------------
On the Raspberry Pi (Debian/Raspbian-based):
- Python 3.9+ (system default on recent images)
- pip
- system packages:
  - libatlas-base-dev (optional, for OpenCV performance)
  - libjpeg-dev, zlib1g-dev, libpng-dev (if building OpenCV)
  - v4l-utils (provides `v4l2-ctl`)
  - rpicam-still (or another capture utility used by the capture_photo function)
  - tesseract-ocr (system Tesseract engine) and language packs
- Python packages:
  - firebase-admin
  - opencv-python
  - pytesseract
  - gpiozero
  - difflib (part of stdlib)
  - any other required dependencies (see installation commands below)

Web UI:
- The HTML files use the Firebase JavaScript SDK (already wired in the files). The public web UI and admin page can be hosted from a static server or served with `python -m http.server` for simple testing.

Installation & setup
--------------------
1. Clone the repo to your Pi or dev machine:
