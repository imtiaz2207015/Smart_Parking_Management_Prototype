
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
  - git clone https://github.com/imtiaz2207015/Smart_Parking_Management_Prototype.git cd Smart_Parking_Management_Prototype

2. Install system packages (example for Debian/Raspbian):
   - sudo apt update sudo apt install -y python3-pip tesseract-ocr libatlas-base-dev v4l-utils

3. Install Python dependencies:
   - python3 -m pip install --upgrade pip python3 -m pip install firebase-admin opencv-python pytesseract gpiozero

     
4. Firebase service account (server-side):
- Create a Firebase project and a service account key for the project.
- Download the service account JSON and place it at the project root with the filename:
  ```
  firebase-key.json
  ```
- WARNING: Do NOT commit this file to source control.

5. Web Firebase configuration (client-side):
- The web UI (admin.html and index.html) already include a Firebase config object.
- To use your own Firebase project, replace the `firebaseConfig` object values in both HTML files with your project's values from the Firebase Console (Project Settings -> SDK setup).

6. Ensure camera focus device:
- The code uses `/dev/v4l-subdev3` to set lens focus. Confirm the correct device on your board or update `CAMERA_LENS_DEVICE` in `smart_parking.py`.

Initialize Firestore slots (one-time)
------------------------------------
You can initialize slots from Python:python3 -c "from firebase_helper import initialize_slots; initialize_slots()"

This writes documents `slot_1`, `slot_2`, `slot_3` to Firestore with status `empty`.

Running the system
------------------
- Start the core parking process on the Pi (this requires hardware present and correct permissions):sudo python3 smart_parking.py
  Note: Depending on your GPIO and camera setup you may need to run as root or with appropriate user groups.

- Serve the web UI locally for testing:Serve static files at http://<pi-ip>:8000/
python3 -m http.server 8000
Then open `/index.html` or `/admin.html` in a browser on the same network. For production, host the HTML on any static host (or use Firebase Hosting).

Useful scripts & utilities
-------------------------
- plate_ocr.py
- CLI usage:
  ```
  python3 plate_ocr.py <path_to_image>
  ```
- Returns OCR result and writes a debug crop to `last_ocr_crop.jpg`.
- Use `read_plate_string(path)` in code — it returns `"UNKNOWN"` on failure.

- firebase_helper.py
- `initialize_slots(...)` — create default slot documents
- `update_slot_status(slot_id, status)` — update a slot
- `log_vehicle_entry(plate_number, entry_time)` — create parked record
- `get_all_parked_vehicles()` — returns list of parked records
- `log_vehicle_exit_by_id(doc_id, exit_time, exit_plate_ocr)` — close a vehicle log
- `log_unresolved_exit(...)` — log exit when no match is confident

Tuning OCR and camera
---------------------
- plate_ocr.py contains parameters near the top:
- DETECT_SCALE, DETECT_MIN_CONFIDENCE, RECOGNIZE_CONFIG, MIN/MAX aspect ratios, MIN_BOX_WIDTH/HEIGHT, MIN_PLATE_CHARS
- If OCR produces poor results:
- Increase camera image quality / lighting
- Adjust focus value (FOCUS_VALUE in smart_parking.py) and ensure v4l2 device is correct
- Tune Tesseract psm (--psm) and whitelist characters in RECOGNIZE_CONFIG
- Save debug crops (the code writes crop images if `debug_crop_path` is given) and inspect them to determine issues

Troubleshooting
---------------
- Camera capture failures:
- Ensure `rpicam-still` (or chosen capture tool) is installed and functional.
- Confirm camera is enabled in Raspberry Pi configuration and accessible.

- Permissions:
- Accessing GPIO and camera devices may require root or group membership. Try `sudo` or adjust group permissions.

- Firebase errors:
- Check that `firebase-key.json` is valid and that Firestore is enabled in your project.
- For web UI, ensure the Firebase config in the HTML matches the intended project and that Firestore rules allow the client actions used (admin page expects authenticated access).

- OCR returns empty or "UNKNOWN":
- Inspect debug crop saved by plate_ocr.py or check `captures/` images to see how the plate looks.
- Improve lighting, framing, or adjust detection parameters.

Contributing
------------
This repo is a prototype. If you want to extend it:
- Improve robustness of OCR (e.g., use a detection model + classification pipeline)
- Add unit tests / integration tests
- Add a proper backend API (instead of direct Firestore access) to improve security
- Add Docker support for consistent runtime environments

Please open issues or pull requests with improvements.

License
-------
MIT License — see LICENSE file (or create one) for details.

Security & privacy
------------------
- Do not commit `firebase-key.json` or any private credentials to the repository.
- The admin dashboard uses Firebase Authentication — choose strong passwords and restrict access.
- Plate OCR images are captured and stored locally (captures/) and some OCR strings are written to Firestore; treat these as sensitive in production.

---

If you'd like, I can:
- Generate a basic systemd service file to run smart_parking.py at startup.
- Add a small Python helper script to initialize Firestore and rotate camera focus.
- Create a .gitignore that excludes firebase-key.json and captures/.
