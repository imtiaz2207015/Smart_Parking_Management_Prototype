"""
plate_ocr.py
Two-pass plate/text OCR for the Smart Parking System.

Pass 1: run Tesseract's layout detection on a downscaled version of the
        full photo to find where any text sits in the frame.
Pass 2: crop tightly to the single best plate-shaped candidate (at full
        resolution) and re-run OCR on just that crop for a clean read.

This avoids needing a hardcoded crop region - it adapts to the plate
landing in slightly different positions across captures, at the cost of
one extra OCR pass per photo (still fast on a Pi 5).

v2 changes (fixing bad reads like "Y S 7 T SH00W L M - - - - - - - 4 7"):
- Detection pass no longer merges every text blob in the frame into one
  giant region. Busy scenes (background signage, stickers, reflections)
  were all getting lumped together with the actual plate, producing
  long garbage strings.
- Candidate boxes are now filtered by aspect ratio (plates are wide
  rectangles) and scored, and only the single best-scoring candidate is
  used - not a merge of everything detected.
- Final OCR output is validated against a plausible plate length/format
  before being accepted; anything that still looks like noise is
  rejected back to "" (caller falls back to "UNKNOWN").
"""

import re
import cv2
import pytesseract

# Detection pass: downscale for speed, --psm 11 = "sparse text, no
# particular layout" which works well for finding text blobs anywhere
# in a busy scene.
DETECT_SCALE = 0.25
DETECT_CONFIG = "--psm 6"
DETECT_MIN_CONFIDENCE = 40  # ignore low-confidence junk detections

# Recognition pass: run on the tight crop at full resolution.
# --psm 6 = "a single line of text" - plates are one line, and this is
# stricter than --psm 7 (block of text), which was letting Tesseract
# hallucinate extra lines out of noise in the crop.
RECOGNIZE_CONFIG = (
    "--psm 6 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
)

# Padding (in full-res pixels) added around the chosen text box before
# cropping, so characters aren't clipped at the edges.
CROP_PADDING = 25

# ---- Plate-shape filtering ----
# Real plates are wide rectangles. These bounds are deliberately generous
# to tolerate camera angle, but reject obviously-wrong shapes (tall
# stickers, square badges, single stray characters, etc).
MIN_ASPECT_RATIO = 0.8   # width / height
MAX_ASPECT_RATIO = 6.5
MIN_BOX_WIDTH = 40        # full-res px; smaller is almost never a real plate
MIN_BOX_HEIGHT = 20

# ---- Output validation ----
# After OCR, reject results that don't look like a plausible plate string.
MIN_PLATE_CHARS = 4    # ignoring spaces/hyphens
MAX_PLATE_CHARS = 12


def _detect_text_boxes(gray_full):
    """Run a fast, downscaled detection pass to find candidate text regions.

    Returns a list of (x, y, w, h, conf) boxes in FULL-RESOLUTION
    coordinates, one per detected text blob (not merged).
    """
    small = cv2.resize(gray_full, None, fx=DETECT_SCALE, fy=DETECT_SCALE)
    data = pytesseract.image_to_data(
        small, output_type=pytesseract.Output.DICT,
         config=DETECT_CONFIG
    )

    boxes = []
    for i, txt in enumerate(data["text"]):
        if not txt.strip():
            continue
        conf = int(data["conf"][i]) if data["conf"][i] != "-1" else -1
        if conf < DETECT_MIN_CONFIDENCE:
            continue
        x = int(data["left"][i] / DETECT_SCALE)
        y = int(data["top"][i] / DETECT_SCALE)
        w = int(data["width"][i] / DETECT_SCALE)
        h = int(data["height"][i] / DETECT_SCALE)
        boxes.append((x, y, w, h, conf))
    return boxes


def _select_best_plate_box(boxes):
    """Pick the single most plate-shaped, highest-confidence candidate.

    Filters out boxes that are too small or have an aspect ratio that
    doesn't look like a plate, then scores survivors by confidence.
    Returns (x, y, w, h) or None if nothing plausible was found.
    """
    candidates = []
    for (x, y, w, h, conf) in boxes:
        if h == 0:
            continue
        aspect = w / h
        if w < MIN_BOX_WIDTH or h < MIN_BOX_HEIGHT:
            continue
        if not (MIN_ASPECT_RATIO <= aspect <= MAX_ASPECT_RATIO):
            continue
        candidates.append((x, y, w, h, conf))

    if not candidates:
        return None

    # Highest OCR confidence among plate-shaped candidates wins.
    best = max(candidates, key=lambda b: b[4])
    x, y, w, h, _ = best
    return x, y, w, h


def _pad_and_clamp(box, img_shape):
    """Apply CROP_PADDING to a box and clamp to image bounds."""
    x, y, w, h = box
    x2 = x + w
    y2 = y + h

    x = max(0, x - CROP_PADDING)
    y = max(0, y - CROP_PADDING)
    x2 = min(img_shape[1], x2 + CROP_PADDING)
    y2 = min(img_shape[0], y2 + CROP_PADDING)

    return x, y, x2 - x, y2 - y


def _looks_like_plate(text):
    """Sanity-check OCR output before accepting it as a real plate read.

    Rejects strings that are too short, too long, or made up mostly of
    separators/noise once whitespace and hyphens are stripped out.
    """
    stripped = re.sub(r"[\s\-]", "", text)
    if not (MIN_PLATE_CHARS <= len(stripped) <= MAX_PLATE_CHARS):
        return False
    # Require it to be purely alphanumeric after stripping separators -
    # whitelist should already guarantee this, but double-check.
    if not stripped.isalnum():
        return False
    return True


def read_plate(image_path, debug_crop_path=None):
    """Run the full two-pass OCR pipeline on a captured photo.

    Returns a cleaned, whitelisted string (letters/digits/hyphen only,
    uppercased) - or "" if no plausible plate was found / OCR failed
    validation.

    debug_crop_path: optional path to save the cropped region to, useful
    for visually checking what the detector found.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    boxes = _detect_text_boxes(gray)
    best_box = _select_best_plate_box(boxes)

    if best_box is None:
        return ""

    x, y, w, h = _pad_and_clamp(best_box, gray.shape)
    crop = gray[y:y + h, x:x + w]

    if debug_crop_path:
        cv2.imwrite(debug_crop_path, crop)

    _, thresh = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    raw_text = pytesseract.image_to_string(thresh, config=RECOGNIZE_CONFIG)
    # --psm 7 expects a single line, but strip/join defensively in case
    # Tesseract still emits stray newlines.
    lines = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]
    cleaned = " ".join(lines).upper()

    if not _looks_like_plate(cleaned):
        return ""

    return cleaned


def read_plate_string(image_path, debug_crop_path=None):
    """Thin wrapper around read_plate() for use by smart_parking.py.

    Returns the OCR'd plate string, or "UNKNOWN" if OCR found nothing
    plausible (empty/rejected string), the image couldn't be read, or
    any other error occurred during the OCR pipeline.
    """
    try:
        result = read_plate(image_path, debug_crop_path=debug_crop_path)
    except Exception:
        return "UNKNOWN"
    return result if result else "UNKNOWN"


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 plate_ocr.py <path_to_image>")
        sys.exit(1)

    result = read_plate(sys.argv[1], debug_crop_path="last_ocr_crop.jpg")
    print(f"Detected text: {result!r}")
    print("Debug crop saved to: last_ocr_crop.jpg")

plate_ocr.py