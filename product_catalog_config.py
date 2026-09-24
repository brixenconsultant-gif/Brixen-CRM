import json

STANDARD_ORDER_DOCUMENT_REQUIREMENTS = [
    {
        "id": "id_document",
        "name": "ID Document (Passport / Driving Licence / Photo ID)",
        "description": "Valid passport, national ID, or driving licence. Accepted: PDF, JPG, PNG, DOC, DOCX.",
        "required": True,
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"],
    },
    {
        "id": "proof_of_address",
        "name": "Proof of Address (Utility bill / Bank statement)",
        "description": "Proof of residential or business address issued within the last 3 months.",
        "required": True,
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"],
    },
    {
        "id": "additional_documents",
        "name": "Additional Documents",
        "description": "Optional extra supporting files for this service (certificates, forms, briefs).",
        "required": False,
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".zip"],
    },
]


FORM_PROFILE_FORMATION = "formation"
FORM_PROFILE_EXISTING_COMPANY = "existing_company"
FORM_PROFILE_WEBSITE = "website"
FORM_PROFILE_BANK = "bank"
FORM_PROFILE_PERSONAL_BANK = "personal_bank"
FORM_PROFILE_IDENTITY = "identity"
FORM_PROFILE_COMPLIANCE = "compliance"
FORM_PROFILE_COMMS = "comms"
FORM_PROFILE_GENERIC = "generic"

WEBSITE_NAME_HINTS = (
    "website", "web design", "webdesign", "business email",
    "email and website", "domain", "logo design",
)
FORMATION_NAME_HINTS = (
    "digital package", "professional package", "all inclusive",
    "company formation", "incorporat", "register a company",
    "ltd formation", "new company",
)
BANK_NAME_HINTS = ("bank", "tide", "zempler", "wise business", "monzo", "ifast", "countingup", "transwap")
PERSONAL_BANK_HINTS = ("personal physical bank", "personal bank")
IDENTITY_NAME_HINTS = ("identity verification", "kyc", "companies house id")
COMPLIANCE_NAME_HINTS = (
    "confirmation statement", "annual compliance", "vat registration",
    "dissolution", "name change", "director", "dormant",
)
EXISTING_COMPANY_HINTS = (
    "registered office", "mail forwarding", "mail handling",
    "virtual office", "shared office", "registered agent",
    "ad01",
)
COMMS_NAME_HINTS = ("virtual number", "call answering", "phone answering")
NO_REQUIRED_DOC_HINTS = (
    "ad01",
    "registered office address",
    "change of registered office",
    "change registered office",
)


def _text(product_name, category=""):
    return f"{product_name or ''} {category or ''}".lower()


def _name_has(text, hints):
    return any(hint in text for hint in hints)


def get_product_form_profile(product_name, category=""):
    """Decide which Details form a catalogue product should use."""
    text = _text(product_name, category)
    name = str(product_name or "").lower()
    if not text.strip():
        return FORM_PROFILE_GENERIC

    is_formation = (
        _name_has(name, FORMATION_NAME_HINTS)
        or ("package" in name and not _name_has(name, WEBSITE_NAME_HINTS))
    )
    if is_formation and not (_name_has(name, IDENTITY_NAME_HINTS) and "package" not in name):
        return FORM_PROFILE_FORMATION

    if _name_has(text, WEBSITE_NAME_HINTS):
        return FORM_PROFILE_WEBSITE
    if _name_has(text, PERSONAL_BANK_HINTS):
        return FORM_PROFILE_PERSONAL_BANK
    if _name_has(text, BANK_NAME_HINTS):
        return FORM_PROFILE_BANK
    if _name_has(text, IDENTITY_NAME_HINTS):
        return FORM_PROFILE_IDENTITY
    if _name_has(text, COMPLIANCE_NAME_HINTS):
        return FORM_PROFILE_COMPLIANCE
    if _name_has(text, EXISTING_COMPANY_HINTS):
        return FORM_PROFILE_EXISTING_COMPANY
    if _name_has(text, COMMS_NAME_HINTS):
        return FORM_PROFILE_COMMS
    return FORM_PROFILE_GENERIC


def service_skips_required_docs(product_name, category=""):
    return _name_has(_text(product_name, category), NO_REQUIRED_DOC_HINTS)


def service_needs_standard_kyc_docs(product_name, category=""):
    if service_skips_required_docs(product_name, category):
        return False
    profile = get_product_form_profile(product_name, category)
    if profile in (FORM_PROFILE_WEBSITE, FORM_PROFILE_COMMS, FORM_PROFILE_GENERIC, FORM_PROFILE_PERSONAL_BANK):
        return False
    if profile == FORM_PROFILE_COMPLIANCE:
        text = _text(product_name, category)
        if "vat" in text or "confirmation statement" in text or "annual compliance" in text:
            return False
        return True
    return profile in (
        FORM_PROFILE_FORMATION,
        FORM_PROFILE_EXISTING_COMPANY,
        FORM_PROFILE_BANK,
        FORM_PROFILE_IDENTITY,
    )


def service_skips_director_dob(product_name, category=""):
    text = _text(product_name, category)
    return "confirmation statement" in text or "annual compliance" in text


def service_is_sole_trader(product_name, category=""):
    return "sole trad" in _text(product_name, category)


def service_is_personal_bank(product_name, category=""):
    return get_product_form_profile(product_name, category) == FORM_PROFILE_PERSONAL_BANK


def service_needs_access_credentials(product_name, category=""):
    return get_product_form_profile(product_name, category) == FORM_PROFILE_BANK


def service_needs_companies_house(product_name, category=""):
    if service_is_sole_trader(product_name, category) or service_is_personal_bank(product_name, category):
        return False
    return get_product_form_profile(product_name, category) in (
        FORM_PROFILE_FORMATION,
        FORM_PROFILE_EXISTING_COMPANY,
        FORM_PROFILE_BANK,
        FORM_PROFILE_IDENTITY,
        FORM_PROFILE_COMPLIANCE,
    )


def missing_required_order_documents(uploaded_docs):
    docs = uploaded_docs if isinstance(uploaded_docs, dict) else {}
    missing = []
    for req in STANDARD_ORDER_DOCUMENT_REQUIREMENTS:
        if not req.get("required", True):
            continue
        item = docs.get(req["id"])
        items = item if isinstance(item, list) else ([item] if item else [])
        has_file = any(
            isinstance(x, dict) and (x.get("document_id") or x.get("id") or x.get("file_name"))
            for x in items
        )
        if not has_file:
            missing.append(req["name"])
    return missing


PRODUCT_FORM_CONFIGS = {
    "Personal Physical Bank": {
        "fields": [
            {
                "id": "full_name",
                "name": "full_name",
                "label": "Full name",
                "type": "text",
                "placeholder": "As it appears on their personal ID",
                "required": True
            },
            {
                "id": "dob",
                "name": "dob",
                "label": "Date of birth",
                "type": "date",
                "required": True
            },
            {
                "id": "home_address",
                "name": "home_address",
                "label": "Personal address",
                "type": "text",
                "placeholder": "Home address, city, postcode",
                "required": True
            },
            {
                "id": "email",
                "name": "email",
                "label": "Email address",
                "type": "email",
                "placeholder": "Personal email",
                "required": True
            },
            {
                "id": "phone",
                "name": "phone",
                "label": "Phone number",
                "type": "phone",
                "placeholder": "+44 ...",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": []
    },
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
        "document_requirements": list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
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
        "document_requirements": list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
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
                    {"id": "nationality", "label": "Nationality", "type": "text", "placeholder": "e.g. British / Pakistani", "required": True},
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
        "document_requirements": list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
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
        "document_requirements": []
    },
    "AD01 Form": {
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
                "id": "new_registered_office_address",
                "name": "new_registered_office_address",
                "label": "New registered office address",
                "type": "address",
                "placeholder": "Address Line 1, City, Postcode, Country",
                "required": True
            },
            {
                "id": "director_name",
                "name": "director_name",
                "label": "Director / Signatory Name",
                "type": "text",
                "placeholder": "Full legal name",
                "required": True
            },
            {
                "id": "forwarding_email",
                "name": "forwarding_email",
                "label": "Notification email",
                "type": "email",
                "placeholder": "contact@company.co.uk",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": []
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
        "document_requirements": list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
    },
    "Identity Verification": {
        "fields": [
            {
                "id": "full_name",
                "name": "full_name",
                "label": "Person to Verify",
                "type": "text",
                "placeholder": "Full legal name",
                "required": True
            },
            {
                "id": "dob",
                "name": "dob",
                "label": "Date of Birth",
                "type": "date",
                "required": True
            },
            {
                "id": "nationality",
                "name": "nationality",
                "label": "Nationality",
                "type": "text",
                "placeholder": "e.g. British / Pakistani",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
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
    },
    "Business Website": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Client company name",
                "type": "text",
                "placeholder": "e.g. Northstar Digital Ltd",
                "required": True
            },
            {
                "id": "domain_name",
                "name": "domain_name",
                "label": "Desired domain",
                "type": "text",
                "placeholder": "e.g. yourcompany.co.uk",
                "help_text": "The domain they want for the website or business email.",
                "required": True
            },
            {
                "id": "access_email",
                "name": "access_email",
                "label": "Email address",
                "type": "email",
                "placeholder": "e.g. info@yourcompany.co.uk",
                "required": True
            },
            {
                "id": "access_email_password",
                "name": "access_email_password",
                "label": "Email password",
                "type": "text",
                "placeholder": "Mailbox password / access code",
                "required": True
            },
            {
                "id": "logo",
                "name": "logo",
                "label": "Company logo",
                "type": "file",
                "help_text": "Optional. Upload a logo if the client already has one — we can work without it.",
                "required": False
            }
        ],
        "repeatable_sections": [],
        "document_requirements": [
            {
                "id": "logo",
                "name": "Company Logo",
                "description": "Optional brand logo (PNG, JPG, SVG, WEBP, PDF).",
                "required": False,
                "allowed_extensions": [".png", ".jpg", ".jpeg", ".svg", ".webp", ".pdf"]
            }
        ]
    },
    "Virtual Numbers": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Client company name",
                "type": "text",
                "required": True
            },
            {
                "id": "preferred_area_code",
                "name": "preferred_area_code",
                "label": "Preferred area / number type",
                "type": "text",
                "placeholder": "e.g. 020 London, 0333, 0800",
                "required": True
            },
            {
                "id": "forwarding_number",
                "name": "forwarding_number",
                "label": "Forward calls to",
                "type": "phone",
                "placeholder": "+44 ...",
                "required": True
            }
        ],
        "repeatable_sections": [],
        "document_requirements": []
    },
    "24/7 Call Answering": {
        "fields": [
            {
                "id": "company_name",
                "name": "company_name",
                "label": "Client company name",
                "type": "text",
                "required": True
            },
            {
                "id": "greeting_script",
                "name": "greeting_script",
                "label": "How should we answer the phone?",
                "type": "textarea",
                "placeholder": "e.g. Good morning, Northstar Digital, how can I help?",
                "required": True
            },
            {
                "id": "forwarding_number",
                "name": "forwarding_number",
                "label": "Urgent calls / WhatsApp number",
                "type": "phone",
                "required": False
            }
        ],
        "repeatable_sections": [],
        "document_requirements": []
    }
}

def get_product_form_config(product_name, category=""):
    """Return product configuration dict for a given product name matching brixenconsultants.com."""
    profile = get_product_form_profile(product_name, category)
    pname_lower = str(product_name or "").lower()

    if product_name in PRODUCT_FORM_CONFIGS:
        config = dict(PRODUCT_FORM_CONFIGS[product_name])
    else:
        config = None
        for k, candidate in PRODUCT_FORM_CONFIGS.items():
            if k.lower() in pname_lower or pname_lower in k.lower():
                config = dict(candidate)
                break
        if config is None and profile == FORM_PROFILE_WEBSITE:
            config = dict(PRODUCT_FORM_CONFIGS["Business Website"])
        if config is None and profile == FORM_PROFILE_PERSONAL_BANK:
            config = dict(PRODUCT_FORM_CONFIGS["Personal Physical Bank"])
        if config is None and profile == FORM_PROFILE_BANK:
            config = dict(PRODUCT_FORM_CONFIGS["Business Bank Account Assistance"])
        if config is None and profile == FORM_PROFILE_IDENTITY:
            config = dict(PRODUCT_FORM_CONFIGS["Identity Verification"])
        if config is None and profile == FORM_PROFILE_COMPLIANCE and "vat" in pname_lower:
            config = dict(PRODUCT_FORM_CONFIGS["VAT Registration Service"])
        if config is None and profile == FORM_PROFILE_COMPLIANCE:
            config = dict(PRODUCT_FORM_CONFIGS["Annual Compliance Filing"])
        if config is None and profile == FORM_PROFILE_EXISTING_COMPANY and "mail" in pname_lower:
            config = dict(PRODUCT_FORM_CONFIGS["Mail Forwarding Service"])
        if config is None and ("ad01" in pname_lower or "change of registered office" in pname_lower):
            config = dict(PRODUCT_FORM_CONFIGS["AD01 Form"])
        if config is None and profile == FORM_PROFILE_EXISTING_COMPANY:
            config = dict(PRODUCT_FORM_CONFIGS["Registered Office Address"])
        if config is None and profile == FORM_PROFILE_COMMS and "number" in pname_lower:
            config = dict(PRODUCT_FORM_CONFIGS["Virtual Numbers"])
        if config is None and profile == FORM_PROFILE_COMMS:
            config = dict(PRODUCT_FORM_CONFIGS["24/7 Call Answering"])
        if config is None and profile == FORM_PROFILE_FORMATION:
            config = dict(PRODUCT_FORM_CONFIGS["Company Formation Package"])
        if config is None:
            config = {
                "fields": [
                    {
                        "id": "company_name",
                        "name": "company_name",
                        "label": "Client company name",
                        "type": "text",
                        "placeholder": "Company or trading name",
                        "required": True
                    },
                    {
                        "id": "service_notes",
                        "name": "service_notes",
                        "label": f"Instructions for {product_name or 'this service'}",
                        "type": "textarea",
                        "placeholder": "Anything the fulfilment team needs to complete this order...",
                        "required": False
                    }
                ],
                "repeatable_sections": [],
                "document_requirements": [],
            }

    config["form_profile"] = profile
    if service_skips_required_docs(product_name, category):
        config["document_requirements"] = []
    elif service_needs_standard_kyc_docs(product_name, category):
        config["document_requirements"] = list(STANDARD_ORDER_DOCUMENT_REQUIREMENTS)
    elif profile == FORM_PROFILE_WEBSITE:
        config["document_requirements"] = list(PRODUCT_FORM_CONFIGS["Business Website"]["document_requirements"])
    return config
