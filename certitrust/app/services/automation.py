"""
Register-number driven student data automation for CertiTrust.

What this module automates
--------------------------
1. parse_register_no()   - derive batch year / graduation year / programme from the register number
2. import_rows()         - bulk upsert students + courses + grades from CSV / Excel, keyed by register number
3. get_student_summary() - one call returns student, grades by semester, CGPA and issued certificates
4. auto_issue()          - idempotent issuance: re-uses an up-to-date certificate, supersedes stale ones
5. bulk_issue_pending()  - issue marksheets for every student whose data changed or has no certificate yet
"""
import csv
import hashlib
import io
import re
from datetime import datetime

from app.db import get_db

GRADE_POINTS = {
    'O': 10.0, 'A+': 9.0, 'A': 8.0, 'B+': 7.0,
    'B': 6.0, 'C': 5.0, 'U': 0.0, 'SA': 0.0, 'W': 0.0,
}

# Edit this to match your college's register-number scheme.
# For 14-digit numbers such as 61782323102081:  6178 | 23 | 23 | 102 | 081
#   college code | admission year | (regulation/entry digits) | dept code | roll
DEPT_CODES = {
    "102": "B.E. Computer Science and Engineering",
}
PROGRAMME_DURATION_YEARS = 4

# Accepted spreadsheet header spellings -> canonical column name
COLUMN_ALIASES = {
    "register_no": {"register_no", "register_number", "registration_number", "registration_no",
                    "reg_no", "regno", "reg_number", "roll_no"},
    "name": {"name", "student_name", "full_name", "name_of_the_candidate"},
    "programme": {"programme", "program", "department", "branch", "course_name_programme"},
    "regulation": {"regulation", "regulations"},
    "batch_year": {"batch_year", "batch", "admission_year", "year_of_admission"},
    "date_of_birth": {"date_of_birth", "dob", "birth_date"},
    "gender": {"gender", "sex"},
    "folio_no": {"folio_no", "folio", "folio_number"},
    "semester": {"semester", "sem"},
    "course_code": {"course_code", "subject_code", "code"},
    "course_name": {"course_name", "course_title", "subject_name", "title"},
    "credits": {"credits", "credit"},
    "grade": {"grade", "grades"},
}
_ALIAS_LOOKUP = {a: canon for canon, names in COLUMN_ALIASES.items() for a in names}

TEMPLATE_HEADER = ["register_no", "name", "programme", "regulation", "batch_year",
                   "date_of_birth", "gender", "semester", "course_code", "course_name",
                   "credits", "grade"]
TEMPLATE_SAMPLE = [
    ["61782323102081", "LOKESHWARAN R", "", "2023", "", "2005-03-14", "Male", "1", "CS3101", "Programming in C", "3", "A+"],
    ["61782323102081", "LOKESHWARAN R", "", "2023", "", "2005-03-14", "Male", "1", "MA3101", "Engineering Mathematics I", "4", "O"],
]


# ------------------------------------------------------------------ register number
def clean_register_no(value) -> str:
    """Excel often turns 61782323102081 into 6.1782323102081E+13 or '61782323102081.0'."""
    s = str(value if value is not None else "").strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        return s.split(".")[0]
    if re.fullmatch(r"\d(\.\d+)?[eE]\+?\d+", s):
        return str(int(float(s)))
    return re.sub(r"\s+", "", s).upper()


def parse_register_no(reg_no: str) -> dict:
    """Derive what we can from the register number. Missing pieces are None."""
    reg_no = clean_register_no(reg_no)
    info = {"register_no": reg_no, "college_code": None, "batch_year": None,
            "graduation_year": None, "programme": None}
    if not reg_no.isdigit() or len(reg_no) < 6:
        return info
    info["college_code"] = reg_no[:4]
    yy = int(reg_no[4:6])
    info["batch_year"] = 2000 + yy
    info["graduation_year"] = 2000 + yy + PROGRAMME_DURATION_YEARS
    if len(reg_no) == 14:
        info["programme"] = DEPT_CODES.get(reg_no[8:11])
    return info


def normalise_dob(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{s}' (use YYYY-MM-DD or DD-MM-YYYY)")


# ------------------------------------------------------------------ file reading
def _canon_header(h) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")
    return _ALIAS_LOOKUP.get(key, key)


def read_rows(file_bytes: bytes, filename: str):
    """Return list of dicts with canonical column names, from .csv or .xlsx."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        try:
            header = [_canon_header(h) for h in next(it)]
        except StopIteration:
            return []
        rows = []
        for r in it:
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue
            rows.append({header[i]: ("" if c is None else c) for i, c in enumerate(r) if i < len(header)})
        return rows
    if name.endswith((".csv", ".txt")):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for r in reader:
            row = {_canon_header(k): (v or "").strip() for k, v in r.items() if k}
            if any(row.values()):
                rows.append(row)
        return rows
    raise ValueError("Unsupported file type. Upload a .csv or .xlsx file.")


# ------------------------------------------------------------------ import
def import_rows(rows, institution_id: int, dry_run: bool = False) -> dict:
    """
    Upsert students (key = institution + register_no), courses (key = institution + course_code)
    and grades (key = student + semester + course). Bad rows are reported, good rows are kept.
    """
    db = get_db()
    report = {"rows": len(rows), "students_created": 0, "students_updated": 0,
              "grades_saved": 0, "courses_created": 0, "errors": [], "register_nos": []}
    seen_students = set()

    for i, row in enumerate(rows, start=2):          # row 1 is the header
        try:
            reg = clean_register_no(row.get("register_no"))
            if not reg:
                raise ValueError("register_no is empty")
            derived = parse_register_no(reg)

            student = db.execute(
                "SELECT * FROM students WHERE institution_id=? AND register_no=?",
                (institution_id, reg)).fetchone()

            name = str(row.get("name") or "").strip()
            programme = str(row.get("programme") or "").strip() or derived["programme"] or ""
            regulation = str(row.get("regulation") or "").strip()
            batch_raw = str(row.get("batch_year") or "").strip()
            batch_year = int(float(batch_raw)) if batch_raw else derived["batch_year"]
            dob = normalise_dob(row.get("date_of_birth"))
            gender = str(row.get("gender") or "").strip()
            folio = str(row.get("folio_no") or "").strip()

            if student is None:
                if not name:
                    raise ValueError("new student needs a name")
                if not programme:
                    raise ValueError("programme missing and could not be derived from register number")
                cur = db.execute("""
                    INSERT INTO students (institution_id, register_no, name, programme, regulation,
                                          batch_year, date_of_birth, gender, folio_no)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (institution_id, reg, name, programme, regulation or str(batch_year or ""),
                     batch_year, dob, gender, folio))
                student_id = cur.lastrowid
                report["students_created"] += 1
            else:
                student_id = student["id"]
                # only overwrite with non-empty values
                db.execute("""
                    UPDATE students SET
                        name = COALESCE(NULLIF(?, ''), name),
                        programme = COALESCE(NULLIF(?, ''), programme),
                        regulation = COALESCE(NULLIF(?, ''), regulation),
                        batch_year = COALESCE(?, batch_year),
                        date_of_birth = COALESCE(NULLIF(?, ''), date_of_birth),
                        gender = COALESCE(NULLIF(?, ''), gender),
                        folio_no = COALESCE(NULLIF(?, ''), folio_no)
                    WHERE id = ?""",
                    (name, programme, regulation, batch_year, dob, gender, folio, student_id))
                if reg not in seen_students:
                    report["students_updated"] += 1
            seen_students.add(reg)
            if reg not in report["register_nos"]:
                report["register_nos"].append(reg)

            # ---- optional grade part of the row
            code = str(row.get("course_code") or "").strip().upper()
            grade = str(row.get("grade") or "").strip().upper()
            if code or grade:
                if not (code and grade):
                    raise ValueError("course_code and grade must both be present")
                if grade not in GRADE_POINTS:
                    raise ValueError(f"invalid grade '{grade}' (allowed: {', '.join(GRADE_POINTS)})")
                sem_raw = str(row.get("semester") or "").strip()
                if not sem_raw:
                    raise ValueError("semester missing")
                semester = int(float(sem_raw))
                if not 1 <= semester <= 8:
                    raise ValueError("semester must be 1-8")

                course = db.execute(
                    "SELECT id FROM courses WHERE institution_id=? AND course_code=?",
                    (institution_id, code)).fetchone()
                if course:
                    course_id = course["id"]
                else:
                    cname = str(row.get("course_name") or "").strip()
                    credits_raw = str(row.get("credits") or "").strip()
                    if not cname or not credits_raw:
                        raise ValueError(f"new course {code} needs course_name and credits")
                    credits = float(credits_raw)
                    if credits <= 0:
                        raise ValueError("credits must be positive")
                    cur = db.execute(
                        "INSERT INTO courses (institution_id, course_code, course_name, credits) VALUES (?,?,?,?)",
                        (institution_id, code, cname, credits))
                    course_id = cur.lastrowid
                    report["courses_created"] += 1

                db.execute("""
                    INSERT INTO student_grades (student_id, semester, course_id, grade, grade_point)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(student_id, semester, course_id)
                    DO UPDATE SET grade=excluded.grade, grade_point=excluded.grade_point""",
                    (student_id, semester, course_id, grade, GRADE_POINTS[grade]))
                report["grades_saved"] += 1
        except Exception as e:          # keep going; report the row
            report["errors"].append({"row": i, "register_no": str(row.get("register_no", "")), "error": str(e)})

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report


# ------------------------------------------------------------------ lookup
def find_student(reg_no: str):
    db = get_db()
    return db.execute("SELECT * FROM students WHERE register_no = ?",
                      (clean_register_no(reg_no),)).fetchone()


def _grades_for(student_id: int, semester=None):
    db = get_db()
    sql = """SELECT sg.semester, sg.grade, sg.grade_point, c.course_code, c.course_name, c.credits
             FROM student_grades sg JOIN courses c ON sg.course_id = c.id
             WHERE sg.student_id = ?"""
    args = [student_id]
    if semester is not None:
        sql += " AND sg.semester = ?"
        args.append(semester)
    sql += " ORDER BY sg.semester, c.course_code"
    return db.execute(sql, args).fetchall()


def compute_gpa(grades) -> float:
    credits = sum(g["credits"] for g in grades)
    points = sum((g["grade_point"] or 0) * g["credits"] for g in grades)
    return round(points / credits, 2) if credits else 0.0


def fingerprint(grades) -> str:
    """Hash of the grade data a certificate would contain - changes iff the data changes."""
    rows = sorted((g["semester"], g["course_code"], g["grade"], g["credits"]) for g in grades)
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def get_student_summary(student) -> dict:
    db = get_db()
    grades = _grades_for(student["id"])
    by_sem = {}
    for g in grades:
        by_sem.setdefault(g["semester"], []).append(dict(g))
    semesters = [{"semester": s, "courses": c, "sgpa": compute_gpa(c)} for s, c in sorted(by_sem.items())]
    certs = db.execute("""SELECT id, certificate_type, issue_date, cgpa, status, data_fingerprint
                          FROM certificates WHERE student_id=? ORDER BY id DESC""",
                       (student["id"],)).fetchall()
    return {
        "student": dict(student),
        "derived": parse_register_no(student["register_no"]),
        "semesters": semesters,
        "cgpa": compute_gpa(grades) if grades else None,
        "certificates": [dict(c) for c in certs],
    }


# ------------------------------------------------------------------ issuance
def auto_issue(student_id: int, semester=None) -> dict:
    """
    Idempotent issuance.
      - If an 'issued' certificate with identical grade data exists -> reuse it (no new signature).
      - Otherwise issue a new one and mark older ones for the same scope 'superseded'.
    """
    from app.services.issuance import issue_certificate   # lazy: needs crypto libs
    db = get_db()
    cert_type = "semester" if semester is not None else "consolidated"
    grades = _grades_for(student_id, semester)
    if not grades:
        raise ValueError("No grades found for this student" +
                         (f" in semester {semester}" if semester else ""))
    fp = fingerprint(grades)

    existing = db.execute("""
        SELECT id FROM certificates
        WHERE student_id=? AND certificate_type=? AND status='issued' AND data_fingerprint=?
          AND COALESCE(json_extract(qr_payload,'$.semester'), -1) = ?
        ORDER BY id DESC LIMIT 1""",
        (student_id, cert_type, fp, semester if semester is not None else -1)).fetchone()
    if existing:
        return {"certificate_id": existing["id"], "reused": True,
                "pdf_filename": f"certificate_{existing['id']}.pdf"}

    result = issue_certificate(student_id, certificate_type=cert_type, semester=semester)
    new_id = result["certificate_id"]
    db.execute("""UPDATE certificates SET status='superseded'
                  WHERE student_id=? AND certificate_type=? AND id<>? AND status='issued'
                    AND COALESCE(json_extract(qr_payload,'$.semester'), -1) = ?""",
               (student_id, cert_type, new_id, semester if semester is not None else -1))
    db.execute("UPDATE certificates SET data_fingerprint=? WHERE id=?", (fp, new_id))
    db.commit()
    result["reused"] = False
    return result


def bulk_issue_pending(student_ids=None) -> dict:
    """Issue a consolidated marksheet for every student that has grades and no up-to-date certificate."""
    db = get_db()
    if student_ids is None:
        student_ids = [r["id"] for r in db.execute(
            "SELECT DISTINCT student_id AS id FROM student_grades").fetchall()]
    out = {"issued": [], "up_to_date": [], "failed": []}
    for sid in student_ids:
        s = db.execute("SELECT register_no FROM students WHERE id=?", (sid,)).fetchone()
        reg = s["register_no"] if s else str(sid)
        try:
            r = auto_issue(sid)
            (out["up_to_date"] if r["reused"] else out["issued"]).append(
                {"register_no": reg, "certificate_id": r["certificate_id"]})
        except Exception as e:
            out["failed"].append({"register_no": reg, "error": str(e)})
    return out


# ------------------------------------------------------------------ schema upgrade
def ensure_schema(db):
    """Non-destructive migration so existing certitrust.db files keep working."""
    def cols(table):
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    try:
        s = cols("students")
    except Exception:
        return
    if not s:
        return
    for col in ("date_of_birth", "gender", "folio_no"):
        if col not in s:
            db.execute(f"ALTER TABLE students ADD COLUMN {col} TEXT")
    if "data_fingerprint" not in cols("certificates"):
        db.execute("ALTER TABLE certificates ADD COLUMN data_fingerprint TEXT")
    db.commit()
