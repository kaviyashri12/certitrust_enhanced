DROP TABLE IF EXISTS certificate_fields;
DROP TABLE IF EXISTS certificates;
DROP TABLE IF EXISTS student_grades;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS institutions;

CREATE TABLE institutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    public_key_pem TEXT,
    private_key_pem TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id INTEGER NOT NULL,
    register_no TEXT NOT NULL,
    name TEXT NOT NULL,
    programme TEXT NOT NULL,
    regulation TEXT,
    batch_year INTEGER,
    date_of_birth TEXT,
    gender TEXT,
    folio_no TEXT,
    UNIQUE(institution_id, register_no),
    FOREIGN KEY (institution_id) REFERENCES institutions(id)
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id INTEGER NOT NULL,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    credits REAL NOT NULL,
    UNIQUE(institution_id, course_code),
    FOREIGN KEY (institution_id) REFERENCES institutions(id)
);

CREATE TABLE student_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    grade TEXT NOT NULL,
    grade_point REAL,
    UNIQUE(student_id, semester, course_id),
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

CREATE TABLE certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    certificate_type TEXT DEFAULT 'degree',
    issue_date DATE NOT NULL,
    cgpa REAL,
    merkle_root TEXT,
    signature TEXT,
    qr_payload TEXT,
    data_fingerprint TEXT,
    status TEXT DEFAULT 'issued',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id)
);

CREATE TABLE certificate_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    certificate_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT NOT NULL,
    leaf_hash TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE(certificate_id, field_name),
    UNIQUE(certificate_id, position),
    FOREIGN KEY (certificate_id) REFERENCES certificates(id)
);