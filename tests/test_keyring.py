import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gimail.accounts import Account, add_account, load_config, remove_account, update_account
from gimail.errors import GimailError
from gimail.keyring import LOOKUP_TIMEOUT, lookup_password
from tests.keyring_helper import make_secret_tool


SECRET = 'keyring-fixture-not-a-real-password'


class KeyringTests(unittest.TestCase):
    def setUp(self):
        self.account = Account.from_record(dict(name='personal', host='imap.example.org', user='me', password_keyring=True))
        env = patch.dict(os.environ, {'FALLBACK_PASSWORD': SECRET, 'GIMAIL_PASSWORD': SECRET}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        helper = patch('gimail.keyring.shutil.which', return_value='/fixture/secret-tool')
        self.helper = helper.start()
        self.addCleanup(helper.stop)
        run = patch('gimail.keyring.subprocess.run', return_value=subprocess.CompletedProcess([], 0, SECRET.encode()))
        self.run = run.start()
        self.addCleanup(run.stop)

    def test_lookup_uses_fixed_attributes_no_shell_and_captured_output(self):
        self.assertEqual(self.account.secret(), SECRET)
        self.helper.assert_called_once_with('secret-tool')
        self.run.assert_called_once_with(
            ['/fixture/secret-tool', 'lookup', '--', 'service', 'gimail', 'account', 'personal'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=LOOKUP_TIMEOUT, check=False,
        )
        for value in (repr(self.account), json.dumps(self.account.public()), json.dumps(self.account.record())):
            self.assertNotIn(SECRET, value)
        self.assertEqual(self.account.public()['credential_source'], 'keyring')
        self.assertEqual(self.account.record()['password_keyring'], True)
        self.assertNotIn('password', self.account.record())
        self.assertNotIn('password_env', self.account.record())

    def test_option_like_and_shell_metacharacter_names_are_plain_arguments(self):
        for name in ('--help', 'semi;colon', '$(not-a-command)', 'space name', '台北'):
            with self.subTest(name=name):
                self.assertEqual(lookup_password(name), SECRET)
                self.assertEqual(self.run.call_args.args[0][-1], name)
                self.assertEqual(self.run.call_args.args[0][2], '--')
                self.assertNotIn('shell', self.run.call_args.kwargs)

    def test_spaces_quotes_and_unicode_are_preserved(self):
        value = '  sëcret " \\ value  '
        self.run.return_value.stdout = value.encode()
        self.assertEqual(self.account.secret(), value)

    def test_missing_helper_is_actionable_without_fallback(self):
        self.helper.return_value = None
        with self.assertRaises(GimailError) as caught:
            self.account.secret()
        self.assertEqual(caught.exception.code, 'auth_failed')
        self.assertIn('libsecret-tools', str(caught.exception))
        self.run.assert_not_called()

    def test_failed_or_empty_lookup_never_uses_environment_fallback(self):
        for status, output in ((1, b''), (1, SECRET.encode()), (0, b''), (-9, SECRET.encode())):
            with self.subTest(status=status, output=output):
                self.run.return_value = subprocess.CompletedProcess([], status, output, SECRET.encode())
                with self.assertRaises(GimailError) as caught:
                    self.account.secret()
                self.assertEqual(caught.exception.code, 'auth_failed')
                self.assertNotIn(SECRET, str(caught.exception))

    def test_invalid_text_or_controls_are_rejected_not_stripped(self):
        for output in (b'\xff', b'secret\n', b'secret\r', b'secret\x00', b'secret\x1b'):
            with self.subTest(output=output):
                self.run.return_value.stdout = output
                with self.assertRaises(GimailError) as caught:
                    self.account.secret()
                self.assertEqual(caught.exception.code, 'auth_failed')
                self.assertNotIn('secret\n', str(caught.exception))

    def test_process_errors_and_timeouts_cannot_echo_captured_secrets(self):
        for error in (FileNotFoundError(SECRET), PermissionError(SECRET),
                      subprocess.TimeoutExpired(['secret-tool'], LOOKUP_TIMEOUT, output=SECRET.encode(), stderr=SECRET.encode())):
            with self.subTest(error=type(error).__name__):
                self.run.side_effect = error
                with self.assertRaises(GimailError) as caught:
                    self.account.secret()
                self.assertEqual(caught.exception.code, 'auth_failed')
                self.assertNotIn(SECRET, str(caught.exception))

    def test_existing_credential_sources_do_not_need_helper(self):
        for source in ({'password': SECRET}, {'password_env': 'FALLBACK_PASSWORD'}):
            account = Account.from_record(dict(name='other', host='host', user='me', **source))
            self.assertEqual(account.secret(), SECRET)
        self.helper.assert_not_called()
        self.run.assert_not_called()

    def test_metadata_operations_never_access_keyring(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.json'
            add_account(path, self.account)
            self.assertEqual(load_config(path)[0][0], self.account)
            before = path.read_bytes()
            result = update_account(path, 'personal', {'user': 'changed'})
            self.assertTrue(result['dry_run'])
            self.assertEqual(path.read_bytes(), before)
            update_account(path, 'personal', {'user': 'changed'}, confirm=True)
            self.assertTrue(load_config(path)[0][0].password_keyring)
            remove_account(path, 'personal', confirm=True)
        self.helper.assert_not_called()
        self.run.assert_not_called()

    def test_schema_requires_exactly_one_credential_source_and_strict_boolean(self):
        base = dict(name='personal', host='host', user='me')
        for source in ({'password_keyring': False}, {'password_keyring': None}, {'password_keyring': 1},
                       {'password_keyring': 'true'}, {'password_keyring': {}},
                       {'password_keyring': True, 'password_env': 'PASSWORD'},
                       {'password_keyring': True, 'password': SECRET}):
            with self.subTest(source=source), self.assertRaises(GimailError):
                Account.from_record({**base, **source})
        account = Account.from_record({**base, 'password_keyring': False, 'password_env': 'FALLBACK_PASSWORD'})
        self.assertFalse(account.password_keyring)
        self.assertNotIn('password_keyring', account.record())


class KeyringProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # No inherited desktop bus or real secret-tool can be reached.
        env = patch.dict(os.environ, {'PATH': str(self.directory), 'HOME': self.temp.name,
                                     'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/nonexistent-gimail-test-bus'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_real_helper_capture_discards_stderr_and_preserves_stdout(self):
        make_secret_tool(self.directory, f'import sys; sys.stdout.write({SECRET!r}); sys.stderr.write({SECRET!r})')
        self.assertEqual(lookup_password('--help'), SECRET)

    def test_real_nonzero_helper_output_is_not_a_password_or_diagnostic(self):
        make_secret_tool(self.directory, f'import sys; sys.stdout.write({SECRET!r}); sys.stderr.write({SECRET!r}); sys.exit(1)')
        with self.assertRaises(GimailError) as caught:
            lookup_password('personal')
        self.assertEqual(caught.exception.code, 'auth_failed')
        self.assertNotIn(SECRET, str(caught.exception))

    def test_real_timeout_kills_and_reaps_helper_without_leaking_partial_output(self):
        pid_file = self.directory / 'pid'
        body = f'''import os, sys, time
from pathlib import Path
Path({str(pid_file)!r}).write_text(str(os.getpid()))
print({SECRET!r}, flush=True)
time.sleep(30)
'''
        make_secret_tool(self.directory, body)
        started = time.monotonic()
        with patch('gimail.keyring.LOOKUP_TIMEOUT', 1), self.assertRaises(GimailError) as caught:
            lookup_password('personal')
        self.assertEqual(caught.exception.code, 'auth_failed')
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertLess(time.monotonic() - started, 5)
        if pid_file.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
