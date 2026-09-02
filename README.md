# 🅿️ Smart Parking Management — Prototype

A working prototype of a smart parking system for a small lot with 3 slots, built on a Raspberry Pi. It automates the two things a real parking gate needs to do — **let cars in and out, and know which slots are free** — without a human at the gate.

Two IR sensors watch the entry and exit points; three more watch the individual slots. When a car arrives, a camera photographs its plate, OCR reads the plate number, the gate opens automatically, and the vehicle is logged to Firestore in realtime. When a car leaves, the system re-photographs and re-reads the plate, then fuzzy-matches it against every car currently parked (since more than one car can be parked at once) to figure out *which* car is leaving and close out its record — rather than just assuming "whoever entered last is leaving now."

Two web pages sit on top of this: a **public live-status page** anyone can check to see which slots are free, and a **password-protected admin dashboard** where staff can review vehicle history and manually fix a record if the OCR got a plate wrong.

**Entry flow:** sensor detects a car → photo → OCR the plate → log entry to Firestore → open gate.
**Exit flow:** sensor detects a car → photo → OCR the plate → fuzzy-match against parked vehicles → match found closes that record, no match logs it as `unresolved_exit` for a human to sort out later (the gate still opens either way — physical access never blocks on OCR).

## ✨ Features

- Realtime slot occupancy via Firestore
- Two-pass Tesseract + OpenCV plate OCR
- Timestamped entry/exit photo capture
- Fuzzy plate matching (difflib) across multiple parked vehicles
- Firebase Auth-gated admin dashboard + public status page

## 📁 Repository layout

| File | Purpose |
|---|---|
| `smart_parking.py` | Main Pi program — sensors, gate, camera, core logic |
| `firebase_helper.py` | Firestore read/write helpers |
| `plate_ocr.py` | Plate detection & OCR |
| `admin.html` | Admin dashboard (Firebase Auth + Firestore) |
| `index.html` | Public live status page |
| `captures/` | Entry/exit photos (auto-created) |

## 🔌 Hardware (GPIO mapping)

| Component | GPIO | Physical pin |
|---|---|---|
| Entry IR sensor | 23 | 16 |
| Exit IR sensor | 24 | 18 |
| Gate servo (shared) | 18 | 12 |
| Slot 1 | 17 | 11 |
| Slot 2 | 27 | 13 |
| Slot 3 | 22 | 15 |

Camera: IMX519 (Arducam) via CSI; lens VCM at `/dev/v4l-subdev3`, focus set via `v4l2-ctl`.

## 🔥 Firestore structure

**`slots`** (`slot_1`/`slot_2`/`slot_3`): `status` (`occupied`|`empty`), `updated_at`

**`vehicle_logs`** (auto-id): `plate_number`, `entry_time`, `exit_time`, `status` (`parked`|`exited`|`unresolved_exit`), `exit_plate_ocr` (audit only)

## 🧰 Prerequisites

**Pi (Debian/Raspbian):** Python 3.9+, `tesseract-ocr`, `v4l-utils`, `rpicam-still`, and Python packages `firebase-admin opencv-python pytesseract gpiozero`.

**Web UI:** Firebase JS SDK (already wired via CDN) — serve statically, no build step needed.

## ⚙️ Setup

```bash
git clone https://github.com/imtiaz2207015/Smart_Parking_Management_Prototype.git
cd Smart_Parking_Management_Prototype
sudo apt update && sudo apt install -y python3-pip tesseract-ocr libatlas-base-dev v4l-utils
python3 -m pip install firebase-admin opencv-python pytesseract gpiozero
```

1. Add your Firebase service account key as `firebase-key.json` in the project root. **Never commit this file** — add it to `.gitignore`.
2. Replace the `firebaseConfig` object in `admin.html` and `index.html` with your own project's values (Firebase Console → Project Settings → SDK setup).
3. Confirm `/dev/v4l-subdev3` matches your board, or update `CAMERA_LENS_DEVICE` in `smart_parking.py`.
4. Initialize Firestore slots (one-time):
   ```bash
   python3 -c "from firebase_helper import initialize_slots; initialize_slots()"
   ```

## ▶️ Running

```bash
sudo python3 smart_parking.py       # core system (needs GPIO/camera access)
python3 -m http.server 8000         # serve admin.html / index.html locally
```

## 🎯 Tuning

OCR tunables live near the top of `plate_ocr.py` (`DETECT_SCALE`, `DETECT_MIN_CONFIDENCE`, aspect-ratio bounds, `RECOGNIZE_CONFIG`, etc). If OCR is unreliable: improve lighting/framing, recheck `FOCUS_VALUE` in `smart_parking.py`, or pass `debug_crop_path` to `read_plate()` and inspect what the detector picked up.

## 🩹 Troubleshooting

- **Camera fails:** confirm `rpicam-still` works standalone and the camera is enabled/detected.
- **Permissions:** GPIO/camera access may need `sudo` or the right user groups.
- **Firebase errors:** check `firebase-key.json` is valid, Firestore is enabled, and `firebaseConfig` in the HTML matches your project.
- **OCR returns `UNKNOWN`:** check `captures/` or a debug crop to see what the camera actually saw.

## 🔒 Security & privacy

Never commit `firebase-key.json`. Admin dashboard is Auth-gated — use strong passwords. Plate photos/OCR text are stored locally and in Firestore — treat as sensitive data in production.

## 📄 License

MIT — see `LICENSE`.
