#!/usr/bin/env python3
"""Mirror of the Accountancy Check classifier. Run before compile."""
import re
import sys

ECOMMERCE_SALES_RE = re.compile(
    r"\b(stripe|sumup|square|shopify|woocommerce|woo\s*commerce|paypal|pay\s*pal|"
    r"etsy|klarna|clearpay|afterpay|gocardless|worldpay|adyen|braintree|"
    r"amazon|amzn|ebay|tiktok\s*shop|bigcommerce|wix|squarespace|ecwid|"
    r"magento|prestashop|marketplace\s*payout|platform\s*payout)\b",
    re.I,
)
MARKETPLACE_RE = re.compile(r"\b(amazon|amzn|ebay|etsy|tiktok\s*shop)\b", re.I)
AMAZON_PRIME_RE = re.compile(r"\bamazon\s+prime\b", re.I)
PRIOR_TX_RE = re.compile(r"relates to a previous", re.I)
DIVIDEND_RE = re.compile(r"\b(dividends?|divi\b|interim\s+div|final\s+div)\b", re.I)
DIRECTOR_LOAN_RE = re.compile(r"\b(directors?\s+loans?|loan\s+account|\bdla\b|drawings?|directors?\s+advances?)\b", re.I)
SALARY_RE = re.compile(r"\b(salary|salaries|wages|payroll|paye|n\.?i\.?c\.?|national insurance|remuneration|director(?:'s|s)?\s+pay|director(?:'s|s)?\s+salary)\b", re.I)
DIRECTOR_WORD_RE = re.compile(r"\bdirectors?\b", re.I)
BANK_FEE_RE = re.compile(r"\b(bank charge|monthly (?:account )?fee|starling.*fee|tide fee|monzo plus|monzo.*fee|paid membership)\b", re.I)
HMRC_VAT_RE = re.compile(r"\b(hmrc vat|vat payment|vat return)\b", re.I)

DIRECTORS = ["suleman iqbal"]


def money_dir(row):
    incoming = float(row.get("money_in") or 0)
    outgoing = float(row.get("money_out") or 0)
    if incoming > 0:
        return "in"
    if outgoing > 0:
        return "out"
    return ""


def mentions_director(desc):
    text = re.sub(r"[^a-z0-9\s'-]", " ", desc.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return any(name in text for name in DIRECTORS)


def classify(row):
    desc = row.get("description") or ""
    direction = money_dir(row)
    if PRIOR_TX_RE.search(desc) and MARKETPLACE_RE.search(desc) and direction == "in":
        return "5000", "high"
    if direction == "in" and ECOMMERCE_SALES_RE.search(desc):
        return "4000", "high"
    if direction == "out" and AMAZON_PRIME_RE.search(desc):
        return "7500", "medium"
    if direction == "out" and MARKETPLACE_RE.search(desc):
        return "5000", "high"
    if direction == "out" and mentions_director(desc) and DIVIDEND_RE.search(desc):
        return "3200", "high"
    if direction == "out" and mentions_director(desc) and not DIRECTOR_LOAN_RE.search(desc):
        return "7002", "high"
    if direction == "out" and HMRC_VAT_RE.search(desc):
        return "2200", "high"
    if direction == "out" and BANK_FEE_RE.search(desc):
        return "7700", "high"
    return "", "none"


CASES = [
    ({"description": "EBAY Commerce UK Ltd (Faster Payments)", "money_in": 35}, "4000", "high"),
    ({"description": "AMAZON.UK LONDON GBR", "money_out": 2.87}, "5000", "high"),
    ({"description": "SULEMAN IQBAL (Faster Payments)", "money_out": 55}, "7002", "high"),
    ({"description": "MUHAMMAD SADAM (Faster Payments)", "money_out": 35}, "", "none"),
    ({"description": "AMAZON UK* ZL4R31KK4 Principal Place", "money_in": 20.25}, "4000", "high"),
    ({"description": "AMAZON.UK LONDON GBR THIS RELATES TO A PREVIOUS TRANSACTION", "money_in": 5.4}, "5000", "high"),
    ({"description": "Stripe Payout", "money_in": 120}, "4000", "high"),
    ({"description": "Interim dividend Suleman Iqbal", "money_out": 200}, "3200", "high"),
    ({"description": "HMRC VAT", "money_out": 80}, "2200", "high"),
    ({"description": "Starling monthly fee", "money_out": 2}, "7700", "high"),
    ({"description": "AMAZON PRIME LONDON GBR", "money_out": 8.99}, "7500", "medium"),
]


def main():
    failed = []
    for row, code, conf in CASES:
        got_code, got_conf = classify(row)
        if got_code != code or got_conf != conf:
            failed.append(f"{row['description']}: {got_code}/{got_conf} != {code}/{conf}")
    if failed:
        print("FAIL")
        for item in failed:
            print(" -", item)
        return 1
    print(f"ok {len(CASES)} classifier tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
