"""
Invoice Maker - Flask Backend
===============================
Run:  python app.py
Open: http://localhost:5000

Dependencies (install once):
    pip install flask flask-cors reportlab
"""

import os
import json
import sqlite3
import base64
import io
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, send_file, send_from_directory, g
from flask_cors import CORS

# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "invoices.db")
STATIC   = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
CORS(app)  # allow the HTML frontend on any port to call this API


# ─────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────
def get_db():
    """Return a thread-local SQLite connection."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    """Create tables if they don't exist yet."""
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS invoices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT    NOT NULL CHECK(type IN ('b2c','b2b')),
            invoice_num INTEGER NOT NULL,
            client_name TEXT    NOT NULL,
            total       REAL    NOT NULL,
            created_at  TEXT    NOT NULL,
            payload     TEXT    NOT NULL   -- full JSON snapshot of the invoice
        );

        CREATE TABLE IF NOT EXISTS counters (
            key   TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 1
        );

        INSERT OR IGNORE INTO counters(key, value) VALUES ('inv_a', 1);
        INSERT OR IGNORE INTO counters(key, value) VALUES ('inv_b', 1);
    """)
    db.commit()
    db.close()
    print("✅  Database ready:", DB_PATH)


# ─────────────────────────────────────────────
# Utility: Indian number formatting
# ─────────────────────────────────────────────
def fmt_inr(amount: float) -> str:
    """Format a number in Indian style: 1,23,456.00"""
    amount = round(amount, 2)
    s = f"{amount:,.2f}"          # standard US formatting first
    # convert to Indian grouping
    parts = s.split(".")
    integer_part = parts[0].replace(",", "")
    decimal_part  = parts[1]

    if len(integer_part) <= 3:
        return f"₹{integer_part}.{decimal_part}"

    last3  = integer_part[-3:]
    rest   = integer_part[:-3]
    groups = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    groups.reverse()
    return "₹" + ",".join(groups) + "," + last3 + "." + decimal_part


def today_str() -> str:
    return datetime.now().strftime("%d %b %Y")          # e.g. 24 Mar 2025


def month_year_str() -> str:
    return datetime.now().strftime("%B %Y")             # e.g. March 2025


def pad2(n: int) -> str:
    return str(n).zfill(2)


# ─────────────────────────────────────────────
# PDF generator (ReportLab)
# ─────────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily


# ─────────────────────────────────────────────
# Register Unicode fonts (required for ₹ symbol)
# ─────────────────────────────────────────────
# Primary paths (Linux / Ubuntu — where DejaVu is pre-installed)
_DEJAVU_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_DEJAVU_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Fallback search paths for Windows and macOS
_FALLBACK_REGULAR = [
    "C:/Windows/Fonts/arial.ttf",          # Windows
    "/Library/Fonts/Arial.ttf",            # macOS
    "/System/Library/Fonts/Helvetica.ttc", # macOS system
]
_FALLBACK_BOLD = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _register_unicode_fonts():
    """Register a TTF font that supports the ₹ (Rupee) symbol."""
    candidates = [(p, _DEJAVU_BOLD) for p in [_DEJAVU_REGULAR]]
    for fb_r, fb_b in zip(_FALLBACK_REGULAR, _FALLBACK_BOLD):
        candidates.append((fb_r, fb_b))

    for reg_path, bold_path in candidates:
        if os.path.exists(reg_path):
            try:
                pdfmetrics.registerFont(TTFont("UniSans",      reg_path))
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont("UniSans-Bold", bold_path))
                else:
                    pdfmetrics.registerFont(TTFont("UniSans-Bold", reg_path))
                registerFontFamily(
                    "UniSans",
                    normal="UniSans", bold="UniSans-Bold",
                    italic="UniSans", boldItalic="UniSans-Bold",
                )
                print(f"✅  Unicode font registered: {reg_path}")
                return "UniSans", "UniSans-Bold"
            except Exception as e:
                print(f"⚠️   Font registration failed for {reg_path}: {e}")

    # Last resort — built-in Helvetica (no ₹ but won't crash)
    print("⚠️   No Unicode TTF found — ₹ symbol may not render in PDF.")
    return "Helvetica", "Helvetica-Bold"


F_NORMAL, F_BOLD = _register_unicode_fonts()

ACCENT  = colors.HexColor("#7241FA")
DARK    = colors.HexColor("#1A1C21")
GRAY    = colors.HexColor("#5E6470")
LIGHT   = colors.HexColor("#D7DAE0")
BG      = colors.HexColor("#F9FAFC")
WHITE   = colors.white


def _style(name, **kw):
    # Default every style to the Unicode font so ₹ always renders correctly
    kw.setdefault("fontName", F_NORMAL)
    base = getSampleStyleSheet()["Normal"]
    return ParagraphStyle(name, parent=base, **kw)


def build_b2c_pdf(data: dict) -> bytes:
    """Generate a B2C invoice PDF and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm,  bottomMargin=14*mm,
    )

    S_HEAD  = _style("head",  fontSize=10, textColor=DARK,  fontName=F_BOLD)
    S_SUB   = _style("sub",   fontSize=8,  textColor=GRAY,  fontName=F_NORMAL)
    S_LABEL = _style("lbl",   fontSize=7,  textColor=GRAY,  fontName=F_NORMAL)
    S_VAL   = _style("val",   fontSize=7,  textColor=DARK,  fontName=F_BOLD)
    S_MONO  = _style("mono",  fontSize=8,  textColor=DARK,  fontName=F_BOLD)
    S_ACC   = _style("acc",   fontSize=9,  textColor=ACCENT, fontName=F_BOLD)
    S_RIGHT = _style("right", fontSize=8,  textColor=GRAY,  alignment=TA_RIGHT)
    S_RTOP  = _style("rtop",  fontSize=8,  textColor=DARK,  alignment=TA_RIGHT, fontName=F_BOLD)

    rows   = data.get("rows", [])
    total  = sum(float(r.get("amount", 0) or 0) for r in rows)
    num    = data.get("invoice_num", 1)

    story = []

    # ── Header row ──────────────────────────────────────────────────────────
    header_data = [
        [
            Paragraph("<b>INVOICE</b><br/>"
                      f"<font size='7' color='#5E6470'>{month_year_str().upper()}</font>", S_HEAD),
            Paragraph(f"<font color='#5E6470'>INV-{pad2(num)}</font>", _style("inv_no", fontSize=8, alignment=TA_RIGHT, textColor=GRAY))
        ]
    ]
    t = Table(header_data, colWidths=["80%", "20%"])
    t.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE")]))
    story.append(t)
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT, spaceAfter=6))

    # ── Info cells: Issued / Billed To / From ───────────────────────────────
    billed_name = data.get("client_name", "—")
    billed_addr = data.get("client_address", "").replace("\n", "<br/>")
    billed_gst  = data.get("client_gst", "")
    from_name   = data.get("from_name", "—")
    from_addr   = data.get("from_address", "").replace("\n", "<br/>")
    from_pan    = data.get("from_pan", "DKBPS5468G")

    info_data = [[
        Paragraph(f"<b>Issued</b><br/><font size='7' color='#5E6470'>{today_str()}</font>", S_HEAD),
        Paragraph(
            f"<b>Billed to</b><br/>"
            f"<b><font size='7' color='#5E6470'>{billed_name}</font></b><br/>"
            f"<font size='6' color='#5E6470'>{billed_addr}</font><br/>"
            f"<font size='6' color='#5E6470'>{billed_gst}</font>", S_HEAD),
        Paragraph(
            f"<b>From</b><br/>"
            f"<font size='7' color='#5E6470'>{from_name}</font><br/>"
            f"<font size='6' color='#5E6470'>{from_addr}</font><br/>"
            f"<font size='6' color='#5E6470'>PAN: {from_pan}</font>", S_HEAD),
    ]]
    t2 = Table(info_data, colWidths=["25%", "37.5%", "37.5%"])
    t2.setStyle(TableStyle([
        ("BOX",       (0,0), (0,0), 0.5, LIGHT),
        ("BOX",       (1,0), (1,0), 0.5, LIGHT),
        ("BOX",       (2,0), (2,0), 0.5, LIGHT),
        ("LINEBEFORE",(1,0), (1,0), 0,   colors.white),
        ("VALIGN",    (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",(0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0),(-1,-1), 10),
    ]))
    story.append(Spacer(1, 4*mm))
    story.append(t2)
    story.append(Spacer(1, 6*mm))

    # ── Services table ───────────────────────────────────────────────────────
    svc_header = [
        Paragraph("<b>Service</b>", S_HEAD),
        Paragraph("<b>Total Price</b>", _style("th_r", fontSize=8, fontName=F_BOLD, textColor=DARK, alignment=TA_RIGHT)),
    ]
    svc_rows = [svc_header]
    for r in rows:
        desc   = r.get("description", "—") or "—"
        amount = float(r.get("amount", 0) or 0)
        svc_rows.append([
            Paragraph(desc, _style("svc_d", fontSize=8, textColor=GRAY)),
            Paragraph(fmt_inr(amount), _style("svc_a", fontSize=8, textColor=GRAY, alignment=TA_RIGHT)),
        ])

    svc_table = Table(svc_rows, colWidths=["75%", "25%"])
    svc_style = [
        ("LINEBELOW",   (0,0), (-1,0),   0.5, LIGHT),
        ("LINEBELOW",   (0,1), (-1,-1),  0.5, LIGHT),
        ("VALIGN",      (0,0), (-1,-1),  "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1),  6),
        ("BOTTOMPADDING",(0,0),(-1,-1),  6),
        ("LEFTPADDING", (0,0), (0,-1),   0),
        ("RIGHTPADDING",(1,0), (1,-1),   0),
    ]
    svc_table.setStyle(TableStyle(svc_style))
    story.append(svc_table)

    # ── Total / Amount Due ───────────────────────────────────────────────────
    totals_data = [
        ["", Paragraph("Total",       _style("tl",  fontSize=8, textColor=DARK)),
              Paragraph(fmt_inr(total), _style("tr", fontSize=8, textColor=GRAY, alignment=TA_RIGHT))],
        ["", Paragraph("<b>Amount due</b>", _style("dl", fontSize=9, textColor=ACCENT, fontName=F_BOLD)),
              Paragraph(f"<b>{fmt_inr(total)}</b>", _style("dr", fontSize=9, textColor=ACCENT, fontName=F_BOLD, alignment=TA_RIGHT))],
    ]
    tot_table = Table(totals_data, colWidths=["50%", "30%", "20%"])
    tot_table.setStyle(TableStyle([
        ("LINEABOVE",    (1,0), (2,0),  0.5, LIGHT),
        ("LINEABOVE",    (1,1), (2,1),  1.5, ACCENT),
        ("LINEBELOW",    (1,1), (2,1),  1.5, ACCENT),
        ("TOPPADDING",   (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0), (-1,-1), 8),
        ("RIGHTPADDING", (2,0), (2,-1),  0),
    ]))
    story.append(Spacer(1, 2*mm))
    story.append(tot_table)
    story.append(Spacer(1, 8*mm))

    # ── Payment Details ───────────────────────────────────────────────────────
    story.append(Paragraph("<b>Payment Details</b>", S_HEAD))
    story.append(Spacer(1, 3*mm))

    pay = data.get("payment", {})
    pay_rows = [
        ("Name on Account", pay.get("account_name",   from_name)),
        ("Name of Bank",    pay.get("bank_name",       "HDFC Bank")),
        ("Branch",          pay.get("branch",          "Indiranagar, Bengaluru, Karnataka 560008")),
        ("Account Number",  pay.get("account_number",  "50100090346771")),
        ("IFSC",            pay.get("ifsc",            "HDFC0002777")),
        ("Swift",           pay.get("swift",           "HDFCINBBBNG")),
    ]
    for label, value in pay_rows:
        row_data = [[
            Paragraph(f"{label} :", _style("pl", fontSize=7, textColor=GRAY, fontName=F_NORMAL)),
            Paragraph(f"<b>{value}</b>", _style("pv", fontSize=7, textColor=DARK, fontName=F_BOLD)),
        ]]
        pt = Table(row_data, colWidths=["35%", "65%"])
        pt.setStyle(TableStyle([
            ("TOPPADDING",    (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
            ("LEFTPADDING",   (0,0), (0,-1),  0),
        ]))
        story.append(pt)

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("<b>Thank you for the business!</b>", S_HEAD))
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT))
    story.append(Spacer(1, 3*mm))

    # Footer
    footer_addr = from_addr.replace("<br/>", ", ")
    footer_text = (
        f"<font color='#5E6470'>{footer_addr} &nbsp;|&nbsp; "
        f"+91 63625 23476 &nbsp;|&nbsp; shylesh@npl.live</font>"
    )
    story.append(Paragraph(footer_text, _style("ft", fontSize=7, textColor=GRAY)))

    doc.build(story)
    return buf.getvalue()


def build_b2b_pdf(data: dict) -> bytes:
    """Generate a B2B GST Tax Invoice PDF and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm,  bottomMargin=14*mm,
    )

    S_HEAD  = _style("h2",   fontSize=10, textColor=DARK,   fontName=F_BOLD)
    S_LABEL = _style("lb2",  fontSize=7,  textColor=GRAY,   fontName=F_NORMAL)
    S_VAL   = _style("vl2",  fontSize=7,  textColor=DARK,   fontName=F_BOLD)
    S_ACC   = _style("ac2",  fontSize=9,  textColor=ACCENT, fontName=F_BOLD)
    S_RIGHT = _style("rt2",  fontSize=8,  textColor=GRAY,   alignment=TA_RIGHT)

    rows   = data.get("rows", [])
    sub    = sum(float(r.get("monthly", 0) or 0) for r in rows)
    cgst   = sub * 0.09
    sgst   = sub * 0.09
    total  = sub + cgst + sgst
    num    = data.get("invoice_num", 1)

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(
            "<b>TAX INVOICE</b><br/>"
            f"<font size='7' color='#5E6470'>{month_year_str().upper()}</font><br/>"
            f"<font size='7' color='#5E6470'>Invoice No: {num}</font>", S_HEAD),
        Paragraph(
            f"<b>{data.get('from_name','')}</b><br/>"
            f"<font size='7' color='#5E6470'>GST: {data.get('from_gst','')}</font><br/>"
            f"<font size='7' color='#5E6470'>SAC: {data.get('sac_code','')} | State: {data.get('state_code','')}</font>",
            _style("hdr_r", fontSize=8, alignment=TA_RIGHT, textColor=DARK))
    ]]
    ht = Table(header_data, colWidths=["60%","40%"])
    ht.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story.append(ht)
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT, spaceAfter=6))

    # ── Info cells ──────────────────────────────────────────────────────────
    billed_name = data.get("client_name", "—")
    billed_addr = data.get("client_address", "").replace("\n", "<br/>")
    billed_gst  = data.get("client_gst", "")
    from_addr   = data.get("from_address", "").replace("\n", "<br/>")
    from_gst    = data.get("from_gst", "")
    state_code  = data.get("state_code", "")
    sac_code    = data.get("sac_code", "")

    info_data = [[
        Paragraph(f"<b>Issued</b><br/><font size='7' color='#5E6470'>{today_str()}</font>", S_HEAD),
        Paragraph(
            f"<b>Billed to</b><br/>"
            f"<b><font size='7' color='#5E6470'>{billed_name}</font></b><br/>"
            f"<font size='6' color='#5E6470'>{billed_addr}</font><br/>"
            f"<b><font size='6' color='#5E6470'>GST: {billed_gst}</font></b>", S_HEAD),
        Paragraph(
            f"<b>From</b><br/>"
            f"<font size='7' color='#5E6470'>{data.get('from_name','')}</font><br/>"
            f"<font size='6' color='#5E6470'>{from_addr}</font><br/>"
            f"<b><font size='6' color='#5E6470'>State: {state_code} | SAC: {sac_code}</font></b>", S_HEAD),
    ]]
    t2 = Table(info_data, colWidths=["25%","37.5%","37.5%"])
    t2.setStyle(TableStyle([
        ("BOX",       (0,0),(0,0), 0.5, LIGHT),
        ("BOX",       (1,0),(1,0), 0.5, LIGHT),
        ("BOX",       (2,0),(2,0), 0.5, LIGHT),
        ("VALIGN",    (0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING",(0,0),(-1,-1), 10),
    ]))
    story.append(Spacer(1, 4*mm))
    story.append(t2)
    story.append(Spacer(1, 6*mm))

    # ── Services table ───────────────────────────────────────────────────────
    svc_header = [
        Paragraph("<b>Service</b>",     S_HEAD),
        Paragraph("<b>Duration</b>",    S_HEAD),
        Paragraph("<b>Monthly (₹)</b>", _style("mh", fontSize=8, fontName=F_BOLD, textColor=DARK, alignment=TA_RIGHT)),
        Paragraph("<b>Total Price</b>", _style("th", fontSize=8, fontName=F_BOLD, textColor=DARK, alignment=TA_RIGHT)),
    ]
    svc_rows = [svc_header]
    for r in rows:
        desc     = r.get("description", "—") or "—"
        dur      = r.get("duration", "—")    or "—"
        monthly  = float(r.get("monthly", 0) or 0)
        svc_rows.append([
            Paragraph(desc,              _style("sd", fontSize=8, textColor=GRAY, fontName=F_BOLD)),
            Paragraph(dur,               _style("du", fontSize=8, textColor=GRAY)),
            Paragraph(fmt_inr(monthly),  _style("mo", fontSize=8, textColor=GRAY, alignment=TA_RIGHT)),
            Paragraph(fmt_inr(monthly),  _style("tp", fontSize=8, textColor=GRAY, alignment=TA_RIGHT)),
        ])

    svc_table = Table(svc_rows, colWidths=["42%","20%","19%","19%"])
    svc_table.setStyle(TableStyle([
        ("LINEBELOW",    (0,0),(-1,0),  0.5, LIGHT),
        ("LINEBELOW",    (0,1),(-1,-1), 0.5, LIGHT),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0),(-1,-1), 6),
        ("BOTTOMPADDING",(0,0),(-1,-1), 6),
        ("LEFTPADDING",  (0,0),(0,-1),  0),
        ("RIGHTPADDING", (3,0),(3,-1),  0),
    ]))
    story.append(svc_table)

    # ── Tax breakdown + Amount Due ───────────────────────────────────────────
    tax_rows = [
        ["", Paragraph("Subtotal",          _style("stl", fontSize=8, textColor=DARK)),
              Paragraph(fmt_inr(sub),        _style("str", fontSize=8, textColor=GRAY, alignment=TA_RIGHT))],
        ["", Paragraph("CGST (9%)",          _style("cl",  fontSize=8, textColor=DARK)),
              Paragraph(fmt_inr(cgst),        _style("cr",  fontSize=8, textColor=GRAY, alignment=TA_RIGHT))],
        ["", Paragraph("SGST (9%)",          _style("sl2", fontSize=8, textColor=DARK)),
              Paragraph(fmt_inr(sgst),        _style("sr",  fontSize=8, textColor=GRAY, alignment=TA_RIGHT))],
        ["", Paragraph("<b>Amount due</b>",  _style("aml", fontSize=9, fontName=F_BOLD, textColor=ACCENT)),
              Paragraph(f"<b>{fmt_inr(total)}</b>", _style("amr", fontSize=9, fontName=F_BOLD, textColor=ACCENT, alignment=TA_RIGHT))],
    ]
    tax_table = Table(tax_rows, colWidths=["50%","30%","20%"])
    tax_table.setStyle(TableStyle([
        ("LINEABOVE",    (1,0),(2,0),  0.5, LIGHT),
        ("LINEABOVE",    (1,3),(2,3),  1.5, ACCENT),
        ("LINEBELOW",    (1,3),(2,3),  1.5, ACCENT),
        ("TOPPADDING",   (0,0),(-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
        ("RIGHTPADDING", (2,0),(2,-1),  0),
    ]))
    story.append(Spacer(1, 2*mm))
    story.append(tax_table)
    story.append(Spacer(1, 8*mm))

    # ── Payment Details ───────────────────────────────────────────────────────
    story.append(Paragraph("<b>Payment Details</b>", S_HEAD))
    story.append(Spacer(1, 3*mm))

    pay = data.get("payment", {})
    pay_rows_list = [
        ("Name on Account", pay.get("account_name",  "Togepe tech (OPC) Pvt Ltd")),
        ("Name of Bank",    pay.get("bank_name",      "HDFC Bank")),
        ("Branch",          pay.get("branch",         "BILEKAHALLI – J.P NAGAR 4TH PHASE")),
        ("Account Number",  pay.get("account_number", "50200084872288")),
        ("IFSC",            pay.get("ifsc",           "HDFC0002777")),
        ("Swift",           pay.get("swift",          "HDFCINBBBNG")),
    ]
    for label, value in pay_rows_list:
        row_data = [[
            Paragraph(f"{label} :", _style("pl2", fontSize=7, textColor=GRAY)),
            Paragraph(f"<b>{value}</b>", _style("pv2", fontSize=7, textColor=DARK, fontName=F_BOLD)),
        ]]
        pt = Table(row_data, colWidths=["35%","65%"])
        pt.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 2),
            ("BOTTOMPADDING", (0,0),(-1,-1), 2),
            ("LEFTPADDING",   (0,0),(0,-1),  0),
        ]))
        story.append(pt)

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("<b>Thank you for the business!</b>", S_HEAD))
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT))
    story.append(Spacer(1, 3*mm))

    # Footer
    from_addr_txt = data.get("from_address", "").replace("\n", ", ")
    footer_text = (
        f"<font color='#5E6470'>{from_addr_txt} &nbsp;|&nbsp; "
        "+91 63625 23476 &nbsp;|&nbsp; shylesh@npl.live</font>"
    )
    story.append(Paragraph(footer_text, _style("ft2", fontSize=7, textColor=GRAY)))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────
def require_json(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 415
        return f(*args, **kwargs)
    return wrapper


def validate_b2c(data: dict):
    errors = []
    if not data.get("client_name", "").strip():
        errors.append("client_name is required")
    if not data.get("from_name", "").strip():
        errors.append("from_name is required")
    rows = data.get("rows", [])
    if not rows:
        errors.append("At least one service row is required")
    for i, r in enumerate(rows):
        if not r.get("description", "").strip():
            errors.append(f"Row {i+1}: description is required")
        try:
            v = float(r.get("amount", 0) or 0)
            if v < 0:
                errors.append(f"Row {i+1}: amount must be >= 0")
        except (ValueError, TypeError):
            errors.append(f"Row {i+1}: amount must be a number")
    return errors


def validate_b2b(data: dict):
    errors = []
    for field in ["client_name", "from_name", "from_gst"]:
        if not data.get(field, "").strip():
            errors.append(f"{field} is required")
    rows = data.get("rows", [])
    if not rows:
        errors.append("At least one service row is required")
    for i, r in enumerate(rows):
        if not r.get("description", "").strip():
            errors.append(f"Row {i+1}: description is required")
        try:
            v = float(r.get("monthly", 0) or 0)
            if v < 0:
                errors.append(f"Row {i+1}: monthly must be >= 0")
        except (ValueError, TypeError):
            errors.append(f"Row {i+1}: monthly must be a number")
    return errors


# ─────────────────────────────────────────────
# Counter helpers
# ─────────────────────────────────────────────
def get_counter(db, key: str) -> int:
    row = db.execute("SELECT value FROM counters WHERE key=?", (key,)).fetchone()
    return row["value"] if row else 1


def bump_counter(db, key: str) -> int:
    current = get_counter(db, key)
    db.execute("UPDATE counters SET value=value+1 WHERE key=?", (key,))
    return current


# ─────────────────────────────────────────────
# Routes – static frontend
# ─────────────────────────────────────────────
@app.route("/")
def index():
    """Serve the invoice maker HTML frontend."""
    html_path = os.path.join(BASE_DIR, "static", "invoice-maker.html")
    if os.path.exists(html_path):
        return send_from_directory(os.path.join(BASE_DIR, "static"), "invoice-maker.html")
    return (
        "<h2>Invoice Maker Backend is running ✅</h2>"
        "<p>Place <code>invoice-maker.html</code> into the <code>static/</code> folder "
        "and reload this page to use the full UI.</p>"
        "<p>API docs: <a href='/api/health'>/api/health</a></p>"
    ), 200


# ─────────────────────────────────────────────
# Routes – health
# ─────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": today_str()})


# ─────────────────────────────────────────────
# Routes – counters (so frontend stays in sync)
# ─────────────────────────────────────────────
@app.route("/api/counters")
def get_counters():
    db = get_db()
    return jsonify({
        "inv_a": get_counter(db, "inv_a"),
        "inv_b": get_counter(db, "inv_b"),
    })


# ─────────────────────────────────────────────
# Routes – B2C invoice
# ─────────────────────────────────────────────
@app.route("/api/invoice/b2c/preview", methods=["POST"])
@require_json
def b2c_preview():
    """
    Validate B2C data and return a summary (no DB write).
    Body: { client_name, client_address, client_gst, from_name, from_address,
            rows: [{description, amount}] }
    """
    data = request.get_json()
    errors = validate_b2c(data)
    if errors:
        return jsonify({"valid": False, "errors": errors}), 422

    rows  = data.get("rows", [])
    total = sum(float(r.get("amount", 0) or 0) for r in rows)
    return jsonify({
        "valid":       True,
        "total":       total,
        "total_fmt":   fmt_inr(total),
        "invoice_num": get_counter(get_db(), "inv_a"),
        "date":        today_str(),
    })


@app.route("/api/invoice/b2c/save", methods=["POST"])
@require_json
def b2c_save():
    """
    Save a B2C invoice to the database.
    Returns: { id, invoice_num, total, total_fmt }
    """
    data = request.get_json()
    errors = validate_b2c(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 422

    db  = get_db()
    num = bump_counter(db, "inv_a")
    db.commit()

    rows  = data.get("rows", [])
    total = sum(float(r.get("amount", 0) or 0) for r in rows)

    data["invoice_num"] = num
    data["created_at"]  = today_str()

    db.execute(
        "INSERT INTO invoices (type, invoice_num, client_name, total, created_at, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("b2c", num, data.get("client_name",""), total, today_str(), json.dumps(data))
    )
    db.commit()

    row_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return jsonify({
        "id":          row_id,
        "invoice_num": num,
        "total":       total,
        "total_fmt":   fmt_inr(total),
        "message":     "Invoice saved successfully",
    }), 201


@app.route("/api/invoice/b2c/pdf", methods=["POST"])
@require_json
def b2c_pdf():
    """
    Generate and return a B2C invoice PDF (does NOT save to DB).
    Use /b2c/save first, then /b2c/pdf, or combine them on the frontend.
    """
    data   = request.get_json()
    errors = validate_b2c(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 422

    db  = get_db()
    num = data.get("invoice_num") or get_counter(db, "inv_a")
    data["invoice_num"] = num

    pdf_bytes = build_b2c_pdf(data)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"invoice_b2c_{pad2(num)}.pdf",
    )


# ─────────────────────────────────────────────
# Routes – B2B invoice
# ─────────────────────────────────────────────
@app.route("/api/invoice/b2b/preview", methods=["POST"])
@require_json
def b2b_preview():
    """
    Validate B2B data and return a tax breakdown summary.
    Body: { client_name, client_address, client_gst, from_name, from_address,
            from_gst, sac_code, state_code,
            rows: [{description, duration, monthly}] }
    """
    data   = request.get_json()
    errors = validate_b2b(data)
    if errors:
        return jsonify({"valid": False, "errors": errors}), 422

    rows = data.get("rows", [])
    sub  = sum(float(r.get("monthly", 0) or 0) for r in rows)
    cgst = sub * 0.09
    sgst = sub * 0.09
    total = sub + cgst + sgst

    return jsonify({
        "valid":       True,
        "subtotal":    sub,
        "cgst":        cgst,
        "sgst":        sgst,
        "total":       total,
        "subtotal_fmt": fmt_inr(sub),
        "cgst_fmt":    fmt_inr(cgst),
        "sgst_fmt":    fmt_inr(sgst),
        "total_fmt":   fmt_inr(total),
        "invoice_num": get_counter(get_db(), "inv_b"),
        "date":        today_str(),
    })


@app.route("/api/invoice/b2b/save", methods=["POST"])
@require_json
def b2b_save():
    """Save a B2B invoice to the database."""
    data   = request.get_json()
    errors = validate_b2b(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 422

    db  = get_db()
    num = bump_counter(db, "inv_b")
    db.commit()

    rows  = data.get("rows", [])
    sub   = sum(float(r.get("monthly", 0) or 0) for r in rows)
    total = sub * 1.18

    data["invoice_num"] = num
    data["created_at"]  = today_str()

    db.execute(
        "INSERT INTO invoices (type, invoice_num, client_name, total, created_at, payload) "
        "VALUES (?,?,?,?,?,?)",
        ("b2b", num, data.get("client_name",""), total, today_str(), json.dumps(data))
    )
    db.commit()

    row_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return jsonify({
        "id":          row_id,
        "invoice_num": num,
        "total":       total,
        "total_fmt":   fmt_inr(total),
        "message":     "Invoice saved successfully",
    }), 201


@app.route("/api/invoice/b2b/pdf", methods=["POST"])
@require_json
def b2b_pdf():
    """Generate and return a B2B GST Tax Invoice PDF."""
    data   = request.get_json()
    errors = validate_b2b(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 422

    db  = get_db()
    num = data.get("invoice_num") or get_counter(db, "inv_b")
    data["invoice_num"] = num

    pdf_bytes = build_b2b_pdf(data)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"invoice_b2b_{pad2(num)}.pdf",
    )


# ─────────────────────────────────────────────
# Routes – history
# ─────────────────────────────────────────────
@app.route("/api/invoices")
def list_invoices():
    """
    List all saved invoices (newest first).
    Query params: ?type=b2c|b2b  ?limit=50  ?offset=0
    """
    inv_type = request.args.get("type")        # optional filter
    limit    = min(int(request.args.get("limit",  100)), 500)
    offset   = int(request.args.get("offset", 0))

    db = get_db()
    if inv_type in ("b2c", "b2b"):
        rows = db.execute(
            "SELECT id,type,invoice_num,client_name,total,created_at FROM invoices "
            "WHERE type=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (inv_type, limit, offset)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id,type,invoice_num,client_name,total,created_at FROM invoices "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()

    total_count = db.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]

    return jsonify({
        "total":    total_count,
        "limit":    limit,
        "offset":   offset,
        "invoices": [
            {
                "id":          r["id"],
                "type":        r["type"],
                "invoice_num": r["invoice_num"],
                "client_name": r["client_name"],
                "total":       r["total"],
                "total_fmt":   fmt_inr(r["total"]),
                "created_at":  r["created_at"],
            }
            for r in rows
        ],
    })


@app.route("/api/invoices/<int:inv_id>")
def get_invoice(inv_id: int):
    """Fetch the full payload of a single saved invoice."""
    db  = get_db()
    row = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Invoice {inv_id} not found"}), 404

    payload = json.loads(row["payload"])
    return jsonify({
        "id":          row["id"],
        "type":        row["type"],
        "invoice_num": row["invoice_num"],
        "client_name": row["client_name"],
        "total":       row["total"],
        "total_fmt":   fmt_inr(row["total"]),
        "created_at":  row["created_at"],
        "data":        payload,
    })


@app.route("/api/invoices/<int:inv_id>/pdf")
def download_invoice_pdf(inv_id: int):
    """Re-generate and download the PDF for a saved invoice."""
    db  = get_db()
    row = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Invoice {inv_id} not found"}), 404

    data      = json.loads(row["payload"])
    inv_type  = row["type"]
    num       = row["invoice_num"]

    if inv_type == "b2c":
        pdf_bytes = build_b2c_pdf(data)
        filename  = f"invoice_b2c_{pad2(num)}.pdf"
    else:
        pdf_bytes = build_b2b_pdf(data)
        filename  = f"invoice_b2b_{pad2(num)}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/invoices/<int:inv_id>", methods=["DELETE"])
def delete_invoice(inv_id: int):
    """Delete a saved invoice."""
    db  = get_db()
    row = db.execute("SELECT id FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Invoice {inv_id} not found"}), 404

    db.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    db.commit()
    return jsonify({"message": f"Invoice {inv_id} deleted"})


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("🚀  Starting Invoice Maker backend on http://localhost:5000")
    print("📂  Database:", DB_PATH)
    print("📁  Static files:", STATIC)
    app.run(host="0.0.0.0", port=5000, debug=True)
