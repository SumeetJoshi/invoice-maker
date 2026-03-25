# Invoice Maker — Python Backend

A complete Flask backend for the Invoice Maker app.
Supports B2C (Individual → Company) and B2B (Company → Company / GST) invoices
with PDF generation, SQLite storage, and a REST API.

---

## 📁 Project Structure

```
invoice-backend/
├── app.py                      ← Main Flask app (all backend logic)
├── requirements.txt            ← Python dependencies
├── invoices.db                 ← SQLite database (auto-created on first run)
├── static/
│   └── invoice-maker.html      ← Frontend (copy your HTML here)
└── README.md
```

---

## ⚡ Quick Start

### 1. Install dependencies

```bash
pip install flask flask-cors reportlab
```

### 2. Run the server

```bash
python app.py
```

You should see:
```
✅  Database ready: .../invoices.db
🚀  Starting Invoice Maker backend on http://localhost:5000
```

### 3. Open the app

Go to **http://localhost:5000** in your browser.

---

## 🔌 REST API Reference

### Health check
```
GET /api/health
→ { "status": "ok", "time": "24 Mar 2025" }
```

### Invoice counters (get current INV numbers)
```
GET /api/counters
→ { "inv_a": 3, "inv_b": 1 }
```

---

### B2C Invoice (Individual → Company)

#### Preview (validate + get totals, no DB save)
```
POST /api/invoice/b2c/preview
Content-Type: application/json

{
  "client_name":    "Infosys Limited",
  "client_address": "Electronics City\nBengaluru 560100",
  "client_gst":     "29AACCI1681G1ZM",
  "from_name":      "Shylesh Monceey",
  "from_address":   "No 42, 3rd Cross, Indiranagar\nBengaluru 560038",
  "rows": [
    { "description": "UI/UX Design Consulting", "amount": 45000 },
    { "description": "Prototype Delivery",       "amount": 15000 }
  ]
}

→ { "valid": true, "total": 60000, "total_fmt": "₹60,000.00", "invoice_num": 1, "date": "..." }
```

#### Save to database
```
POST /api/invoice/b2c/save    (same body as above)
→ { "id": 1, "invoice_num": 1, "total": 60000, "total_fmt": "₹60,000.00" }
```

#### Download PDF
```
POST /api/invoice/b2c/pdf     (same body as above)
→ Binary PDF file download
```

---

### B2B Invoice (Company → Company / GST Tax Invoice)

#### Preview
```
POST /api/invoice/b2b/preview
Content-Type: application/json

{
  "client_name":    "Tech Mahindra Ltd",
  "client_address": "Hitech City\nHyderabad 500081",
  "client_gst":     "27AABCT3518Q1ZE",
  "from_name":      "Togepe Tech (OPC) Pvt Ltd",
  "from_address":   "No 1075, 100 Feet Rd\nIndiranagar, Bengaluru 560008",
  "from_gst":       "29AAKCT4607P1ZL",
  "sac_code":       "998314",
  "state_code":     "29",
  "rows": [
    { "description": "Software Development", "duration": "1 Month", "monthly": 120000 },
    { "description": "Project Management",   "duration": "1 Month", "monthly": 35000  }
  ]
}

→ {
    "valid": true,
    "subtotal": 155000, "subtotal_fmt": "₹1,55,000.00",
    "cgst": 13950,      "cgst_fmt":     "₹13,950.00",
    "sgst": 13950,      "sgst_fmt":     "₹13,950.00",
    "total": 182900,    "total_fmt":    "₹1,82,900.00"
  }
```

#### Save to database
```
POST /api/invoice/b2b/save    (same body as above)
→ { "id": 2, "invoice_num": 1, "total": 182900, "total_fmt": "₹1,82,900.00" }
```

#### Download PDF
```
POST /api/invoice/b2b/pdf     (same body as above)
→ Binary PDF file download
```

---

### Invoice History

#### List all invoices
```
GET /api/invoices
GET /api/invoices?type=b2c          ← filter by type
GET /api/invoices?limit=20&offset=0 ← pagination

→ {
    "total": 5,
    "invoices": [
      { "id": 2, "type": "b2b", "invoice_num": 1, "client_name": "Tech Mahindra",
        "total": 182900, "total_fmt": "₹1,82,900.00", "created_at": "24 Mar 2025" },
      ...
    ]
  }
```

#### Get single invoice (with full data)
```
GET /api/invoices/2
→ { "id": 2, "type": "b2b", ..., "data": { ...full payload... } }
```

#### Download PDF of saved invoice
```
GET /api/invoices/2/pdf
→ Binary PDF file download
```

#### Delete invoice
```
DELETE /api/invoices/2
→ { "message": "Invoice 2 deleted" }
```

---

## 🗄️ Database

SQLite file `invoices.db` is created automatically in the same folder as `app.py`.

**Tables:**
- `invoices` — all saved invoices with full JSON payload
- `counters` — auto-incrementing invoice numbers (inv_a, inv_b)

---

## 🔧 Customising

### Change port
Edit the last line of `app.py`:
```python
app.run(host="0.0.0.0", port=5000, debug=True)
                          ^^^^
```

### Change hardcoded bank details / PAN
Search for them in `app.py` — they appear in `build_b2c_pdf()` and `build_b2b_pdf()`,
or pass them in the `payment` field of your JSON:

```json
"payment": {
  "account_name":   "Your Name",
  "bank_name":      "HDFC Bank",
  "branch":         "Your Branch",
  "account_number": "XXXXXXXXXXXX",
  "ifsc":           "HDFC0001234",
  "swift":          "HDFCINBBXXX"
}
```

### Enable HTTPS / production
Replace `app.run(debug=True)` with a production server like `gunicorn`:
```bash
pip install gunicorn
gunicorn app:app -b 0.0.0.0:5000
```
