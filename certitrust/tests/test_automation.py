"""
Run from project root:   python -m unittest tests.test_automation -v
Issuance is stubbed so the data-automation logic can be tested without the signing stack;
the real crypto path is covered by test_crypto.py and manual issue from the UI.
"""
import io, json, os, sys, tempfile, types, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in ("Crypto", "Crypto.PublicKey", "Crypto.Signature", "Crypto.Hash", "pdf2image", "qrcode"):
    try:
        __import__(mod)
    except ImportError:
        m = types.ModuleType(mod)
        m.RSA = m.pkcs1_15 = m.SHA256 = m.convert_from_bytes = object()
        sys.modules[mod] = m
if "Crypto.PublicKey" in sys.modules and not hasattr(sys.modules["Crypto.PublicKey"], "RSA"):
    sys.modules["Crypto.PublicKey"].RSA = object()

from app import create_app
from app.db import get_db, init_db
from app.services import automation as au

CSV = (
    "register_no,name,programme,regulation,batch_year,date_of_birth,gender,semester,course_code,course_name,credits,grade\n"
    "61782323102081,LOKESHWARAN R,,2023,,14-03-2005,Male,1,MA3101,Maths I,4,O\n"
    "61782323102081,LOKESHWARAN R,,2023,,14-03-2005,Male,1,CS3101,C Programming,3,A\n"
    "61782323102079,LITHWIN SHUJI J,,2023,,2005-06-02,Male,1,MA3101,,,B\n"
    "61782323102069,KAVIYASHRI V,,2023,,2005-11-21,Female,1,CS3101,,,Z\n"      # invalid grade
    "6.1782323102099E+13,NEW STUDENT,,2023,,2005-01-01,Male,,,,,\n"            # excel-mangled reg no
)


def fake_issue(student_id, certificate_type="degree", semester=None):
    db = get_db()
    payload = json.dumps({"semester": semester})
    cur = db.execute("INSERT INTO certificates (student_id,certificate_type,issue_date,qr_payload,status) "
                     "VALUES (?,?,date('now'),?,'issued')", (student_id, certificate_type, payload))
    db.commit()
    return {"certificate_id": cur.lastrowid}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app()
        self.app.config.update(DATABASE=os.path.join(self.tmp, "t.db"), TESTING=True)
        self.ctx = self.app.app_context(); self.ctx.push()
        init_db()
        db = get_db()
        db.execute("INSERT INTO institutions (name, code, public_key_pem) VALUES ('Sona','SONA','x')")
        db.commit()
        self.inst = 1

    def tearDown(self):
        self.ctx.pop()

    def test_parse_register_no(self):
        p = au.parse_register_no("61782323102081")
        self.assertEqual((p["batch_year"], p["graduation_year"]), (2023, 2027))
        self.assertEqual(p["programme"], "B.E. Computer Science and Engineering")
        self.assertEqual(au.clean_register_no("6.1782323102081E+13"), "61782323102081")
        self.assertEqual(au.clean_register_no("61782323102081.0"), "61782323102081")

    def test_import_report_and_idempotent(self):
        rows = au.read_rows(CSV.encode(), "x.csv")
        r = au.import_rows(rows, self.inst)
        self.assertEqual(r["students_created"], 4)
        self.assertEqual(r["grades_saved"], 3)
        self.assertEqual(len(r["errors"]), 1)           # bad grade 'Z'
        self.assertIn("invalid grade", r["errors"][0]["error"])
        s = au.find_student("61782323102081")
        self.assertEqual(s["date_of_birth"], "2005-03-14")
        self.assertEqual(s["batch_year"], 2023)
        self.assertEqual(s["programme"], "B.E. Computer Science and Engineering")
        r2 = au.import_rows(rows, self.inst)             # re-import: no duplicates
        self.assertEqual(r2["students_created"], 0)
        n = get_db().execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
        self.assertEqual(n, 4)

    def test_dry_run_saves_nothing(self):
        au.import_rows(au.read_rows(CSV.encode(), "x.csv"), self.inst, dry_run=True)
        self.assertEqual(get_db().execute("SELECT COUNT(*) c FROM students").fetchone()["c"], 0)

    def test_summary_gpa(self):
        au.import_rows(au.read_rows(CSV.encode(), "x.csv"), self.inst)
        s = au.find_student("61782323102081")
        sm = au.get_student_summary(s)
        self.assertEqual(sm["cgpa"], round((10 * 4 + 8 * 3) / 7, 2))

    def test_auto_issue_idempotent_and_supersede(self):
        au.import_rows(au.read_rows(CSV.encode(), "x.csv"), self.inst)
        s = au.find_student("61782323102081")
        with mock.patch("app.services.issuance.issue_certificate", fake_issue):
            a = au.auto_issue(s["id"])
            b = au.auto_issue(s["id"])
            self.assertFalse(a["reused"]); self.assertTrue(b["reused"])
            self.assertEqual(a["certificate_id"], b["certificate_id"])
            # change a grade -> new certificate, old one superseded
            au.import_rows(au.read_rows(
                (CSV.splitlines()[0] + "\n61782323102081,,,,,,,1,CS3101,,,O\n").encode(), "x.csv"), self.inst)
            c = au.auto_issue(s["id"])
            self.assertFalse(c["reused"])
            st = get_db().execute("SELECT status FROM certificates WHERE id=?", (a["certificate_id"],)).fetchone()
            self.assertEqual(st["status"], "superseded")

    def test_portal_requires_dob_and_isolates_students(self):
        au.import_rows(au.read_rows(CSV.encode(), "x.csv"), self.inst)
        c = self.app.test_client()
        r = c.post("/student", data={"action": "login", "reg_no": "61782323102081", "dob": "2000-01-01"})
        self.assertIn(b"incorrect", r.data)
        r = c.post("/student", data={"action": "login", "reg_no": "61782323102081", "dob": "2005-03-14"}, follow_redirects=True)
        self.assertIn(b"LOKESHWARAN", r.data)
        with mock.patch("app.services.issuance.issue_certificate", fake_issue):
            other = au.find_student("61782323102079")
            cid = fake_issue(other["id"])["certificate_id"]
        self.assertEqual(c.get(f"/student/download/{cid}").status_code, 403)

    def test_admin_pages_need_login(self):
        c = self.app.test_client()
        for u in ("/admin/import", "/admin/lookup", "/api/student/61782323102081"):
            self.assertEqual(c.get(u).status_code, 302)


if __name__ == "__main__":
    unittest.main()
