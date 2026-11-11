from app.db import get_db
from app.crypto.keys import generate_rsa_keypair, save_private_key

GRADE_POINTS = {
    'O': 10.0, 'A+': 9.0, 'A': 8.0, 'B+': 7.0,
    'B': 6.0, 'C': 5.0, 'U': 0.0, 'SA': 0.0, 'W': 0.0
}

def seed_sample_data():
    db = get_db()

    # Check if institution already exists
    inst = db.execute("SELECT id FROM institutions WHERE code = 'SONA'").fetchone()
    if inst:
        inst_id = inst["id"]
    else:
        # Generate keys and save private key to file only
        priv, pub = generate_rsa_keypair(2048)
        save_private_key(priv)
        db.execute("""
            INSERT INTO institutions (name, code, public_key_pem)
            VALUES (?, ?, ?)
        """, ("Sona College of Technology", "SONA", pub))
        inst_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Check if student already exists
    student = db.execute(
        "SELECT id FROM students WHERE register_no = '61781921025'"
    ).fetchone()
    if student:
        return student["id"]

    # Create student
    db.execute("""
        INSERT INTO students (institution_id, register_no, name, programme, regulation, batch_year)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (inst_id, "61781921025", "Rahul Kumar",
          "B.E. Computer Science and Engineering", "2021", 2021))
    student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Courses
    courses = [
        ("CS8501", "Theory of Computation", 3.0),
        ("CS8591", "Computer Networks", 3.0),
        ("CS8491", "Computer Architecture", 3.0),
        ("MA8351", "Discrete Mathematics", 4.0),
        ("CS8391", "Data Structures", 3.0),
    ]
    course_ids = {}
    for code, name, credits in courses:
        existing = db.execute(
            "SELECT id FROM courses WHERE course_code = ?", (code,)
        ).fetchone()
        if existing:
            course_ids[code] = existing["id"]
        else:
            db.execute("""
                INSERT INTO courses (institution_id, course_code, course_name, credits)
                VALUES (?, ?, ?, ?)
            """, (inst_id, code, name, credits))
            course_ids[code] = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Grades
    grade_data = [
        (1, "CS8391", "A"),
        (1, "MA8351", "A+"),
        (2, "CS8491", "O"),
        (2, "CS8591", "A"),
        (3, "CS8501", "B+"),
    ]
    for sem, code, grade in grade_data:
        db.execute("""
            INSERT OR IGNORE INTO student_grades
            (student_id, semester, course_id, grade, grade_point)
            VALUES (?, ?, ?, ?, ?)
        """, (student_id, sem, course_ids[code], grade, GRADE_POINTS[grade]))

    db.commit()
    return student_id