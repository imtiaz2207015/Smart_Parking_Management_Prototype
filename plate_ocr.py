

import re
import cv2
import pytesseract


DETECT_SCALE = 0.25
DETECT_CONFIG = "--psm 6"
DETECT_MIN_CONFIDENCE = 40  # ignore low-confidence junk detections


RECOGNIZE_CONFIG = (
    "--psm 6 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
)


CROP_PADDING = 25


MIN_ASPECT_RATIO = 0.8   
MAX_ASPECT_RATIO = 6.5
MIN_BOX_WIDTH = 40        
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
   
    stripped = re.sub(r"[\s\-]", "", text)
    if not (MIN_PLATE_CHARS <= len(stripped) <= MAX_PLATE_CHARS):
        return False
    # Require it to be purely alphanumeric after stripping separators -
    # whitelist should already guarantee this, but double-check.
    if not stripped.isalnum():
        return False
    return True


def read_plate(image_path, debug_crop_path=None):
    
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
   
    lines = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]
    cleaned = " ".join(lines).upper()

    if not _looks_like_plate(cleaned):
        return ""

    return cleaned


def read_plate_string(image_path, debug_crop_path=None):
    
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

