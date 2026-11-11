import csv
import io
import os
import time
from collections import defaultdict

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   session, jsonify, Response, current_app, send_from_directory, abort)

from app.auth import login_required
from app.db import get_db
from app.services import automation as au

bp = Blueprint('auto', __name__)

# ---------------------------------------------------------------- admin: bulk import
@bp.route('/admin/import', methods=['GET', 'POST'])
@login_required
def import_students():
    report = None
    issue_report = None
    if request.method == 'POST':
        db = get_db()
        inst = db.execute("SELECT id FROM institutions LIMIT 1").fetchone()
        if not inst:
            flash("Please setup institution first", "danger")
            return redirect(url_for('main.setup_institution'))
        f = request.files.get('datafile')
        if not f or not f.filename:
            flash("Choose a .csv or .xlsx file", "danger")
            return render_template('import_students.html', report=None, issue_report=None)
        try:
            rows = au.read_rows(f.read(), f.filename)
        except Exception as e:
            flash(f"Could not read file: {e}", "danger")
            return render_template('import_students.html', report=None, issue_report=None)

        dry = bool(request.form.get('dry_run'))
        report = au.import_rows(rows, inst["id"], dry_run=dry)
        report["dry_run"] = dry

        if not dry and request.form.get('auto_issue') and report["register_nos"]:
            ids = []
            for reg in report["register_nos"]:
                s = au.find_student(reg)
                if s:
                    ids.append(s["id"])
            issue_report = au.bulk_issue_pending(ids)
        if not dry:
            flash(f"Import finished: {report['students_created']} new, "
                  f"{report['students_updated']} updated, {report['grades_saved']} grades saved.",
                  "success")
    return render_template('import_students.html', report=report, issue_report=issue_report)


@bp.route('/admin/import/template.csv')
@login_required
def import_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(au.TEMPLATE_HEADER)
    w.writerows(au.TEMPLATE_SAMPLE)
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment; filename=certitrust_import_template.csv"})


# ---------------------------------------------------------------- admin: lookup by register no
@bp.route('/admin/lookup')
@login_required
def lookup():
    reg = request.args.get('reg_no', '').strip()
    summary = None
    if reg:
        s = au.find_student(reg)
        if s:
            summary = au.get_student_summary(s)
        else:
            flash(f"No student found with register number {au.clean_register_no(reg)}", "danger")
    return render_template('lookup_student.html', reg=reg, summary=summary)


@bp.route('/admin/auto-issue/<reg_no>', methods=['POST'])
@login_required
def auto_issue_route(reg_no):
    s = au.find_student(reg_no)
    if not s:
        flash("Student not found", "danger")
        return redirect(url_for('auto.lookup'))
    scope = request.form.get('scope', 'consolidated')
    try:
        sem = None if scope == 'consolidated' else int(scope)
        r = au.auto_issue(s["id"], semester=sem)
        flash(("Existing up-to-date marksheet reused" if r["reused"] else "New signed marksheet issued")
              + f" (certificate #{r['certificate_id']})", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for('auto.lookup', reg_no=s["register_no"]))


@bp.route('/admin/bulk-issue', methods=['POST'])
@login_required
def bulk_issue():
    rep = au.bulk_issue_pending()
    flash(f"Bulk issue: {len(rep['issued'])} issued, {len(rep['up_to_date'])} already up to date, "
          f"{len(rep['failed'])} failed.", "success" if not rep['failed'] else "danger")
    return redirect(url_for('main.admin_dashboard'))


@bp.route('/admin/certificate/<int:cert_id>/pdf')
@login_required
def admin_cert_pdf(cert_id):
    return send_from_directory(os.path.join(current_app.instance_path, "certificates"),
                               f"certificate_{cert_id}.pdf", as_attachment=True)


# ---------------------------------------------------------------- admin JSON API
@bp.route('/api/student/<reg_no>')
@login_required
def api_student(reg_no):
    s = au.find_student(reg_no)
    if not s:
        return jsonify({"error": "not found"}), 404
    summary = au.get_student_summary(s)
    summary["student"].pop("id", None)
    return jsonify(summary)


# ---------------------------------------------------------------- public student self-service
_FAILS = defaultdict(list)        # ip -> [timestamps]; in-memory is fine for a prototype
MAX_FAILS, WINDOW = 5, 600


def _throttled(ip):
    now = time.time()
    _FAILS[ip] = [t for t in _FAILS[ip] if now - t < WINDOW]
    return len(_FAILS[ip]) >= MAX_FAILS


@bp.route('/student', methods=['GET', 'POST'])
def student_portal():
    """Student enters register number + date of birth; marksheets are generated automatically."""
    if request.method == 'POST' and request.form.get('action') == 'login':
        ip = request.remote_addr or "?"
        if _throttled(ip):
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
            return render_template('student_portal.html', summary=None)
        s = au.find_student(request.form.get('reg_no', ''))
        try:
            dob = au.normalise_dob(request.form.get('dob', ''))
        except ValueError:
            dob = ""
        # same message for "no such student" and "wrong DOB" - do not leak which register numbers exist
        if not s or not dob or not s["date_of_birth"] or s["date_of_birth"] != dob:
            _FAILS[ip].append(time.time())
            flash("Register number or date of birth is incorrect.", "danger")
            return render_template('student_portal.html', summary=None)
        session['student_id'] = s["id"]
        return redirect(url_for('auto.student_portal'))

    sid = session.get('student_id')
    if not sid:
        return render_template('student_portal.html', summary=None)
    s = get_db().execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not s:
        session.pop('student_id', None)
        return redirect(url_for('auto.student_portal'))

    if request.method == 'POST' and request.form.get('action') == 'generate':
        scope = request.form.get('scope', 'consolidated')
        try:
            sem = None if scope == 'consolidated' else int(scope)
            r = au.auto_issue(s["id"], semester=sem)
            return redirect(url_for('auto.student_download', cert_id=r["certificate_id"]))
        except Exception as e:
            flash(str(e), "danger")
    return render_template('student_portal.html', summary=au.get_student_summary(s))


@bp.route('/student/download/<int:cert_id>')
def student_download(cert_id):
    sid = session.get('student_id')
    if not sid:
        abort(403)
    row = get_db().execute("SELECT student_id FROM certificates WHERE id=?", (cert_id,)).fetchone()
    if not row or row["student_id"] != sid:          # a student can only download their own marksheet
        abort(403)
    return send_from_directory(os.path.join(current_app.instance_path, "certificates"),
                               f"certificate_{cert_id}.pdf", as_attachment=True)


@bp.route('/student/logout')
def student_logout():
    session.pop('student_id', None)
    return redirect(url_for('main.index'))
