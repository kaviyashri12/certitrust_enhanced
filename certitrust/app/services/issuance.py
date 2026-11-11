import json
import base64
from datetime import date
from io import BytesIO
import zlib
from PIL import Image
import qrcode
from app.db import get_db
from app.crypto.canonicalize import canonicalize
from app.crypto.merkle import sha256_hex, build_merkle_tree
from app.crypto.keys import sign_data, load_private_key

from app.services.certificate_pdf import generate_marksheet_pdf
import os
from flask import current_app

def get_ordered_fields(student, grades, cgpa, issue_date, semester=None, semester_gpa=None):
    """
    Build ordered fields.
    If semester is given → Semester Marksheet (only that semester)
    If semester is None → Consolidated (all grades)
    """
    fields = [
        ("Name", student["name"]),
        ("RegisterNo", student["register_no"]),
        ("Programme", student["programme"]),
        ("IssueDate", issue_date),
    ]

    if semester is not None:
        fields.append(("Semester", semester))
        if semester_gpa is not None:
            fields.append(("SemesterGPA", semester_gpa))
    else:
        fields.append(("CGPA", cgpa))

    # Add only the relevant grades
    for g in sorted(grades, key=lambda x: (x["semester"], x["course_code"])):
        field_name = f"Course_{g['course_code']}_Grade"
        fields.append((field_name, g["grade"]))

    return fields

def issue_certificate(student_id: int, certificate_type: str = "degree", semester: int = None):
    db = get_db()

    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        raise ValueError("Student not found")

    try:
        private_pem = load_private_key()
    except FileNotFoundError:
        raise ValueError("Private key not found. Please setup institution first.")

    # Load grades
    if semester is not None:
        # Only selected semester
        grades = db.execute("""
            SELECT sg.semester, sg.grade, sg.grade_point, c.course_code, c.course_name, c.credits
            FROM student_grades sg
            JOIN courses c ON sg.course_id = c.id
            WHERE sg.student_id = ? AND sg.semester = ?
            ORDER BY c.course_code
        """, (student_id, semester)).fetchall()
    else:
        # All semesters
        grades = db.execute("""
            SELECT sg.semester, sg.grade, sg.grade_point, c.course_code, c.course_name, c.credits
            FROM student_grades sg
            JOIN courses c ON sg.course_id = c.id
            WHERE sg.student_id = ?
            ORDER BY sg.semester, c.course_code
        """, (student_id,)).fetchall()

    if not grades:
        raise ValueError("No grades found for this student/semester")

    # Calculate GPA / CGPA
    total_points = sum((g["grade_point"] or 0) * g["credits"] for g in grades)
    total_credits = sum(g["credits"] for g in grades)
    calculated_gpa = round(total_points / total_credits, 2) if total_credits > 0 else 0.0

    issue_date = date.today()

    if semester is not None:
        ordered_fields = get_ordered_fields(
            student, grades, None, issue_date,
            semester=semester, semester_gpa=calculated_gpa
        )
        final_cgpa = None
    else:
        ordered_fields = get_ordered_fields(
            student, grades, calculated_gpa, issue_date
        )
        final_cgpa = calculated_gpa

    # --- Rest of the function remains same (canonicalize, merkle, sign, save) ---
    leaf_hashes = []
    field_records = []
    for pos, (name, value) in enumerate(ordered_fields):
        canon = canonicalize(name, value)
        leaf = sha256_hex(canon)
        leaf_hashes.append(leaf)
        field_records.append({
            "field_name": name,
            "field_value": str(value),
            "leaf_hash": leaf,
            "position": pos
        })

    root, _ = build_merkle_tree(leaf_hashes)
    signature = sign_data(private_pem, root)

    # Build full payload (keep all fields)
    payload = {
        "inst": "SONA",
        "type": certificate_type,
        "semester": semester,
        "fields": {name: str(value) for name, value in ordered_fields},
        "sig": signature
    }

    # Compress the payload to reduce QR size
    json_str = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    compressed = zlib.compress(json_str.encode('utf-8'), level=9)
    qr_payload = "CT1:" + base64.b64encode(compressed).decode('ascii')   # CT1 = CertiTrust v1 compressed

    # Save original uncompressed version in DB for reference
    cur = db.execute("""
        INSERT INTO certificates
        (student_id, certificate_type, issue_date, cgpa, merkle_root, signature, qr_payload, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'issued')
    """, (student_id, certificate_type, issue_date.isoformat(), final_cgpa, root, signature, json_str))
    cert_id = cur.lastrowid

    for fr in field_records:
        db.execute("""
            INSERT INTO certificate_fields
            (certificate_id, field_name, field_value, leaf_hash, position)
            VALUES (?, ?, ?, ?, ?)
        """, (cert_id, fr["field_name"], fr["field_value"], fr["leaf_hash"], fr["position"]))

    db.commit()

    # Generate QR with better settings for compressed data
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # Medium is better for scanning
        box_size=12,
        border=4
    )
    qr.add_data(qr_payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    qr_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # Generate PDF
    pdf_buffer = generate_marksheet_pdf(
        student=student,
        grades=grades,
        gpa=calculated_gpa,
        qr_b64=qr_b64,
        certificate_id=cert_id,
        semester=semester
    )

    # Save PDF
    cert_dir = os.path.join(current_app.instance_path, "certificates")
    os.makedirs(cert_dir, exist_ok=True)
    pdf_path = os.path.join(cert_dir, f"certificate_{cert_id}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_buffer.getvalue())

    # Generate PNG version of the marksheet
    marksheet_image_b64 = None
    try:
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(
            pdf_buffer.getvalue(),
            dpi=150,
            poppler_path=r'C:\poppler\Library\bin'
        )

        if pages:
            img_buffer = BytesIO()
            pages[0].save(img_buffer, format="PNG")
            marksheet_image_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
    except Exception as e:
        print("PNG generation error:", e)
        marksheet_image_b64 = None

    return {
        "certificate_id": cert_id,
        "merkle_root": root,
        "signature": signature,
        "cgpa": final_cgpa,
        "semester_gpa": calculated_gpa if semester else None,
        "qr_payload": qr_payload,
        "qr_image_b64": qr_b64,
        "fields": field_records,
        "pdf_filename": f"certificate_{cert_id}.pdf",
        "marksheet_image_b64": marksheet_image_b64
    }