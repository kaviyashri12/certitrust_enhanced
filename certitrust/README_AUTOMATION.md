# CertiTrust – Register-Number Automation (enhancement)

## New files
- `app/services/automation.py`  – register-no parsing, CSV/Excel import, summary, idempotent issue, bulk issue, DB upgrade
- `app/automation_routes.py`    – new routes (blueprint `auto`)
- `app/templates/import_students.html`, `lookup_student.html`, `student_portal.html`
- `sample_data/students_sample.csv` (sample DOBs are made-up placeholders), `tests/test_automation.py`

## Changed files
`app/__init__.py` (registers blueprint, auto-migrates old DB), `app/routes.py` (duplicate register-no check, auto batch year),
`app/schema.sql` (DOB/gender/folio/fingerprint columns), `base_admin.html`, `admin_dashboard.html`, `index.html` (links)

## Run
    pip install -r requirements.txt
    python run.py
Existing `instance/certitrust.db` and `private_key.pem` keep working; missing columns are added on first start.

## Workflow
1. Admin -> Bulk Import -> upload CSV/XLSX (tick *Preview only* first) -> fix reported rows -> import
2. Admin -> Lookup by Reg No -> fetch student, grades, CGPA -> issue semester / consolidated marksheet
3. Admin -> Issue pending marksheets (bulk)
4. Student -> /student -> register number + DOB -> download own signed marksheet

## Configure
`DEPT_CODES` and `PROGRAMME_DURATION_YEARS` at the top of `automation.py` control what is derived from a register number.

## Tests
    python -m unittest tests.test_automation -v
