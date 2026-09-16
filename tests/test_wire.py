"""Offline integration tests: real imaplib and CLI subprocesses on loopback."""
import base64
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.helpers import BODY, HEADERS

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'wire-test-password-not-a-real-credential'


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        self.wfile.write(b'* OK local test IMAP server\r\n')
        while True:
            line = self.rfile.readline()
            if not line:
                return
            self.server.commands.append(line)
            tag, command, *parts = line.rstrip(b'\r\n').split(b' ', 2)
            rest = parts[0] if parts else b''
            status = b'OK'
            message = b'completed'
            if command == b'CAPABILITY':
                self.wfile.write(b'* CAPABILITY ' + self.server.capabilities + b'\r\n')
            elif command == b'LOGIN':
                if self.server.auth_fail or b'LOGINDISABLED' in self.server.capabilities:
                    status, message = b'NO', SECRET.encode()
            elif command == b'AUTHENTICATE' and rest == b'PLAIN':
                self.wfile.write(b'+ \r\n')
                self.server.auth_payload = base64.b64decode(self.rfile.readline().strip(), validate=True)
                if self.server.auth_fail:
                    status, message = b'NO', SECRET.encode()
            elif command in (b'EXAMINE', b'SELECT'):
                if rest == b'"Missing"':
                    status = b'NO'
                else:
                    self.wfile.write(b'* 3 EXISTS\r\n* FLAGS (\\Seen \\Deleted)\r\n* OK [UIDVALIDITY 1234] valid\r\n')
                    message = b'[READ-ONLY] selected' if command == b'EXAMINE' else b'[READ-WRITE] selected'
            elif command == b'UID':
                operation, arguments = rest.split(b' ', 1)
                if operation == b'SEARCH':
                    if self.server.abort_search:
                        return
                    uids = sorted(self.server.flags)
                    if b'UNSEEN' in arguments:
                        uids = [uid for uid in uids if b'\\Seen' not in self.server.flags[uid]]
                    self.wfile.write(b'* SEARCH ' + b' '.join(str(uid).encode() for uid in uids) + b'\r\n')
                else:
                    uid_text, *tail = arguments.split(b' ', 1)
                    uid = int(uid_text)
                    details = tail[0] if tail else b''
                    if operation == b'FETCH' and uid in self.server.flags:
                        content = BODY if b'BODY.PEEK[]' in details else HEADERS
                        flags = b' '.join(self.server.flags[uid])
                        # Exercise metadata after a literal, not only the common UID-first order.
                        self.wfile.write(b'* 2 FETCH (BODY[] {' + str(len(content)).encode() + b'}\r\n' + content
                                         + b' FLAGS (' + flags + b') UID ' + uid_text + b' RFC822.SIZE 456)\r\n')
                    elif operation == b'STORE' and uid in self.server.flags:
                        mode, value = details.split(b' ', 1)
                        flag = value.strip(b'()')
                        if not self.server.ignore_store:
                            if mode == b'+FLAGS':
                                self.server.flags[uid].add(flag)
                            else:
                                self.server.flags[uid].discard(flag)
                        self.wfile.write(b'* 2 FETCH (FLAGS (' + b' '.join(self.server.flags[uid]) + b') UID ' + uid_text + b')\r\n')
                    elif operation in (b'MOVE', b'COPY'):
                        if details == b'"Missing"':
                            status = b'NO'
                        else:
                            self.server.copies.append((uid, details))
                            if operation == b'MOVE':
                                self.server.flags.pop(uid, None)
                                self.wfile.write(b'* 2 EXPUNGE\r\n')
                    elif operation == b'EXPUNGE':
                        if b'\\Deleted' in self.server.flags.get(uid, set()):
                            self.server.flags.pop(uid)
                            self.wfile.write(b'* 2 EXPUNGE\r\n')
            elif command == b'LOGOUT':
                self.wfile.write(b'* BYE logging out\r\n' + tag + b' OK logout\r\n')
                return
            else:
                status, message = b'BAD', b'unsupported command'
            self.wfile.write(tag + b' ' + status + b' ' + message + b'\r\n')


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class WireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.capabilities = b'IMAP4rev1 MOVE UIDPLUS'
        self.server.commands, self.server.copies = [], []
        self.server.flags = {7: set(), 42: {b'\\Seen'}, 99: {b'\\Deleted'}}
        self.server.auth_fail = self.server.abort_search = self.server.ignore_store = False
        self.server.auth_payload = None
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIMAIL_') and key != 'PYTHONPATH'}
        self.env.update(
            GIMAIL_CONFIG=str(Path(self.temp.name) / 'absent.json'),
            GIMAIL_HOST='127.0.0.1', GIMAIL_PORT=str(self.server.server_address[1]),
            GIMAIL_USER='wire"user\\name', GIMAIL_PASSWORD=SECRET, GIMAIL_SECURITY='plain',
        )

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def invoke(self, *args, executable=None, cwd=None):
        command = executable or [sys.executable, '-S', str(ROOT / 'gimail.py')]
        process = subprocess.run(command + list(args), cwd=cwd or self.temp.name,
                                 env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(process.stderr, '')
        self.assertNotIn(SECRET, process.stdout)
        return process.returncode, json.loads(process.stdout)

    def wire(self):
        return b''.join(self.server.commands)

    def test_first_use_from_clone_with_environment_only(self):
        status, result = self.invoke('list', '--unread', '--limit', '5')
        self.assertEqual(status, 0)
        self.assertEqual([message['uid'] for message in result['data']['messages']], [99, 7])
        self.assertEqual(result['data']['uidvalidity'], 1234)
        self.assertIn(b'UID SEARCH UNSEEN\r\n', self.wire())
        self.assertIn(b'EXAMINE "INBOX"\r\n', self.wire())
        self.assertIn(b'LOGIN "wire\\"user\\\\name"', self.wire())
        self.assertFalse(Path(self.env['GIMAIL_CONFIG']).exists())

    def test_show_does_not_change_seen_and_missing_uid_errors(self):
        status, result = self.invoke('show', '7')
        self.assertEqual(status, 0)
        self.assertIn('Hello body.', result['data']['text'])
        self.assertNotIn(b'\\Seen', self.server.flags[7])
        self.assertIn(b'BODY.PEEK[]', self.wire())
        status, result = self.invoke('show', '123')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'not_found')

    def test_mark_move_delete_previews_send_no_mutating_commands(self):
        for args in (('mark', '7', '--read'), ('move', '7', 'Archive'), ('delete', '7')):
            with self.subTest(args=args):
                status, result = self.invoke(*args)
                self.assertEqual(status, 0)
                self.assertTrue(result['data']['dry_run'])
        for operation in (b'UID STORE ', b'UID MOVE ', b'UID COPY ', b'EXPUNGE', b' CLOSE'):
            self.assertNotIn(operation, self.wire())
        self.assertNotIn(b' SELECT ', self.wire())

    def test_confirmed_mark_and_delete_are_uid_scoped_and_do_not_expunge(self):
        status, _ = self.invoke('mark', '7', '--read', '--confirm')
        self.assertEqual(status, 0)
        self.assertIn(b'\\Seen', self.server.flags[7])
        status, _ = self.invoke('--confirm', 'mark', '7', '--unread')
        self.assertEqual(status, 0)
        self.assertNotIn(b'\\Seen', self.server.flags[7])
        status, result = self.invoke('delete', '7', '--confirm')
        self.assertEqual(status, 0)
        self.assertFalse(result['data']['expunge_requested'])
        self.assertIn(b'\\Deleted', self.server.flags[7])
        self.assertIn(99, self.server.flags)
        self.assertNotIn(b'EXPUNGE', self.wire())
        self.assertNotIn(b' CLOSE', self.wire())

    def test_native_move_quotes_non_ascii_destination(self):
        status, _ = self.invoke('move', '7', '台北 & Stuff', '--confirm')
        self.assertEqual(status, 0)
        self.assertEqual(self.server.copies, [(7, b'"&U,BTFw- &- Stuff"')])
        self.assertNotIn(7, self.server.flags)
        self.assertIn(99, self.server.flags)

    def test_fallback_move_expunge_never_removes_another_deleted_uid(self):
        self.server.capabilities = b'IMAP4rev1 UIDPLUS'
        status, result = self.invoke('move', '7', 'Other Folder', '--confirm')
        self.assertEqual(status, 0)
        self.assertTrue(result['data']['expunge_requested'])
        self.assertNotIn(7, self.server.flags)
        self.assertIn(99, self.server.flags)
        self.assertIn(b'UID EXPUNGE 7\r\n', self.wire())
        self.assertNotIn(b' CLOSE', self.wire())

    def test_fallback_without_uidplus_retains_deleted_source(self):
        self.server.capabilities = b'IMAP4rev1'
        status, result = self.invoke('move', '7', 'Other Folder', '--confirm')
        self.assertEqual(status, 0)
        self.assertFalse(result['data']['expunge_requested'])
        self.assertIn(b'\\Deleted', self.server.flags[7])
        self.assertEqual(self.server.copies, [(7, b'"Other Folder"')])
        self.assertNotIn(b'EXPUNGE', self.wire())

    def test_search_utf8_and_mailbox_quoting(self):
        status, _ = self.invoke('search', '--from', 'a"b\\c', '--subject', 'café', '--query', 'SINCE 01-Jan-2026', '--folder', 'Other Folder')
        self.assertEqual(status, 0)
        self.assertIn('UID SEARCH CHARSET UTF-8 FROM "a\\"b\\\\c" SUBJECT "café" SINCE 01-Jan-2026\r\n'.encode(), self.wire())
        self.assertIn(b'EXAMINE "Other Folder"', self.wire())

    def test_auth_failure_never_echoes_server_response(self):
        self.server.auth_fail = True
        status, result = self.invoke('account', 'test')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'auth_failed')
        self.assertNotIn(SECRET, json.dumps(result))

    def test_ascii_authenticate_plain_when_login_disabled(self):
        self.server.capabilities += b' AUTH=PLAIN LOGINDISABLED'
        status, result = self.invoke('account', 'test')
        self.assertEqual(status, 0)
        self.assertTrue(result['data']['connected'])
        self.assertIn(b'AUTHENTICATE PLAIN\r\n', self.wire())
        self.assertNotIn(b' LOGIN ', self.wire())
        self.assertEqual(self.server.auth_payload, b'\0' + self.env['GIMAIL_USER'].encode() + b'\0' + SECRET.encode())

    def test_flag_keywords_cannot_spoof_uid_or_message_size(self):
        self.server.flags[7] = {b'UID', b'42', b'RFC822.SIZE', b'999', b'\\Seen'}
        status, result = self.invoke('show', '7')
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['uid'], 7)
        self.assertEqual(result['data']['size'], 456)
        self.assertFalse(result['data']['unread'])

    def test_ignored_store_returns_error_and_fallback_move_does_not_expunge(self):
        self.server.capabilities = b'IMAP4rev1 UIDPLUS'
        self.server.ignore_store = True
        for args in (('mark', '7', '--read'), ('delete', '7'), ('move', '7', 'Archive')):
            with self.subTest(args=args):
                status, result = self.invoke(*args, '--confirm')
                self.assertEqual(status, 1)
                self.assertEqual(result['code'], 'imap_error')
                self.assertIn('did not apply', result['error'])
        self.assertNotIn(b'EXPUNGE', self.wire())
        self.assertNotIn(b'\\Deleted', self.server.flags[7])
        self.assertNotIn(b'\\Seen', self.server.flags[7])

    def test_aborted_search_returns_imap_error(self):
        self.server.abort_search = True
        status, result = self.invoke('list')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'imap_error')

    def test_closed_output_pipe_does_not_emit_a_traceback(self):
        with subprocess.Popen([sys.executable, '-S', str(ROOT / 'gimail.py'), 'list'],
                              cwd=self.temp.name, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            process.stdout.close()
            process.stdout = None
            _, errors = process.communicate(timeout=10)
        self.assertEqual(errors, b'')
        self.assertEqual(process.returncode, 0)

    def test_module_and_symlink_entry_points(self):
        status, _ = self.invoke('list', '--limit', '1', executable=[sys.executable, '-m', 'gimail'], cwd=ROOT)
        self.assertEqual(status, 0)
        symlink = Path(self.temp.name) / 'gimail'
        symlink.symlink_to(ROOT / 'gimail.py')
        # Ensure the shebang uses the project's local test interpreter.
        self.env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + self.env.get('PATH', '')
        status, _ = self.invoke('list', '--limit', '1', executable=[str(symlink)])
        self.assertEqual(status, 0)
