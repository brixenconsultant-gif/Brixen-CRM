"""
UK FormFill Pro — AD01 Form (Change of registered office address)
Standalone and embedded handler for Brixen Consultants CRM.
Generates official AD01 PDF and interactive web wizard.
"""

import os
import json
import urllib.parse
from datetime import datetime
from db import query_db, execute_db

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None


def generate_ad01_pdf(data):
    """
    Generate official UK Companies House Form AD01 (Change of registered office address) PDF.
    """
    company_number = str(data.get('company_number') or '').strip().upper()
    company_name = str(data.get('company_name') or '').strip().upper()
    
    building = str(data.get('address_line_1') or data.get('building_name_number') or '').strip()
    street = str(data.get('address_line_2') or data.get('street') or '').strip()
    post_town = str(data.get('post_town') or data.get('city') or 'London').strip()
    county = str(data.get('county') or '').strip()
    postcode = str(data.get('postcode') or '').strip().upper()
    
    signer_name = str(data.get('signer_name') or 'Authorised Signatory').strip()
    signer_role = str(data.get('signer_role') or 'Director').strip()
    sign_date = str(data.get('sign_date') or datetime.now().strftime('%d/%m/%Y')).strip()

    if not FPDF:
        raise RuntimeError("fpdf2 library not installed.")

    class AD01PDF(FPDF):
        def header(self):
            self.set_fill_color(0, 57, 113) # Brixen Navy #003971
            self.rect(0, 0, 210, 14, 'F')
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(255, 255, 255)
            self.set_xy(10, 3)
            self.cell(190, 8, 'COMPANIES HOUSE  |  FORM AD01', 0, 0, 'L')
            self.cell(0, 8, 'Change of registered office address', 0, 1, 'R')
            self.ln(6)

        def footer(self):
            self.set_y(-14)
            self.set_font('Helvetica', '', 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, f'AD01 - Change of registered office address | Page {self.page_no()} | Generated via Brixen UK FormFill Pro', 0, 0, 'C')

    pdf = AD01PDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Title Section
    pdf.set_text_color(0, 57, 113)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 9, 'AD01', 0, 1, 'L')
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 6, 'Change of registered office address', 0, 1, 'L')
    pdf.set_font('Helvetica', 'I', 9)
    pdf.set_text_color(100, 116, 139)
    pdf.multi_cell(0, 5, 'You can use the WebFiling service to file this form online, or submit this completed document to Companies House.')
    pdf.ln(4)

    # Section 1: Company details
    pdf.set_fill_color(241, 245, 249)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(0, 57, 113)
    pdf.cell(0, 7, '  1. Company details', 0, 1, 'L', fill=True)
    pdf.ln(2)

    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(45, 7, 'Company number:', 0, 0)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, company_number or 'N/A', 0, 1)

    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(45, 7, 'Company name in full:', 0, 0)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, company_name or 'N/A', 0, 1)
    pdf.ln(3)

    # Section 2: New registered office address
    pdf.set_fill_color(241, 245, 249)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(0, 57, 113)
    pdf.cell(0, 7, '  2. New registered office address', 0, 1, 'L', fill=True)
    pdf.ln(2)

    fields = [
        ('Building name/number', building or '100 Bishopsgate'),
        ('Street', street or 'London'),
        ('Post town', post_town or 'London'),
        ('County/Region', county or 'Greater London'),
        ('Postcode', postcode or 'EC2N 4AG'),
    ]

    for label, val in fields:
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(45, 6.5, f'{label}:', 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 6.5, val, 0, 1)

    pdf.ln(3)

    # Section 3: Signature
    pdf.set_fill_color(241, 245, 249)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(0, 57, 113)
    pdf.cell(0, 7, '  3. Signature & Authorisation', 0, 1, 'L', fill=True)
    pdf.ln(2)

    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(45, 6.5, 'Authorised Signatory:', 0, 0)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6.5, signer_name, 0, 1)

    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(45, 6.5, 'Capacity / Role:', 0, 0)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6.5, signer_role, 0, 1)

    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(45, 6.5, 'Date of signature:', 0, 0)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6.5, sign_date, 0, 1)

    pdf.ln(4)

    # Boxed Confirmation Note
    pdf.set_draw_color(203, 213, 225)
    pdf.set_fill_color(248, 250, 252)
    pdf.rect(10, pdf.get_y(), 190, 24, 'DF')
    pdf.set_xy(14, pdf.get_y() + 3)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(0, 57, 113)
    pdf.cell(0, 5, 'Filing & Processing Note:', 0, 1)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(71, 85, 105)
    pdf.set_x(14)
    pdf.multi_cell(182, 4.2, 'The change in registered office address takes effect once Companies House registers this notice. A person may validly serve any document on the company at its previous registered office address for 14 days after the date on which the change is registered.')

    return bytes(pdf.output())


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UK FormFill Pro — Form AD01</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #003971;
            --primary-light: #0284c7;
            --primary-dark: #002244;
            --slate-50: #f8fafc;
            --slate-100: #f1f5f9;
            --slate-200: #e2e8f0;
            --slate-300: #cbd5e1;
            --slate-600: #475569;
            --slate-800: #1e293b;
            --slate-900: #0f172a;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: __BODY_BG__;
            color: var(--slate-800);
            padding: __BODY_PAD__;
            line-height: 1.5;
        }
        .formfill-container {
            max-width: 780px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            __CONTAINER_STYLE__
        }
        .header-box {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 16px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--slate-200);
        }
        .header-title h2 {
            font-size: 1.25rem;
            color: var(--primary);
            font-weight: 800;
        }
        .header-title p {
            font-size: 0.85rem;
            color: var(--slate-600);
            margin-top: 2px;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            font-size: 0.75rem;
            font-weight: 700;
            border-radius: 20px;
            background: #e0f2fe;
            color: #0369a1;
        }
        .form-section {
            margin-bottom: 20px;
            background: var(--slate-50);
            border: 1px solid var(--slate-200);
            border-radius: 8px;
            padding: 16px;
        }
        .section-title {
            font-size: 0.92rem;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .form-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        label {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--slate-800);
        }
        input, select {
            padding: 8px 12px;
            font-size: 0.88rem;
            border: 1px solid var(--slate-300);
            border-radius: 6px;
            background: #ffffff;
            font-family: inherit;
            color: var(--slate-900);
            transition: border-color 0.15s;
        }
        input:focus, select:focus {
            outline: none;
            border-color: var(--primary-light);
            box-shadow: 0 0 0 3px rgba(2,132,199,0.15);
        }
        .preset-btn {
            background: #ffffff;
            border: 1px solid var(--slate-300);
            padding: 6px 12px;
            font-size: 0.78rem;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            color: var(--primary);
            transition: all 0.15s;
        }
        .preset-btn:hover {
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }
        .btn-group {
            display: flex;
            gap: 12px;
            margin-top: 24px;
        }
        .btn-primary {
            flex: 1;
            padding: 10px 18px;
            background: var(--primary);
            color: #ffffff;
            font-size: 0.92rem;
            font-weight: 700;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: background 0.15s;
        }
        .btn-primary:hover {
            background: var(--primary-dark);
        }
        .status-msg {
            margin-top: 12px;
            padding: 10px 14px;
            border-radius: 6px;
            font-size: 0.85rem;
            display: none;
        }
        .status-msg.success {
            background: #dcfce7;
            color: #166534;
            border: 1px solid #bbf7d0;
            display: block;
        }
        .status-msg.error {
            background: #fee2e2;
            color: #991b1b;
            border: 1px solid #fecaca;
            display: block;
        }
    </style>
</head>
<body>
    <div class="formfill-container">
        <div class="header-box">
            <div class="header-title">
                <h2>UK FormFill Pro — Form AD01</h2>
                <p>Companies House: Change of Registered Office Address</p>
            </div>
            <span class="badge">Official Form AD01</span>
        </div>

        <form id="form-ad01" onsubmit="handleGenerateAD01(event)">
            <!-- Company Selection / Prefill -->
            <div class="form-section">
                <div class="section-title">1. Company Information</div>
                <div class="form-grid">
                    <div class="form-group">
                        <label for="company_number">Company Number *</label>
                        <input type="text" id="company_number" name="company_number" placeholder="e.g. 12345678" required maxlength="12" value="__COMPANY_PREFILL__" oninput="lookupCrmCompany(this.value)">
                    </div>
                    <div class="form-group">
                        <label for="company_name">Company Full Legal Name *</label>
                        <input type="text" id="company_name" name="company_name" placeholder="e.g. Acme Holdings Limited" required>
                    </div>
                </div>
            </div>

            <!-- New Registered Office Address -->
            <div class="form-section">
                <div class="section-title" style="justify-content:space-between;">
                    <span>2. New Registered Office Address</span>
                    <button type="button" class="preset-btn" onclick="applyBrixenAddress()">📍 Use Brixen Central London Address</button>
                </div>
                <div class="form-grid">
                    <div class="form-group" style="grid-column: 1 / -1;">
                        <label for="address_line_1">Building Name / Number *</label>
                        <input type="text" id="address_line_1" name="address_line_1" placeholder="e.g. 100 Bishopsgate" required>
                    </div>
                    <div class="form-group">
                        <label for="address_line_2">Street</label>
                        <input type="text" id="address_line_2" name="address_line_2" placeholder="e.g. London">
                    </div>
                    <div class="form-group">
                        <label for="post_town">Post Town (City) *</label>
                        <input type="text" id="post_town" name="post_town" value="London" required>
                    </div>
                    <div class="form-group">
                        <label for="county">County / Region</label>
                        <input type="text" id="county" name="county" value="Greater London">
                    </div>
                    <div class="form-group">
                        <label for="postcode">UK Postcode *</label>
                        <input type="text" id="postcode" name="postcode" placeholder="e.g. EC2N 4AG" required>
                    </div>
                </div>
            </div>

            <!-- Signatory Details -->
            <div class="form-section">
                <div class="section-title">3. Signatory Details</div>
                <div class="form-grid">
                    <div class="form-group">
                        <label for="signer_name">Signatory Name</label>
                        <input type="text" id="signer_name" name="signer_name" placeholder="Full name of Director / Agent">
                    </div>
                    <div class="form-group">
                        <label for="signer_role">Role / Capacity</label>
                        <select id="signer_role" name="signer_role">
                            <option value="Director">Director</option>
                            <option value="Authorised Person">Authorised Person</option>
                            <option value="Company Secretary">Company Secretary</option>
                            <option value="Administrator">Administrator</option>
                        </select>
                    </div>
                </div>
            </div>

            <div id="status-box" class="status-msg"></div>

            <div class="btn-group">
                <button type="submit" class="btn-primary" id="btn-submit">
                    📄 Generate & Download AD01 PDF
                </button>
            </div>
        </form>
    </div>

    <script>
        const CRM_COMPANIES = __COMPANIES_JSON__;

        function lookupCrmCompany(val) {
            const clean = (val || '').trim().toLowerCase();
            if (!clean) return;
            const found = CRM_COMPANIES.find(function(c) {
                return (c.company_number || '').toLowerCase() === clean;
            });
            if (found) {
                document.getElementById('company_name').value = found.name || '';
            }
        }

        function applyBrixenAddress() {
            document.getElementById('address_line_1').value = '100 Bishopsgate';
            document.getElementById('address_line_2').value = 'London';
            document.getElementById('post_town').value = 'London';
            document.getElementById('county').value = 'Greater London';
            document.getElementById('postcode').value = 'EC2N 4AG';
        }

        async function handleGenerateAD01(e) {
            e.preventDefault();
            const btn = document.getElementById('btn-submit');
            const statusBox = document.getElementById('status-box');
            btn.disabled = true;
            btn.innerText = '⏳ Generating PDF...';
            statusBox.style.display = 'none';

            const payload = {
                company_number: document.getElementById('company_number').value,
                company_name: document.getElementById('company_name').value,
                address_line_1: document.getElementById('address_line_1').value,
                address_line_2: document.getElementById('address_line_2').value,
                post_town: document.getElementById('post_town').value,
                county: document.getElementById('county').value,
                postcode: document.getElementById('postcode').value,
                signer_name: document.getElementById('signer_name').value || 'Authorised Signatory',
                signer_role: document.getElementById('signer_role').value || 'Director'
            };

            try {
                const res = await fetch('/formfill/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.message || 'Failed to generate PDF');
                }

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `AD01_Form_${payload.company_number || 'UK'}.pdf`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);

                statusBox.className = 'status-msg success';
                statusBox.innerText = '✅ Form AD01 generated and downloaded successfully!';
            } catch (err) {
                statusBox.className = 'status-msg error';
                statusBox.innerText = '❌ ' + err.message;
            } finally {
                btn.disabled = false;
                btn.innerText = '📄 Generate & Download AD01 PDF';
            }
        }
    </script>
</body>
</html>
"""

def render_formfill_html(environ, form_code='AD01', embed=False):
    """
    Render modern UK FormFill Pro interactive wizard.
    """
    qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
    embed_param = qs.get('embed', ['0'])[0] in ('1', 'true', 'yes') or embed
    company_prefill = qs.get('company_number', [''])[0].strip()

    try:
        recent_companies = query_db("SELECT id, name, company_number, registered_address FROM companies ORDER BY id DESC LIMIT 50;") or []
        companies_json = json.dumps([dict(c) for c in recent_companies])
    except Exception:
        companies_json = "[]"

    body_bg = "#ffffff" if embed_param else "var(--slate-50)"
    body_pad = "16px" if embed_param else "32px 16px"
    container_style = "padding: 8px 0;" if embed_param else "box-shadow: 0 4px 20px rgba(0,0,0,0.06); border: 1px solid var(--slate-200); padding: 24px;"

    html = HTML_TEMPLATE.replace('__BODY_BG__', body_bg)
    html = html.replace('__BODY_PAD__', body_pad)
    html = html.replace('__CONTAINER_STYLE__', container_style)
    html = html.replace('__COMPANY_PREFILL__', company_prefill)
    html = html.replace('__COMPANIES_JSON__', companies_json)

    return html.encode('utf-8')


def dispatch_formfill_route(environ, start_response, path, method, user):
    """
    Handle /formfill routes for both embedded iframe and direct browser usage.
    """
    clean_path = path.rstrip('/')
    if clean_path in ('/formfill', '/formfill/ad01'):
        if method in ('GET', 'HEAD'):
            body = render_formfill_html(environ, form_code='AD01')
            start_response("200 OK", [
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(body))),
                ('X-Frame-Options', 'SAMEORIGIN'),
            ])
            return [body]

    if clean_path == '/formfill/generate' and method == 'POST':
        try:
            content_len = int(environ.get('CONTENT_LENGTH') or 0)
        except (ValueError, TypeError):
            content_len = 0
        raw_data = environ['wsgi.input'].read(content_len) if content_len > 0 else b'{}'
        try:
            payload = json.loads(raw_data.decode('utf-8'))
        except Exception:
            payload = {}

        try:
            pdf_bytes = generate_ad01_pdf(payload)
            num = str(payload.get('company_number') or 'UK').strip()
            filename = f"AD01_Form_{num}.pdf"
            start_response("200 OK", [
                ('Content-Type', 'application/pdf'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
                ('Content-Length', str(len(pdf_bytes))),
            ])
            return [pdf_bytes]
        except Exception as exc:
            err_body = json.dumps({'status': 'error', 'message': str(exc)}).encode('utf-8')
            start_response("500 Internal Server Error", [
                ('Content-Type', 'application/json'),
                ('Content-Length', str(len(err_body))),
            ])
            return [err_body]

    return None
