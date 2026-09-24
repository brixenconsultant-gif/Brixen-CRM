#!/usr/bin/env python3
"""QA for Accountancy: classifier, CH months, statement-open July, Check chrome."""
import os
import sys
from pathlib import Path

ROOT = Path("/Users/simple/.gemini/antigravity/scratch/hypetex-portal")
JS = (ROOT / "static/js/accounts-file.js").read_text()
CSS = (ROOT / "static/css/styles.css").read_text()
HTML = (ROOT / "templates/index.html").read_text()

failures = []


def ok(cond, msg):
    if not cond:
        failures.append(msg)


def main():
    bal = 0
    for ch in JS:
        bal += 1 if ch == "{" else -1 if ch == "}" else 0
        if bal < 0:
            failures.append("JS brace underflow")
            break
    ok(bal == 0, f"JS brace balance {bal}")

    for token in (
        "classifyTransaction",
        "runClassifierSelfTest",
        "checkWorkflow",
        "needs-you",
        "bankUploadRequiredLine",
        "Please Upload Bank Statement",
        "accounts-select-all",
        "statementFileSpan",
        "On statement",
        "inCompileWindow",
        "clampBooksToFilingPeriod",
        "loadCompaniesHousePeriod",
        "accuracyReportCard",
        "isRunnerConfirmed",
        "markLinesReviewed",
    ):
        ok(token in JS, f"missing {token}")

    ok("accounts-select-all" in JS, "select-all checkbox missing")
    ok("data-line-select" in JS, "row checkboxes missing")
    ok(".accounts-check-cell input[type=\"checkbox\"]" in CSS, "checkbox size CSS missing")
    ok("accounts-file.js?v=3254.0" in HTML, "cache not 3254")
    ok("function filingYearCard" in JS, "File must explain the CH first-accounts form")
    ok("firstStillWanted" in JS, "overdue first accounts must win over a later bank statement")
    ok("/companies-house" in JS, "Accountancy must load the live Companies House filing window")
    ok("accounts_period_start" in JS, "File must use CH next_accounts.period_start_on")
    APP = (ROOT / "app.py").read_text()
    ok("unpaidCapital" in JS, "dormant first-year form must show CH share capital, not blank boxes")
    ok("fetch_companies_house_share_capital" in APP, "backend must read SH01/NEWINC statement of capital")
    ok("def live_companies_house_filing" in APP, "backend must fetch CH accounts window live")
    ok("accounts_made_up_to" in APP and "period_end_on" in APP, "CH profile must keep next_accounts.period_end_on")
    ok("function lockBooksToCompaniesHouseYear" in JS, "File must lock books to the CH year")
    ok("emptyFirstYear" in JS, "blank first-year CH form must zero the balance sheet")
    ok("Checking categories" not in JS, "compile must not stall on category reconcile")
    ok("Opening balance" not in JS, "opening balance field must be gone")
    ok("Stay on Start and choose what this company did this year." in JS, "company select must not skip Start")
    ok("function runPool" in JS, "category changes must batch in parallel")
    ok(JS.count("${statementMonthsCard(data)}") == 1, "months card only on Bank")
    ok('id="accounts-pick-many"' not in JS, "no separate Multiple bank statements button")
    ok("multiple required" in JS, "Choose Files must still allow several statements")
    ok(">Compile</button>" in JS, "compile button must be named Compile")
    ok("function setCompileProgress" in JS, "compile must show a percent")
    ok(".accounts-compile-fill" in CSS, "compile progress bar missing")
    ok(".accounts-apple-btn:hover" in CSS, "Compile button needs hover")
    ok("function homeScreen(data) {\n        return pathChooser();" in JS, "Start must stay the path chooser")
    ok("isRunnerConfirmed(row.line)" in JS, "Needs you must drop runner-confirmed lines")
    ok('multiple required' in JS, "Bank Choose Files must allow multiple statements")
    ok("function compileButton" in JS, "Test then compile lives on Bank")
    ok("state.checkStep = 0;" in JS, "compile must move to the first Check step")
    ok('data-accountancy-pane="hmrc"' in HTML, "HMRC tab present")
    ok(HTML.find('data-accountancy-pane="filing"') < HTML.find('data-accountancy-pane="accounts"') < HTML.find('data-accountancy-pane="hmrc"'), "tab order Filing status / Company Accounts / HMRC")
    ok("Company Accounts" in HTML, "Accounts tab labelled Company Accounts")
    ok("addEventListener('beforeunload', onAccountsBeforeUnload)" in JS, "warn before refresh when accounts data exists")
    ok("function clearOpenBooksOnReload" in JS, "reload must open a clean Accountancy view")
    ok('ch-bs-why' not in JS, "long CH form tagline must be gone")
    ok(JS.count("accountantWorkingNote(data)") == 2, "accountant notes only on File (def + one call)")
    ok("{ id: 'hmrc', n: '5', label: 'HMRC' }" not in JS, "HMRC must leave the Accounts stepper")

    # July 2025 must not be bank-not-open when statement starts 2025-07-01
    ok("openedOn && month.key < openedOn" in JS, "bank-not-open must use statement start, not first txn")
    ok("On statement" in JS, "months on the PDF with no lines should say On statement")

    classify_rc = os.system(f"{sys.executable} {ROOT / 'tests' / 'test_accounts_classify.py'}")
    ok(classify_rc == 0, "classifier self-test failed")

    if failures:
        print("FAIL")
        for item in failures:
            print(" -", item)
        return 1
    print("ok accountancy QA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
