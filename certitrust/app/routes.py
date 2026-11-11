from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash)
from app.db import get_db
from app.auth import login_required, ADMIN_USERNAME, ADMIN_PASSWORD
from app.services.issuance import issue_certificate
from app.services.verification import verify_from_payload
from app.crypto.keys import generate_rsa_keypair, save_private_key
from app.services.document_verify import verify_document

import cv2
import numpy as np
from PIL import Image
import io

from flask import send_from_directory
import os
from flask import current_app

bp = Blueprint('main', __name__)

GRADE_POINTS = {
    'O': 10.0, 'A+': 9.0, 'A': 8.0, 'B+': 7.0,
    'B': 6.0, 'C': 5.0, 'U': 0.0, 'SA': 0.0, 'W': 0.0, '-': 0.0
}

# ===================== PUBLIC =====================
@bp.route('/')
def index():
    return render_template('index.html')

# ===================== VERIFY =====================
@bp.route('/verify', methods=['GET', 'POST'])
def verify():
    result = None

    if request.method == 'POST':
        # ---------- Full Document Upload (PDF / Image) ----------
        if 'document' in request.files and request.files['document'].filename:
            file = request.files['document']
            file_bytes = file.read()
            result = verify_document(file_bytes, file.filename)

        # ---------- Old QR Image ----------
        elif 'qr_image' in request.files and request.files['qr_image'].filename:
            file = request.files['qr_image']
            try:
                import cv2
                import numpy as np
                img_bytes = file.read()
                nparr = np.frombuffer(img_bytes, np.uint8)
                img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                detector = cv2.QRCodeDetector()
                data, _, _ = detector.detectAndDecode(img_cv)
                if not data:
                    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                    data, _, _ = detector.detectAndDecode(gray)

                if data:
                    result = verify_from_payload(data)
                else:
                    result = {"valid": False, "error": "No QR code found in the image."}
            except Exception as e:
                result = {"valid": False, "error": f"Image processing failed: {str(e)}"}

        # ---------- Paste Payload ----------
        else:
            qr_payload = request.form.get('qr_payload', '').strip()
            if qr_payload:
                printed = {}
                for key in request.form:
                    if key.startswith("printed_"):
                        printed[key.replace("printed_", "")] = request.form[key]
                result = verify_from_payload(qr_payload, printed if printed else None)
            else:
                result = {"valid": False, "error": "Please upload a document or QR image, or paste the payload."}

    return render_template('verify.html', result=result)

# ===================== ADMIN AUTH =====================
@bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if (request.form.get('username') == ADMIN_USERNAME and
            request.form.get('password') == ADMIN_PASSWORD):
            session['admin_logged_in'] = True
            return redirect(url_for('main.admin_dashboard'))
        flash("Invalid credentials", "danger")
    return render_template('admin_login.html')

@bp.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('main.index'))

# ===================== ADMIN DASHBOARD =====================
@bp.route('/admin')
@login_required
def admin_dashboard():
    db = get_db()

    students_raw = db.execute("""
        SELECT s.*, 
               (SELECT COUNT(*) FROM certificates c WHERE c.student_id = s.id) as cert_count
        FROM students s
        ORDER BY s.id DESC
    """).fetchall()

    students = []
    for s in students_raw:
        sems = db.execute("""
            SELECT DISTINCT semester FROM student_grades WHERE student_id = ?
        """, (s["id"],)).fetchall()
        sem_set = {row["semester"] for row in sems}
        students.append({
            **dict(s),
            "semesters": {i: (i in sem_set) for i in range(1,9)},
            "cert_count": s["cert_count"]
        })

    total_students = len(students)
    issued_count = db.execute("SELECT COUNT(*) as c FROM certificates").fetchone()["c"]
    pending_updates = sum(1 for s in students if not all(s["semesters"].values()))
    month_issued = issued_count  # simplified

    return render_template('admin_dashboard.html',
                           students=students,
                           total_students=total_students,
                           issued_count=issued_count,
                           pending_updates=pending_updates,
                           month_issued=month_issued)

# ===================== SETUP INSTITUTION =====================
@bp.route('/admin/setup', methods=['GET', 'POST'])
@login_required
def setup_institution():
    if request.method == 'POST':
        name = request.form['name']
        code = request.form['code'].upper()
        priv, pub = generate_rsa_keypair(2048)
        save_private_key(priv)
        db = get_db()
        db.execute("DELETE FROM institutions")
        db.execute(
            "INSERT INTO institutions (name, code, public_key_pem) VALUES (?, ?, ?)",
            (name, code, pub)
        )
        db.commit()
        flash("Institution setup complete. Private key saved securely.", "success")
        return redirect(url_for('main.admin_dashboard'))
    return render_template('setup_institution.html')

# ===================== ADD STUDENT =====================
@bp.route('/admin/student/add', methods=['GET', 'POST'])
@login_required
def add_student():
    if request.method == 'POST':
        db = get_db()
        inst = db.execute("SELECT id FROM institutions LIMIT 1").fetchone()
        if not inst:
            flash("Please setup institution first", "danger")
            return redirect(url_for('main.setup_institution'))

        from app.services.automation import clean_register_no, parse_register_no
        reg = clean_register_no(request.form['register_no'])
        if db.execute("SELECT 1 FROM students WHERE register_no = ?", (reg,)).fetchone():
            flash(f"Register number {reg} already exists", "danger")
            return redirect(url_for('main.add_student'))
        derived = parse_register_no(reg)
        db.execute("""
            INSERT INTO students 
            (institution_id, register_no, name, programme, regulation, batch_year, date_of_birth, gender, folio_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            inst["id"],
            reg,
            request.form['name'].strip(),
            request.form['programme'].strip(),
            request.form.get('regulation', '').strip(),
            request.form.get('batch_year') or derived['batch_year'],
            request.form.get('date_of_birth', '').strip(),
            request.form.get('gender', '').strip(),
            request.form.get('folio_no', '').strip()
        ))
        db.commit()
        flash("Student added successfully", "success")
        return redirect(url_for('main.admin_dashboard'))
    return render_template('add_student.html')

# ===================== ADD SEMESTER GRADES =====================
@bp.route('/admin/student/<int:student_id>/grades', methods=['GET', 'POST'])
@login_required
def add_grades(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for('main.admin_dashboard'))

    if request.method == 'POST':
        semester = int(request.form['semester'])
        course_codes = request.form.getlist('course_code[]')
        course_names = request.form.getlist('course_name[]')
        credits_list = request.form.getlist('credits[]')
        grades_list = request.form.getlist('grade[]')

        inst_id = student["institution_id"]

        for code, name, credits, grade in zip(course_codes, course_names, credits_list, grades_list):
            code = code.strip().upper()
            if not code:
                continue
            # Insert or get course
            course = db.execute(
                "SELECT id FROM courses WHERE institution_id = ? AND course_code = ?",
                (inst_id, code)
            ).fetchone()
            if course:
                course_id = course["id"]
            else:
                db.execute(
                    "INSERT INTO courses (institution_id, course_code, course_name, credits) VALUES (?, ?, ?, ?)",
                    (inst_id, code, name.strip(), float(credits))
                )
                course_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

            gp = GRADE_POINTS.get(grade.strip().upper(), 0.0)
            db.execute("""
                INSERT OR REPLACE INTO student_grades 
                (student_id, semester, course_id, grade, grade_point)
                VALUES (?, ?, ?, ?, ?)
            """, (student_id, semester, course_id, grade.strip().upper(), gp))

        db.commit()
        flash(f"Semester {semester} grades saved", "success")
        return redirect(url_for('main.admin_dashboard'))

    return render_template('add_grades.html', student=student)

# ===================== ISSUE CERTIFICATE =====================
@bp.route('/admin/issue/<int:student_id>', methods=['GET', 'POST'])
@login_required
def admin_issue(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for('main.admin_dashboard'))

    # Get available semesters
    semesters = db.execute("""
        SELECT DISTINCT semester FROM student_grades 
        WHERE student_id = ? ORDER BY semester
    """, (student_id,)).fetchall()
    semesters = [s["semester"] for s in semesters]

    if request.method == 'POST':
        cert_type = request.form.get('cert_type')
        semester = request.form.get('semester')
        semester = int(semester) if semester else None

        try:
            result = issue_certificate(student_id, certificate_type=cert_type, semester=semester)
            return render_template('issue_result.html', result=result)
        except Exception as e:
            flash(str(e), "danger")
            return redirect(url_for('main.admin_dashboard'))

    return render_template('select_issue.html', student=student, semesters=semesters)

@bp.route('/admin/students')
@login_required
def students_list():
    return redirect(url_for('main.admin_dashboard'))

@bp.route('/admin/marks')
@login_required
def marks_update():
    flash("Select a student from Dashboard and click Marks", "success")
    return redirect(url_for('main.admin_dashboard'))

@bp.route('/admin/documents')
@login_required
def document_issue():
    flash("Select a student from Dashboard and click Issue", "success")
    return redirect(url_for('main.admin_dashboard'))

@bp.route('/admin/download/<filename>')
@login_required
def download_certificate(filename):
    cert_dir = os.path.join(current_app.instance_path, "certificates")
    return send_from_directory(cert_dir, filename, as_attachment=True)