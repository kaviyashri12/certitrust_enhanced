import os
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import base64
from datetime import datetime

def generate_marksheet_pdf(student, grades, gpa, qr_b64, certificate_id, semester=None, institution_name="Sona College of Technology"):
    """
    Generate a clean white A4 marksheet PDF
    """
    buffer = BytesIO()
    page_width, page_height = A4

    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # ---------- HEADER ----------
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 40, institution_name.upper())

    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height - 55, "(An Autonomous Institution Affiliated to Anna University)")

    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(width/2, height - 80, "STATEMENT OF GRADES" if semester else "CONSOLIDATED STATEMENT OF GRADES")

    # Horizontal line
    c.setStrokeColor(colors.HexColor("#333333"))
    c.setLineWidth(1)
    c.line(40, height - 95, width - 40, height - 95)

    # ---------- STUDENT INFO (Left side) ----------
    y = height - 130
    c.setFont("Helvetica", 10)

    name = student["name"] if student["name"] else ""
    reg_no = student["register_no"] if student["register_no"] else ""
    programme = student["programme"] if student["programme"] else ""
    dob = student["date_of_birth"] if "date_of_birth" in student.keys() and student["date_of_birth"] else ""

    c.drawString(45, y, f"Name of the Candidate : {name}")
    c.drawString(45, y - 18, f"Register No           : {reg_no}")
    c.drawString(45, y - 36, f"Programme            : {programme}")
    if dob:
        c.drawString(45, y - 54, f"Date of Birth         : {dob}")

    # Move Certificate ID, Semester, Issue Date below student details
    info_y = y - 80 if dob else y - 60
    c.setFont("Helvetica", 10)
    c.drawString(45, info_y, f"Certificate ID        : {certificate_id}")
    if semester:
        c.drawString(45, info_y - 18, f"Semester              : {semester}")
        c.drawString(45, info_y - 36, f"Issue Date            : {datetime.now().strftime('%d-%m-%Y')}")
    else:
        c.drawString(45, info_y - 18, f"Issue Date            : {datetime.now().strftime('%d-%m-%Y')}")

        # ---------- QR CODE (Top Right) ----------
    try:
        qr_img_data = base64.b64decode(qr_b64)
        qr_buffer = BytesIO(qr_img_data)
        qr_img = ImageReader(qr_buffer)

        qr_size = 110          # larger QR
        qr_x = width - 50 - qr_size
        qr_y = height - 260

        c.drawImage(qr_img, qr_x, qr_y, width=qr_size, height=qr_size, mask='auto')
        c.setFont("Helvetica", 8)
        c.drawCentredString(qr_x + qr_size/2, qr_y - 12, "Scan to Verify")
    except Exception as e:
        print("QR embed error:", e)

    # ---------- GRADES TABLE ----------
    table_top = info_y - 70

    data = [["S.No", "Course Code", "Course Title", "Credits", "Grade"]]

    for idx, g in enumerate(grades, 1):
        data.append([
            str(idx),
            g["course_code"],
            g["course_name"][:42],
            str(g["credits"]),
            g["grade"]
        ])

    col_widths = [40, 90, 250, 60, 60]
    table = Table(data, colWidths=col_widths)

    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (3, 0), (4, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
    ])

    table.setStyle(style)

    table.wrapOn(c, width, height)
    table_height = len(data) * 22
    table.drawOn(c, 45, table_top - table_height)

    # ---------- GPA ----------
    gpa_y = table_top - table_height - 35
    c.setFont("Helvetica-Bold", 11)
    if semester:
        c.drawString(45, gpa_y, f"Semester Grade Point Average (SGPA) : {gpa}")
    else:
        c.drawString(45, gpa_y, f"Cumulative Grade Point Average (CGPA) : {gpa}")

    # ---------- FOOTER & SIGNATURE (Bottom) ----------
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 50, 90, "Controller of Examinations")

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(45, 40, "This is a digitally signed document. Verify authenticity by scanning the QR code.")
    c.drawString(45, 26, "Generated by CertiTrust – Field-Level Tamper-Proof Certificate System")

    c.save()
    buffer.seek(0)
    return buffer