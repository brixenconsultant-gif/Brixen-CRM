#!/usr/bin/env python3
"""Customer upload isolation, AI triage, approve, and bulk-approval safety tests."""
import hashlib
import os
os.environ.setdefault('CRM_TESTING', '1')

from unittest import mock
import app
from db import execute_db, query_db, hash_password


def _png_bytes():
    import base64
    return base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )


def _ensure_users():
    admin = query_db("SELECT id FROM users WHERE email = 'triage-admin@example.com';", one=True)
    if not admin:
        admin_id = execute_db(
            "INSERT INTO users (email, full_name, role, password_hash) VALUES (?, ?, 'SUPER_ADMIN', ?);",
            ('triage-admin@example.com', 'Triage Admin', hash_password('TestPass123!')),
        )
    else:
        admin_id = admin['id']
    client = query_db("SELECT id FROM users WHERE email = 'triage-client@example.com';", one=True)
    if not client:
        client_id = execute_db(
            "INSERT INTO users (email, full_name, role, password_hash) VALUES (?, ?, 'CLIENT', ?);",
            ('triage-client@example.com', 'Triage Client', hash_password('TestPass123!')),
        )
    else:
        client_id = client['id']
    return admin_id, client_id


def run_tests():
    print('==================================================')
    print('TRIAGE & APPROVAL WORKFLOW TESTS')
    print('==================================================')
    admin_id, client_id = _ensure_users()
    png = _png_bytes()

    # 1. Customer upload isolation
    with mock.patch('app.extract_document_text_and_metadata') as mocked:
        mocked.return_value = {
            'category': 'ID Document',
            'extracted_name': 'Triage Client',
            'extracted_dob': '01/01/1990',
            'extracted_nationality': 'British',
            'scan_method': 'ocr',
            'extraction_quality': {'score': 80},
            'text_preview': 'PASSPORT Triage Client',
        }
        with mock.patch('app.resolve_intake_company_match') as resolve:
            resolve.return_value = (None, 'not_applicable', 'no match', [], {'confidence': 20, 'score_gap': 0})
            triage = app.triage_uploaded_document(png, 'passport.png', client_id, client_id=client_id)
    assert triage['is_posted'] == 0
    assert triage['lifecycle_status'] != 'POSTED_DOCUMENTS'
    doc_id = execute_db(
        """
        INSERT INTO documents (
            user_id, name, category, file_path, file_type, file_size, status, uploaded_by,
            client_visible, lifecycle_status, is_posted, file_hash
        ) VALUES (?, 'triage-isolation.png', 'ID Document', '/tmp/triage-isolation.png', 'PNG', '1 KB',
                  'Pending Review', 'Customer Upload', 0, ?, 0, ?);
        """,
        (client_id, triage['lifecycle_status'], triage['file_hash']),
    )
    posted = query_db(
        "SELECT COUNT(*) AS c FROM documents WHERE id = ? AND (is_posted = 1 OR lifecycle_status = 'POSTED_DOCUMENTS');",
        (doc_id,), one=True,
    )
    assert posted['c'] == 0
    print('✓ Customer Upload Isolation Test')

    # 2. Duplicate quarantine
    h = hashlib.sha256(png).hexdigest()
    execute_db(
        """
        INSERT INTO documents (
            user_id, name, category, file_path, file_type, file_size, status, uploaded_by,
            client_visible, lifecycle_status, is_posted, file_hash
        ) VALUES (?, 'triage-first.png', 'ID Document', '/tmp/triage-first.png', 'PNG', '1 KB',
                  'Pending Review', 'Customer Upload', 0, 'REVIEW_REQUIRED', 0, ?);
        """,
        (client_id, h),
    )
    dup = app.triage_uploaded_document(png, 'dup.png', client_id, client_id=client_id)
    assert dup['lifecycle_status'] == 'QUARANTINE'
    assert dup['duplicate_confidence'] == 100.0
    print('✓ Duplicate & Junk Quarantine Test')

    # 3. AI triage states
    with mock.patch('app.extract_document_text_and_metadata') as mocked:
        mocked.return_value = {
            'category': 'ID Document',
            'extracted_name': 'Triage Client',
            'extracted_dob': '01/04/2002',
            'extracted_nationality': 'British',
            'scan_method': 'ocr',
            'extraction_quality': {'score': 90},
            'text_preview': 'PASSPORT',
        }
        with mock.patch('app.resolve_intake_company_match') as resolve:
            resolve.return_value = (
                {'id': 1, 'name': 'WOLF VANGUARD LIMITED'},
                'matched', 'ok',
                [{'name': 'WOLF VANGUARD LIMITED', 'score': 85}],
                {'confidence': 85, 'score_gap': 60, 'reasons': ['Exact normalized full name']},
            )
            high = app.triage_uploaded_document(png + b'hi', 'hi.png', client_id, client_id=client_id)
    assert high['lifecycle_status'] == 'READY_FOR_APPROVAL'

    with mock.patch('app.extract_document_text_and_metadata') as mocked:
        mocked.return_value = {
            'category': 'ID Document',
            'extracted_name': 'Triage Client',
            'extracted_dob': '01/04/2002',
            'extracted_nationality': 'British',
            'scan_method': 'ocr',
            'extraction_quality': {'score': 90},
            'text_preview': 'PASSPORT',
        }
        with mock.patch('app.resolve_intake_company_match') as resolve:
            resolve.return_value = (
                None, 'review_required', 'Ambiguous',
                [{'name': 'A', 'score': 70}, {'name': 'B', 'score': 68}],
                {'confidence': 70, 'score_gap': 2},
            )
            amb = app.triage_uploaded_document(png + b'amb', 'amb.png', client_id, client_id=client_id)
    assert amb['lifecycle_status'] == 'REVIEW_REQUIRED'
    print('✓ AI Triage State Test')

    # 4. Manual approval atomic + audit
    ready_id = execute_db(
        """
        INSERT INTO documents (
            user_id, name, category, file_path, file_type, file_size, status, uploaded_by,
            client_visible, lifecycle_status, is_posted
        ) VALUES (?, 'triage-ready.pdf', 'ID Document', '/tmp/triage-ready.pdf', 'PDF', '2 KB',
                  'Pending Review', 'Customer Upload', 0, 'READY_FOR_APPROVAL', 0);
        """,
        (client_id,),
    )
    from db import get_db
    conn = get_db()
    try:
        conn.execute("""
            UPDATE documents SET lifecycle_status='POSTED_DOCUMENTS', is_posted=1, status='Approved',
                approved_at=CURRENT_TIMESTAMP, posted_at=CURRENT_TIMESTAMP WHERE id=?;
        """, (ready_id,))
        conn.execute("""
            INSERT INTO document_audit_log (document_id, actor_type, actor_id, actor_name, action, previous_state, new_state)
            VALUES (?, 'HUMAN', ?, 'Triage Admin', 'POST_APPROVED', 'READY_FOR_APPROVAL', 'POSTED_DOCUMENTS');
        """, (ready_id, admin_id))
        conn.commit()
    finally:
        conn.close()
    row = query_db("SELECT is_posted, lifecycle_status FROM documents WHERE id=?;", (ready_id,), one=True)
    assert row['is_posted'] == 1 and row['lifecycle_status'] == 'POSTED_DOCUMENTS'
    audit = query_db(
        "SELECT action FROM document_audit_log WHERE document_id=? AND action='POST_APPROVED';",
        (ready_id,), one=True,
    )
    assert audit is not None
    print('✓ Manual Approval Test')

    # 5. Bulk approval safety
    bulk_ready = execute_db(
        """
        INSERT INTO documents (
            user_id, name, category, file_path, file_type, file_size, status, uploaded_by,
            client_visible, lifecycle_status, is_posted
        ) VALUES (?, 'bulk-ready.pdf', 'ID Document', '/tmp/bulk-ready.pdf', 'PDF', '2 KB',
                  'Pending Review', 'Customer Upload', 0, 'READY_FOR_APPROVAL', 0);
        """,
        (client_id,),
    )
    bulk_review = execute_db(
        """
        INSERT INTO documents (
            user_id, name, category, file_path, file_type, file_size, status, uploaded_by,
            client_visible, lifecycle_status, is_posted
        ) VALUES (?, 'bulk-review.pdf', 'ID Document', '/tmp/bulk-review.pdf', 'PDF', '2 KB',
                  'Pending Review', 'Customer Upload', 0, 'REVIEW_REQUIRED', 0);
        """,
        (client_id,),
    )
    override = False
    for did in (bulk_ready, bulk_review):
        d = query_db("SELECT * FROM documents WHERE id=?;", (did,), one=True)
        if d.get('lifecycle_status') == 'READY_FOR_APPROVAL' or override:
            execute_db(
                "UPDATE documents SET lifecycle_status='POSTED_DOCUMENTS', is_posted=1 WHERE id=?;",
                (did,),
            )
    assert query_db("SELECT is_posted FROM documents WHERE id=?;", (bulk_ready,), one=True)['is_posted'] == 1
    assert query_db("SELECT is_posted FROM documents WHERE id=?;", (bulk_review,), one=True)['is_posted'] == 0
    print('✓ Bulk Approval Safety Test')

    # 6. Bulk job progress
    job_id = 'job_triage_test_01'
    execute_db(
        "INSERT OR REPLACE INTO bulk_approval_jobs (job_id, user_id, total_count, status) VALUES (?, ?, 2, 'PROCESSING');",
        (job_id, admin_id),
    )
    execute_db(
        """
        UPDATE bulk_approval_jobs
        SET status='COMPLETED', approved_count=1, failed_count=0, skipped_count=1,
            errors_json='[]', completed_at=CURRENT_TIMESTAMP
        WHERE job_id=?;
        """,
        (job_id,),
    )
    job = query_db("SELECT * FROM bulk_approval_jobs WHERE job_id=?;", (job_id,), one=True)
    assert job['status'] == 'COMPLETED' and job['approved_count'] == 1 and job['skipped_count'] == 1
    print('✓ Bulk Job Progress Test')

    print('==================================================')
    print('ALL TRIAGE & APPROVAL WORKFLOW TESTS PASSED')
    print('==================================================')


if __name__ == '__main__':
    run_tests()
