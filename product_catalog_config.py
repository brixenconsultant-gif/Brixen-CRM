import json

PRODUCT_FORM_CONFIGS = {
    "Business Bank Account Assistance": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Company Name",
                "type": "text",
                "placeholder": "Enter your company name",
                "required": True
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Company Registration Number",
                "type": "text",
                "placeholder": "Enter your company registration number",
                "required": True
            },
            {
                "id": "trading_proof",
                "name": "trading_proof",
                "label": "Business Trading Proof (Website or Selling Platform)",
                "type": "text",
                "placeholder": "Enter website URL or platform name",
                "help_text": "If available, otherwise we can create it for you.",
                "required": False
            },
            {
                "id": "email",
                "name": "email",
                "label": "Email Address",
                "type": "email",
                "placeholder": "Enter your email address",
                "required": True
            },
            {
                "id": "uk_contact",
                "name": "uk_contact",
                "label": "UK Contact Phone Number",
                "type": "phone",
                "placeholder": "Enter your UK contact number (+44...)",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "valid_passport",
                "name": "Valid Passport / Photo ID",
                "description": "Valid passport or government photo ID (Accepted: JPG, PNG, PDF, DOC).",
                "required": True,
                "allowed_extensions": [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
            },
            {
                "id": "bank_statement",
                "name": "Local Bank Statement (Last 3 months)",
                "description": "Recent local bank statement issued within the last 90 days.",
                "required": True,
                "allowed_extensions": [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
            }
        ]
    },
    "Tide Business Bank Details": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Company Name",
                "type": "text",
                "placeholder": "Enter your company name",
                "required": True
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Company Registration Number",
                "type": "text",
                "placeholder": "Enter your company registration number",
                "required": True
            },
            {
                "id": "trading_proof",
                "name": "trading_proof",
                "label": "Business Trading Proof (Website or Selling Platform)",
                "type": "text",
                "placeholder": "Enter website URL or platform name",
                "help_text": "If available, otherwise we can create it for you.",
                "required": False
            },
            {
                "id": "email",
                "name": "email",
                "label": "Email Address",
                "type": "email",
                "placeholder": "Enter your email address",
                "required": True
            },
            {
                "id": "uk_contact",
                "name": "uk_contact",
                "label": "UK Contact Phone Number",
                "type": "phone",
                "placeholder": "Enter your UK contact number (+44...)",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "valid_passport",
                "name": "Valid Passport / Photo ID",
                "description": "Valid passport or government photo ID.",
                "required": True,
                "allowed_extensions": [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
            },
            {
                "id": "bank_statement",
                "name": "Local Bank Statement (Last 3 months)",
                "description": "Recent local bank statement issued within the last 90 days.",
                "required": True,
                "allowed_extensions": [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
            }
        ]
    },
    "Company Formation Package": {
        "fields": [
            {
                "id": "proposed_company_name",
                "name": "proposed_company_name",
                "label": "Proposed Company Name",
                "type": "text",
                "placeholder": "e.g. BRIXEN ENTERPRISES LIMITED",
                "help_text": "Must end with LTD or LIMITED.",
                "required": True
            },
            {
                "id": "company_type",
                "name": "company_type",
                "label": "Company Structure / Type",
                "type": "dropdown",
                "options": [
                    "Private Limited Company by Shares (LTD)",
                    "Limited Liability Partnership (LLP)",
                    "Public Limited Company (PLC)",
                    "Company Limited by Guarantee",
                    "Sole Trader / Partnership"
                ],
                "default_value": "Private Limited Company by Shares (LTD)",
                "required": True
            },
            {
                "id": "sic_code",
                "name": "sic_code",
                "label": "SIC Code / Business Activity",
                "type": "dropdown",
                "options": [
                    "62020 - Information technology consultancy activities",
                    "70229 - Management consultancy activities",
                    "68209 - Letting and operating of real estate",
                    "47910 - E-Commerce / Online Retail",
                    "82990 - General Business Support Services",
                    "Other Business Activity"
                ],
                "default_value": "62020 - Information technology consultancy activities",
                "required": True
            },
            {
                "id": "has_existing_company",
                "name": "has_existing_company",
                "label": "Do you already have a registered company number?",
                "type": "radio",
                "options": ["NO", "YES"],
                "default_value": "NO",
                "required": False
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Companies House Company Number",
                "type": "text",
                "placeholder": "e.g. 12345678",
                "help_text": "Required if company is already registered.",
                "depends_on": "has_existing_company",
                "condition": "equals",
                "condition_value": "YES",
                "required": False
            },
            {
                "id": "registered_office_address",
                "name": "registered_office_address",
                "label": "Registered Office Address",
                "type": "address",
                "placeholder": "Address Line 1, City, Postcode, Country",
                "help_text": "Address for Companies House public record.",
                "required": True
            }
        ],
        "repeatable_sections": [
            {
                "id": "directors",
                "title": "Directors Information",
                "button_label": "Add Director",
                "min_entries": 1,
                "fields": [
                    {"id": "full_name", "label": "Director Full Name", "type": "text", "required": True},
                    {"id": "email", "label": "Email Address", "type": "email", "required": True},
                    {"id": "phone", "label": "Phone Number", "type": "phone", "required": False},
                    {"id": "dob", "label": "Date of Birth", "type": "date", "required": True},
                    {"id": "nationality", "label": "Nationality", "type": "text", "default_value": "British", "required": True},
                    {"id": "country_of_residence", "label": "Country of Residence", "type": "country", "default_value": "United Kingdom", "required": True},
                    {"id": "residential_address", "label": "Residential Address", "type": "text", "required": True}
                ]
            },
            {
                "id": "shareholders",
                "title": "Shareholders & Capital Allocation",
                "button_label": "Add Shareholder",
                "min_entries": 1,
                "fields": [
                    {"id": "full_name", "label": "Shareholder Name", "type": "text", "required": True},
                    {"id": "number_of_shares", "label": "Number of Shares", "type": "number", "default_value": "100", "required": True},
                    {"id": "share_class", "label": "Share Class", "type": "text", "default_value": "Ordinary £1.00", "required": True},
                    {"id": "currency", "label": "Currency", "type": "dropdown", "options": ["GBP (£)", "USD ($)", "EUR (€)"], "default_value": "GBP (£)", "required": True}
                ]
            }
        ],
        "document_requirements": [
            {
                "id": "director_id",
                "name": "Director Passport / Photo ID",
                "description": "Valid passport or driving licence for identity verification.",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png"]
            },
            {
                "id": "proof_of_address",
                "name": "Proof of Address",
                "description": "Utility bill or bank statement issued within the last 3 months.",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png"]
            },
            {
                "id": "supporting_docs",
                "name": "Additional Supporting Documents",
                "description": "Any additional documents or Articles of Association customization.",
                "required": False,
                "allowed_extensions": [".pdf", ".docx", ".zip"]
            }
        ]
    },
    "Registered Office Address": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Registered Company Name",
                "type": "text",
                "placeholder": "e.g. ACME HOLDINGS LTD",
                "required": True
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Companies House Company Number",
                "type": "text",
                "placeholder": "e.g. 09876543",
                "required": True
            },
            {
                "id": "contact_person",
                "name": "contact_person",
                "label": "Primary Contact Person",
                "type": "text",
                "placeholder": "Full Name",
                "required": True
            },
            {
                "id": "forwarding_email",
                "name": "forwarding_email",
                "label": "Official Notification & Scan Forwarding Email",
                "type": "email",
                "placeholder": "contact@company.co.uk",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "proof_of_id",
                "name": "Passport or ID Card",
                "description": "Government issued photo ID of director/owner.",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".png"]
            },
            {
                "id": "proof_of_address",
                "name": "Proof of Residential Address",
                "description": "Recent bank statement or utility bill (<3 months old).",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".png"]
            }
        ]
    },
    "Mail Forwarding Service": {
        "fields": [
            {
                "id": "business_name",
                "name": "business_name",
                "label": "Business / Trading Name",
                "type": "text",
                "required": True
            },
            {
                "id": "forwarding_frequency",
                "name": "forwarding_frequency",
                "label": "Scan & Mail Forwarding Frequency",
                "type": "dropdown",
                "options": ["Daily Digital Scan", "Weekly Mail Forwarding", "Monthly Digest"],
                "default_value": "Daily Digital Scan",
                "required": True
            },
            {
                "id": "forwarding_physical_address",
                "name": "forwarding_physical_address",
                "label": "Physical Mail Delivery Address",
                "type": "address",
                "placeholder": "Address Line 1, City, Postcode, Country",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "proof_of_id",
                "name": "Photo ID Verification",
                "description": "Passport or Driving Licence.",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".png"]
            }
        ]
    },
    "Annual Compliance Filing": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Company Registered Name",
                "type": "text",
                "required": True
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Companies House Company Number",
                "type": "text",
                "required": True
            },
            {
                "id": "cs_period",
                "name": "cs_period",
                "label": "Confirmation Statement Due Date / Period",
                "type": "date",
                "required": True
            },
            {
                "id": "has_capital_changes",
                "name": "has_capital_changes",
                "label": "Any changes to officers, share capital, or PSCs in the last 12 months?",
                "type": "radio",
                "options": ["NO", "YES"],
                "default_value": "NO",
                "required": True
            },
            {
                "id": "changes_details",
                "name": "changes_details",
                "label": "Details of Changes to Report",
                "type": "textarea",
                "placeholder": "Describe any director, address, or shareholder changes...",
                "depends_on": "has_capital_changes",
                "condition": "equals",
                "condition_value": "YES",
                "required": False
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "cs_supporting_doc",
                "name": "Previous Confirmation Statement / Accounts",
                "description": "Optional prior year filing copy.",
                "required": False,
                "allowed_extensions": [".pdf"]
            }
        ]
    },
    "VAT Registration Service": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Company Name",
                "type": "text",
                "required": True
            },
            {
                "id": "company_number",
                "name": "company_number",
                "label": "Company Registration Number",
                "type": "text",
                "required": True
            },
            {
                "id": "estimated_turnover",
                "name": "estimated_turnover",
                "label": "Estimated 12-Month Turnover (£)",
                "type": "number",
                "placeholder": "e.g. 90000",
                "required": True
            },
            {
                "id": "business_bank_details",
                "name": "business_bank_details",
                "label": "Business Bank Account Name & Sort Code",
                "type": "text",
                "placeholder": "Bank Name, Sort Code: XX-XX-XX, Account: XXXXXXXX",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "cert_inc",
                "name": "Certificate of Incorporation",
                "description": "Official Companies House incorporation certificate.",
                "required": True,
                "allowed_extensions": [".pdf"]
            },
            {
                "id": "bank_statement_header",
                "name": "Business Bank Statement Header",
                "description": "Recent bank statement showing company name and account number.",
                "required": True,
                "allowed_extensions": [".pdf", ".jpg", ".png"]
            }
        ]
    }
}

def get_product_form_config(product_name):
    """Return product configuration dict for a given product name matching brixenconsultants.com."""
    if product_name in PRODUCT_FORM_CONFIGS:
        return PRODUCT_FORM_CONFIGS[product_name]
    
    pname_lower = str(product_name or '').lower()
    for k, config in PRODUCT_FORM_CONFIGS.items():
        if k.lower() in pname_lower or pname_lower in k.lower():
            return config
            
    if 'bank' in pname_lower or 'tide' in pname_lower:
        return PRODUCT_FORM_CONFIGS["Business Bank Account Assistance"]

    return {
        "fields": [
            {
                "id": "service_notes",
                "name": "service_notes",
                "label": f"Instructions & Specifications for {product_name}",
                "type": "textarea",
                "placeholder": "Enter any specific requirements or details for this service...",
                "required": True
            },
            {
                "id": "contact_phone",
                "name": "contact_phone",
                "label": "Primary Contact Telephone",
                "type": "phone",
                "placeholder": "+44 20 7946 0912",
                "required": False
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "supporting_doc",
                "name": "Supporting Document / Brief",
                "description": "Upload any project files, documents, or specification briefs.",
                "required": False,
                "allowed_extensions": [".pdf", ".doc", ".docx", ".zip", ".jpg", ".png"]
            }
        ]
    }
