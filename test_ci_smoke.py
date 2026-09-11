import io
import os
import sqlite3
import tempfile


def request_homepage(application):
    status = {}
    headers = {}

    def start_response(response_status, response_headers, _exc_info=None):
        status['value'] = response_status
        headers.update({str(key).lower(): value for key, value in response_headers})

    environ = {
        'REQUEST_METHOD': 'GET',
        'PATH_INFO': '/',
        'QUERY_STRING': '',
        'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': '5050',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'wsgi.url_scheme': 'http',
        'wsgi.input': io.BytesIO(),
        'wsgi.errors': io.StringIO(),
        'wsgi.version': (1, 0),
        'wsgi.multithread': False,
        'wsgi.multiprocess': False,
        'wsgi.run_once': False,
        'CONTENT_LENGTH': '0',
        'HTTP_HOST': '127.0.0.1:5050',
    }
    body = b''.join(application(environ, start_response))
    return status.get('value'), headers, body


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = os.path.join(temp_dir, 'ci-smoke.db')
        os.environ['CRM_TESTING'] = '1'
        os.environ['DATABASE_URL'] = database_path

        from db import init_db

        init_db()
        connection = sqlite3.connect(database_path)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_tables = {'users', 'documents', 'orders', 'invoices', 'settings'}
        assert required_tables <= tables
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        connection.close()

        from app import application

        status, headers, body = request_homepage(application)
        assert status == '200 OK', status
        assert headers.get('content-type', '').startswith('text/html')
        assert b'Brixen Consultants' in body
        assert b'password_hash' not in body
        assert b'session_token' not in body

    print('CI smoke test passed: schema, integrity, WSGI startup, homepage, and secret filtering')


if __name__ == '__main__':
    main()
