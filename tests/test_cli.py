import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gimail.cli import main, terminal_safe
from tests.helpers import FakeIMAP


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'accounts.json'
        env = patch.dict(os.environ, {
            'GIMAIL_CONFIG': str(self.path), 'GIMAIL_HOST': 'imap.example.org',
            'GIMAIL_USER': 'me', 'GIMAIL_PASSWORD': 'never-print-this-secret',
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeIMAP()
        factory = patch('gimail.imap_client.imaplib.IMAP4_SSL', return_value=self.fake)
        factory.start()
        self.addCleanup(factory.stop)

    def invoke(self, *args, text=False):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = main(list(args))
        self.assertEqual(errors.getvalue(), '')
        self.assertNotIn('never-print-this-secret', output.getvalue())
        return status, output.getvalue() if text else json.loads(output.getvalue())

    def test_first_use_unread_via_env_no_config(self):
        status, result = self.invoke('list', '--unread', '--limit', '5')
        self.assertEqual(status, 0)
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['account'], 'env')
        self.assertFalse(self.path.exists())

    def test_global_flags_before_and_after_subcommand(self):
        for args in (('--text', '--folder', 'Other Folder', 'list'),
                     ('list', '--text', '--folder', 'Other Folder')):
            with self.subTest(args=args):
                status, result = self.invoke(*args, text=True)
                self.assertEqual(status, 0)
                self.assertIn('UID\tSTATE', result)
                self.assertIn(('SELECT', '"Other Folder"', True), self.fake.calls)

    def test_all_mutations_select_readonly_for_preview_and_writable_on_confirm(self):
        for args in (('mark', '7', '--read'), ('move', '7', 'Archive'), ('delete', '7')):
            for confirm in (False, True):
                with self.subTest(args=args, confirm=confirm):
                    self.fake.calls.clear()
                    status, result = self.invoke(*args, *(['--confirm'] if confirm else []))
                    self.assertEqual(status, 0)
                    self.assertEqual(result['data']['dry_run'], not confirm)
                    self.assertIn(('SELECT', '"INBOX"', not confirm), self.fake.calls)
                    if not confirm:
                        self.assertFalse(any(call[0] in ('STORE', 'MOVE', 'COPY', 'EXPUNGE') for call in self.fake.calls))

    def test_bad_arguments_are_json_without_echoed_secrets_or_network(self):
        cases = [(), ('no-such-command',), ('mark', '7'), ('mark', '7', '--read', '--unread'),
                 ('delete', '1:*'), ('delete', '0'), ('delete', '4294967296'),
                 ('delete', '７'), ('delete', '7', '--confir'), ('list', '--limit', '0'),
                 ('list', '--password', 'never-print-this-secret')]
        for args in cases:
            with self.subTest(args=args):
                status, result = self.invoke(*args)
                self.assertEqual(status, 2)
                self.assertFalse(result['ok'])
                self.assertEqual(result['code'], 'imap_error')
        self.assertEqual(self.fake.calls, [])

    def test_account_add_list_and_test_named(self):
        status, result = self.invoke('account', 'add', 'gmail', '--preset', 'gmail', '--user', 'me@gmail.com', '--password-env', 'GMAIL_PASSWORD')
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['host'], 'imap.gmail.com')
        self.assertEqual(self.fake.calls, [])
        os.environ['GMAIL_PASSWORD'] = 'never-print-this-secret'
        status, result = self.invoke('account', 'list')
        self.assertEqual(result['data']['accounts'][0]['password_env'], 'GMAIL_PASSWORD')
        self.assertEqual(self.fake.calls, [])
        status, result = self.invoke('--account', 'gmail', 'account', 'test')
        self.assertEqual(status, 0)
        self.assertTrue(result['data']['connected'])
        self.assertEqual(result['data']['account'], 'gmail')
        status, _ = self.invoke('account', 'test', 'gmail')
        self.assertEqual(status, 0)

    def test_account_argument_errors_do_not_write_or_connect(self):
        cases = [
            ('account', 'test', 'one', '--account', 'two'),
            ('account', 'add', 'work', '--user', 'me', '--password-env', 'PASSWORD'),
        ]
        for port in ('0', 'bad', '65536'):
            cases.append(('account', 'add', 'work', '--host', 'host', '--user', 'me', '--password-env', 'PASSWORD', '--port', port))
        for args in cases:
            with self.subTest(args=args):
                status, result = self.invoke(*args)
                self.assertEqual(status, 2)
                self.assertFalse(result['ok'])
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.path.exists())

    def test_explicit_config_flag_overrides_environment(self):
        other = self.path.with_name('explicit.json')
        status, _ = self.invoke('--config', str(other), 'account', 'add', 'work', '--host', 'host', '--user', 'me', '--password-env', 'PASSWORD')
        self.assertEqual(status, 0)
        self.assertTrue(other.exists())
        self.assertFalse(self.path.exists())
        status, result = self.invoke('account', 'list', '--config', str(other))
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['accounts'][0]['name'], 'work')

    def test_password_stdin_preserves_spaces_and_is_never_echoed(self):
        with patch('sys.stdin', io.StringIO('  never-print-this-secret  \n')):
            status, _ = self.invoke('account', 'add', 'work', '--host', 'mail.example.org', '--user', 'me', '--password-stdin')
        self.assertEqual(status, 0)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['accounts'][0]['password'], '  never-print-this-secret  ')
        status, _ = self.invoke('account', 'list')
        self.assertEqual(status, 0)

    def test_auth_error_json_and_text(self):
        self.fake.failure = 'LOGIN'
        status, result = self.invoke('account', 'test')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'auth_failed')
        status, result = self.invoke('account', 'test', '--text', text=True)
        self.assertIn('Error (auth_failed)', result)

    def test_unexpected_exception_is_sanitized(self):
        with patch('gimail.cli.dispatch', side_effect=RuntimeError('never-print-this-secret')):
            status, result = self.invoke('list')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'imap_error')

    def test_text_show_and_dry_run(self):
        status, result = self.invoke('show', '7', '--text', text=True)
        self.assertEqual(status, 0)
        self.assertIn('Hello body.', result)
        self.assertIn('Subject: Hello ✓', result)
        status, result = self.invoke('delete', '7', '--text', text=True)
        self.assertIn('DRY RUN', result)
        self.assertIn('--confirm', result)
        status, result = self.invoke('move', '7', 'Other Folder', '--text', text=True)
        self.assertEqual(status, 0)
        self.assertIn('-> Other Folder', result)
        status, result = self.invoke('search', '--subject', 'Hello', '--text', text=True)
        self.assertEqual(status, 0)
        self.assertIn('UID\tSTATE', result)

    def test_terminal_escapes_cannot_execute(self):
        self.assertEqual(terminal_safe('evil\x1b[31m\r\nSubject\u202e'), 'evil\\u001b[31m  Subject\\u202e')
        self.assertEqual(terminal_safe('a\nb', multiline=True), 'a\nb')
