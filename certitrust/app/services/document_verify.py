import cv2
import numpy as np
import pytesseract
import re
from pdf2image import convert_from_bytes

from app.services.verification import verify_from_payload
from app.crypto.canonicalize import canonicalize

# ===================== PATHS =====================
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
POPPLER_PATH = r'C:\poppler\Library\bin'


# ===================== QR EXTRACTION =====================
def extract_qr_from_image(img_cv):
    if img_cv is None or img_cv.size == 0:
        return None

    detector = cv2.QRCodeDetector()
    h, w = img_cv.shape[:2]

    # Crop top-right (QR location) + full image fallback
    crops = [
        img_cv[0:int(h * 0.45), int(w * 0.55):w],
        img_cv[0:int(h * 0.40), int(w * 0.60):w],
        img_cv
    ]

    candidates = []
    for crop in crops:
        if crop is None or crop.size == 0:
            continue

        candidates.append(crop)

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        candidates.append(gray)

        # Upscale (important for dense QR)
        up = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        candidates.append(up)
        candidates.append(cv2.cvtColor(up, cv2.COLOR_BGR2GRAY))

        # Threshold
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(th)

        up_gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        _, th2 = cv2.threshold(up_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(th2)

    for img in candidates:
        try:
            data, _, _ = detector.detectAndDecode(img)
            if data and len(data) > 20:
                return data
        except Exception:
            continue

    return None


# ===================== OCR =====================
def extract_text_with_ocr(img_cv):
    try:
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray, config='--psm 4')
        return text
    except Exception as e:
        print("OCR Error:", e)
        return ""


# ===================== PARSE FIELDS =====================
def parse_printed_fields(ocr_text):
    printed = {}
    lines = [line.strip() for line in ocr_text.split('\n') if line.strip()]

    for line in lines:
        upper = line.upper()

        if "NAME OF THE CANDIDATE" in upper or (upper.startswith("NAME") and ":" in line):
            parts = re.split(r'[:\-]', line, maxsplit=1)
            if len(parts) > 1:
                name_val = parts[1].strip()
                name_val = re.split(r'\s{2,}|REGISTER|PROGRAMME', name_val)[0].strip()
                if len(name_val) > 3:
                    printed["Name"] = name_val

        if "REGISTER" in upper:
            nums = re.findall(r'\d{10,}', line)
            if nums:
                printed["RegisterNo"] = nums[0]

        if "PROGRAMME" in upper or "B.E" in upper:
            parts = re.split(r'[:\-]', line, maxsplit=1)
            if len(parts) > 1:
                printed["Programme"] = parts[1].strip()

        if "DATE OF BIRTH" in upper or "DOB" in upper:
            dob = re.search(r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})', line)
            if dob:
                printed["DateOfBirth"] = dob.group(1)

        if "SEMESTER" in upper and "GPA" not in upper:
            sem = re.search(r'\b([1-8])\b', line)
            if sem:
                printed["Semester"] = sem.group(1)

        if "ISSUE DATE" in upper:
            idate = re.search(r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})', line)
            if idate:
                printed["IssueDate"] = idate.group(1)

        if "SGPA" in upper or "GPA" in upper:
            nums = re.findall(r'\d+\.\d+', line)
            if nums:
                printed["SemesterGPA"] = nums[0]

        if "CGPA" in upper:
            nums = re.findall(r'\d+\.\d+', line)
            if nums:
                printed["CGPA"] = nums[0]

    # Course Grades
    grade_map = {
        'FE)': 'O', 'FE': 'O', 'F)': 'O', '[E)': 'O', 'E)': 'O',
        'OO': 'O', 'O0': 'O', '0O': 'O', 'O)': 'O', '0': 'O',
        'AT': 'A+', 'A+': 'A+', 'A': 'A',
        'B+': 'B+', 'B': 'B', 'C': 'C', 'U': 'U'
    }

    for line in lines:
        code_match = re.search(r'(U23[A-Z0-9]{5,10})', line, re.IGNORECASE)
        if not code_match:
            continue

        code = code_match.group(1).upper()
        tokens = re.findall(r'[A-Za-z0-9\[\]\|\+\)\(]+', line)
        if not tokens:
            continue

        raw = tokens[-1].upper()
        cleaned = re.sub(r'[^A-Z0-9\+]', '', raw)

        if cleaned in grade_map:
            grade = grade_map[cleaned]
        elif cleaned in ['O', 'A', 'A+', 'B', 'B+', 'C', 'U']:
            grade = cleaned
        else:
            found = re.findall(r'\b([OABCU][\+]?)\b', line.upper())
            if found:
                grade = found[-1]
            elif 'FE' in raw or 'OO' in raw or '[E' in raw:
                grade = 'O'
            elif 'AT' in raw:
                grade = 'A+'
            else:
                continue

        printed[f"Course_{code}_Grade"] = grade

    return printed


# ===================== MAIN =====================
def verify_document(file_bytes, filename):
    result = {
        "valid": False,
        "status": None,
        "error": None,
        "qr_fields": None,
        "printed_fields": None,
        "mismatches": []
    }

    try:
        # 1. Load document
        if filename.lower().endswith('.pdf'):
            pages = convert_from_bytes(
                file_bytes, dpi=300,
                poppler_path=POPPLER_PATH,
                first_page=1, last_page=1
            )
            if not pages:
                result["error"] = "PDF conversion returned no pages"
                return result
            img_cv = cv2.cvtColor(np.array(pages[0]), cv2.COLOR_RGB2BGR)
        else:
            nparr = np.frombuffer(file_bytes, np.uint8)
            if nparr.size == 0:
                result["error"] = "Uploaded file is empty"
                return result
            img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img_cv is None:
                result["error"] = "Could not decode image"
                return result

        # 2. Extract QR
        qr_payload = extract_qr_from_image(img_cv)
        if not qr_payload:
            cv2.imwrite("debug_last_document.png", img_cv)
            result["error"] = "No QR code found in the document"
            return result

        # 3. Verify signature
        qr_result = verify_from_payload(qr_payload)
        if not qr_result.get("valid"):
            result["status"] = "QR_FORGED"
            result["error"] = "QR itself is forged or signature is invalid"
            result["qr_fields"] = qr_result.get("authentic_fields")
            return result

        qr_fields = qr_result.get("authentic_fields", {})
        result["qr_fields"] = qr_fields

        # 4. OCR
        ocr_text = extract_text_with_ocr(img_cv)
        printed_fields = parse_printed_fields(ocr_text)
        result["printed_fields"] = printed_fields
        result["ocr_raw"] = ocr_text
        result["ocr_courses"] = {k: v for k, v in printed_fields.items() if k.startswith("Course_")}

        # 5. Compare
        def normalize_date(value):
            if not value:
                return ""
            value = str(value).strip()
            if re.match(r'\d{4}-\d{2}-\d{2}', value):
                return value
            m = re.match(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})', value)
            if m:
                d, mth, y = m.groups()
                return f"{y}-{int(mth):02d}-{int(d):02d}"
            return value

        def clean(text):
            if not text:
                return ""
            text = str(text).upper().strip()
            text = re.sub(r'[^A-Z0-9\.\-\s\+]', '', text)
            return re.sub(r'\s+', ' ', text).strip()

        mismatches = []

        for name, qr_value in qr_fields.items():
            if name.startswith("Course_"):
                continue
            if name not in printed_fields:
                continue

            qr_val = qr_value
            printed_val = printed_fields[name]

            if "DATE" in name.upper() or name == "IssueDate":
                if normalize_date(qr_val) == normalize_date(printed_val):
                    continue
            else:
                a, b = clean(qr_val), clean(printed_val)
                if a == b:
                    continue
                if a.isdigit() and b.isdigit() and (a in b or b in a):
                    continue
                if "COMPUTER SCIENCE" in a and "COMPUTER SCIENCE" in b:
                    continue

            mismatches.append({
                "field": name,
                "qr_value": str(qr_val),
                "printed_value": str(printed_val)
            })

        # Courses
        qr_courses = {}
        for k, v in qr_fields.items():
            if k.startswith("Course_") and k.endswith("_Grade"):
                code = k.replace("Course_", "").replace("_Grade", "")
                qr_courses[code] = v

        printed_courses = {}
        for k, v in printed_fields.items():
            if k.startswith("Course_") and k.endswith("_Grade"):
                code = k.replace("Course_", "").replace("_Grade", "")
                printed_courses[code] = v

        for code in set(qr_courses) | set(printed_courses):
            qr_grade = qr_courses.get(code)
            printed_grade = printed_courses.get(code)

            if qr_grade is None:
                mismatches.append({
                    "field": f"Course_{code}",
                    "qr_value": "Not present in QR",
                    "printed_value": f"Present with grade {printed_grade}"
                })
            elif printed_grade is None:
                mismatches.append({
                    "field": f"Course_{code}",
                    "qr_value": f"Present with grade {qr_grade}",
                    "printed_value": "Not found in document"
                })
            elif clean(qr_grade) != clean(printed_grade):
                mismatches.append({
                    "field": f"Course_{code}_Grade",
                    "qr_value": qr_grade,
                    "printed_value": printed_grade
                })

        result["mismatches"] = mismatches
        result["valid"] = True
        result["status"] = "TAMPERING_IN_MARKSHEET" if mismatches else "FULLY_VALID"
        return result

    except Exception as e:
        result["error"] = f"Processing failed: {str(e)}"
        return result