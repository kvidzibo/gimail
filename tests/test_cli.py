import contextlib
import io
import json
import os
import smtplib
import ssl
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

    def test_send_preview_confirmation_and_delivery_safety(self):
        body = 'Hello ✓.\n\x1b[31mNot terminal instructions.\n'
        body_path = Path(self.temp.name) / 'body.txt'
        body_path.write_text(body, encoding='utf-8')
        command = ('send', '--account', 'sender', '--to', 'one@example.org',
                   '--to', 'two@example.org', '--to', 'one@example.org',
                   '--subject', 'Hello ✓', '--body-file', str(body_path))
        with patch('gimail.accounts.lookup_password', return_value='never-print-this-secret') as lookup, \
                patch('gimail.smtp_client.smtplib.SMTP_SSL') as tls, \
                patch('gimail.smtp_client.smtplib.SMTP') as starttls:
            status, _ = self.invoke('account', 'add', 'sender', '--preset', 'gmail',
                                    '--user', 'me@example.org', '--keyring')
            self.assertEqual(status, 0)
            status, preview = self.invoke(*command)
            self.assertEqual(status, 0)
            self.assertTrue(preview['data']['dry_run'])
            self.assertEqual(preview['data']['body'], body)
            self.assertEqual(preview['data']['to'], ['one@example.org', 'two@example.org'])
            self.assertEqual(preview['data']['smtp_port'], 465)
            self.assertEqual(preview['data']['delivery'], 'not_sent')
            status, text = self.invoke(*command, '--text', text=True)
            self.assertEqual(status, 0)
            self.assertIn('DRY RUN', text)
            self.assertNotIn('\x1b', text)
            lookup.assert_not_called()
            tls.assert_not_called()
            starttls.assert_not_called()

            for field, value in (('--from', ''), ('--from', 'me@example.org\r\nBcc: bad@example.org'),
                                 ('--to', 'Name <bad@example.org>'), ('--to', 'a@example.org,b@example.org'),
                                 ('--to', 'ü@example.org'), ('--subject', 'x\nBcc: bad@example.org')):
                with self.subTest(field=field, value=value):
                    status, result = self.invoke(*command, field, value, '--confirm')
                    self.assertEqual(status, 2)
                    self.assertFalse(result['ok'])
            lookup.assert_not_called()
            tls.assert_not_called()
            starttls.assert_not_called()

            smtp = tls.return_value
            smtp.send_message.return_value = {}
            smtp.close.side_effect = OSError('never-print-this-secret')
            status, result = self.invoke(*command, '--confirm')
            self.assertEqual(status, 0)  # Cleanup failure after acceptance must not trigger a retry.
            self.assertEqual(result['data']['delivery'], 'accepted')
            self.assertNotIn('body', result['data'])
            self.assertEqual(smtp.send_message.call_count, 1)
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(str(message['Subject']), 'Hello ✓')
            self.assertEqual(message.get_content().replace('\r\n', '\n'), body)
            self.assertTrue(message['Message-ID'])
            self.assertTrue(message['Date'])
            self.assertEqual(smtp.send_message.call_args.kwargs['from_addr'], 'me@example.org')
            self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'], preview['data']['to'])
            message.as_bytes().decode('ascii')  # Unicode content does not require SMTPUTF8/8BITMIME.
            context = tls.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(tls.call_args.args, ('smtp.gmail.com', 465))
            smtp.login.assert_called_once_with('me@example.org', 'never-print-this-secret')
            smtp.close.assert_called_once()

            failures = (
                ({'two@example.org': (550, b'never-print-this-secret')}, 'partial_delivery', 'partial'),
                (smtplib.SMTPRecipientsRefused({'one@example.org': (550, b'never-print-this-secret')}), 'smtp_error', 'not_sent'),
                (smtplib.SMTPDataError(554, b'never-print-this-secret'), 'smtp_error', 'not_sent'),
                (smtplib.SMTPServerDisconnected('never-print-this-secret'), 'delivery_unknown', 'unknown'),
            )
            for failure, code, delivery in failures:
                with self.subTest(code=code, delivery=delivery):
                    smtp.reset_mock()
                    smtp.send_message.side_effect = failure if isinstance(failure, Exception) else None
                    smtp.send_message.return_value = failure if isinstance(failure, dict) else {}
                    status, result = self.invoke(*command, '--confirm')
                    self.assertEqual(status, 1)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['code'], code)
                    self.assertEqual(result['data']['delivery'], delivery)
                    smtp.send_message.assert_called_once()  # No blind retry, even on ambiguity.
                    smtp.close.assert_called_once()
                    if delivery == 'partial':
                        self.assertEqual(result['data']['accepted'], ['one@example.org'])
                        self.assertEqual(result['data']['refused'], ['two@example.org'])

            # Stored settings round-trip; a previewed update cannot change the endpoint.
            status, _ = self.invoke('account', 'update', 'sender', '--smtp-host', 'smtp.example.org',
                                    '--smtp-security', 'starttls', '--smtp-port', '587')
            self.assertEqual(status, 0)
            saved = json.loads(self.path.read_text())['accounts'][0]
            self.assertEqual(saved['smtp_security'], 'ssl')
            status, _ = self.invoke('account', 'update', 'sender', '--smtp-host', 'smtp.example.org',
                                    '--smtp-security', 'starttls', '--smtp-port', '587', '--confirm')
            self.assertEqual(status, 0)
            upgraded = starttls.return_value
            upgraded.send_message.return_value = {}
            for failure in (ssl.SSLError('never-print-this-secret'), None):
                with self.subTest(starttls_failure=failure is not None):
                    upgraded.reset_mock()
                    upgraded.starttls.side_effect = failure
                    status, result = self.invoke(*command, '--confirm')
                    self.assertEqual(status, 1 if failure else 0)
                    if failure:
                        upgraded.login.assert_not_called()
                        upgraded.send_message.assert_not_called()
                    else:
                        self.assertEqual([call[0] for call in upgraded.method_calls],
                                         ['ehlo', 'starttls', 'ehlo', 'login', 'send_message', 'close'])
                        context = upgraded.starttls.call_args.kwargs['context']
                        self.assertTrue(context.check_hostname)
                        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

            os.environ.update(GIMAIL_SMTP_HOST='smtp.example.net', GIMAIL_USER='env@example.net')
            with patch('sys.stdin', io.StringIO('Body from stdin ✓')):
                status, result = self.invoke('send', '--to', 'one@example.org', '--subject', 'Stdin', '--body-stdin')
            self.assertEqual(status, 0)
            self.assertEqual(result['data']['body'], 'Body from stdin ✓')
            self.assertEqual(result['data']['smtp_host'], 'smtp.example.net')
            self.assertEqual(result['data']['smtp_port'], 465)
        self.assertEqual(self.fake.calls, [])  # SMTP send never opens an IMAP mailbox.

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

    def test_keyring_add_list_and_remove_never_resolve_credentials(self):
        with patch('gimail.accounts.lookup_password', side_effect=AssertionError('metadata must not access keyring')) as lookup:
            status, result = self.invoke('account', 'add', 'personal', '--preset', 'gmail', '--user', 'me', '--keyring')
            self.assertEqual(status, 0)
            self.assertEqual(result['data']['credential_source'], 'keyring')
            saved = json.loads(self.path.read_text())['accounts'][0]
            self.assertTrue(saved['password_keyring'])
            self.assertNotIn('password', saved)
            self.assertNotIn('password_env', saved)
            status, result = self.invoke('account', 'list')
            self.assertEqual(status, 0)
            self.assertEqual(result['data']['accounts'][0]['credential_source'], 'keyring')
            status, text = self.invoke('account', 'list', '--text', text=True)
            self.assertEqual(status, 0)
            self.assertIn('keyring', text)
            status, _ = self.invoke('account', 'remove', 'personal', '--confirm')
            self.assertEqual(status, 0)
            lookup.assert_not_called()
        self.assertEqual(self.fake.calls, [])

    def test_keyring_update_is_previewed_and_switches_sources_without_lookup(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        with patch('gimail.accounts.lookup_password', side_effect=AssertionError('config update must not access keyring')) as lookup:
            status, result = self.invoke('account', 'update', 'personal', '--keyring')
            self.assertEqual(status, 0)
            self.assertEqual(result['data']['changed_fields'], ['password_env', 'password_keyring'])
            self.assertEqual(before, self.path.read_bytes())
            status, _ = self.invoke('account', 'update', 'personal', '--keyring', '--confirm')
            self.assertEqual(status, 0)
            saved = json.loads(self.path.read_text())['accounts'][0]
            self.assertTrue(saved['password_keyring'])
            self.assertNotIn('password_env', saved)
            status, _ = self.invoke('account', 'update', 'personal', '--password-env', 'NEW_PASSWORD', '--confirm')
            self.assertEqual(status, 0)
            saved = json.loads(self.path.read_text())['accounts'][0]
            self.assertNotIn('password_keyring', saved)
            self.assertEqual(saved['password_env'], 'NEW_PASSWORD')
            lookup.assert_not_called()
        self.assertEqual(self.fake.calls, [])

    def test_keyring_flags_are_mutually_exclusive_with_other_sources(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        for command in (('account', 'add', 'new', '--preset', 'gmail', '--user', 'me'),
                        ('account', 'update', 'personal')):
            for other in (('--password-env', 'PASSWORD'), ('--password-stdin',)):
                status, result = self.invoke(*command, '--keyring', *other, '--confirm')
                self.assertEqual(status, 2)
                self.assertFalse(result['ok'])
                self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.fake.calls, [])

    def test_normal_keyring_commands_login_without_exposing_password(self):
        self.invoke('account', 'add', 'personal', '--preset', 'gmail', '--user', 'me', '--keyring')
        with patch('gimail.accounts.lookup_password', return_value='never-print-this-secret') as lookup:
            for args in (('account', 'test', 'personal'), ('list', '--account', 'personal', '--unread', '--limit', '5')):
                status, result = self.invoke(*args)
                self.assertEqual(status, 0)
                self.assertEqual(result['data']['account'], 'personal')
            self.assertEqual(lookup.call_count, 2)
            lookup.assert_called_with('personal')
        self.assertIn(('LOGIN', '"me"', 'never-print-this-secret'), self.fake.calls)

    def test_keyring_failures_are_json_or_text_auth_errors_before_connecting(self):
        self.invoke('account', 'add', 'personal', '--preset', 'gmail', '--user', 'me', '--keyring')
        with patch('gimail.keyring.shutil.which', return_value=None):
            status, result = self.invoke('account', 'test', 'personal')
            self.assertEqual(status, 1)
            self.assertEqual(result['code'], 'auth_failed')
            status, text = self.invoke('account', 'test', 'personal', '--text', text=True)
            self.assertEqual(status, 1)
            self.assertIn('auth_failed', text)
            self.assertIn('libsecret-tools', text)
        self.assertEqual(self.fake.calls, [])

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

    def seed_saved_accounts(self):
        for name in ('personal', 'work'):
            status, _ = self.invoke('account', 'add', name, '--host', 'imap.example.org',
                                    '--user', name, '--password-env', 'UNSET_PASSWORD')
            self.assertEqual(status, 0)

    def test_account_update_preview_and_confirm(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        status, result = self.invoke('account', 'update', 'personal', '--password-env', 'GMAIL_APP_PASSWORD')
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['changed_fields'], ['password_env'])
        self.assertTrue(result['data']['dry_run'])
        self.assertEqual(before, self.path.read_bytes())
        status, result = self.invoke('--confirm', 'account', 'update', 'personal', '--password-env', 'GMAIL_APP_PASSWORD')
        self.assertEqual(status, 0)
        self.assertFalse(result['data']['dry_run'])
        self.assertEqual(json.loads(self.path.read_text())['accounts'][0]['password_env'], 'GMAIL_APP_PASSWORD')
        self.assertEqual(self.fake.calls, [])

    def test_account_update_all_connection_fields_and_default(self):
        self.seed_saved_accounts()
        status, result = self.invoke('account', 'update', 'work', '--host', 'other.example.org',
                                     '--port', '143', '--security', 'starttls', '--user', 'other', '--default', '--confirm')
        self.assertEqual(status, 0)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['default_account'], 'work')
        self.assertEqual(saved['accounts'][1], dict(name='work', host='other.example.org', port=143,
                                                   user='other', security='starttls', password_env='UNSET_PASSWORD'))
        self.assertEqual(self.fake.calls, [])

    def test_account_update_stdin_is_secret_safe_in_preview_and_apply(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        for confirm in (False, True):
            with patch('sys.stdin', io.StringIO('  never-print-this-secret  \n')):
                status, result = self.invoke('account', 'update', 'personal', '--password-stdin',
                                             *(['--confirm'] if confirm else []))
            self.assertEqual(status, 0)
            if not confirm:
                self.assertEqual(before, self.path.read_bytes())
        saved = json.loads(self.path.read_text())['accounts'][0]
        self.assertEqual(saved['password'], '  never-print-this-secret  ')
        self.assertNotIn('password_env', saved)
        self.assertEqual(self.fake.calls, [])

    def test_new_account_command_argument_errors_are_json(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        cases = [
            ('account', 'update', 'personal'),
            ('account', 'update', 'personal', '--password-env', 'PASSWORD', '--password-stdin'),
            ('account', 'update', 'personal', '--password', 'never-print-this-secret'),
            ('account', 'update', 'personal', '--port', '65536'),
            ('account', 'update', 'personal', '--host', 'host', '--account', 'work'),
            ('account', 'remove', 'personal', '--account', 'work'),
            ('account', 'remove', 'personal', '--confir'),
        ]
        for args in cases:
            with self.subTest(args=args):
                status, result = self.invoke(*args)
                self.assertEqual(status, 2)
                self.assertFalse(result['ok'])
                self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.fake.calls, [])

    def test_empty_explicit_names_never_fall_through_to_another_account(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        cases = [
            (('account', 'update', '', '--account', 'personal', '--user', 'changed', '--confirm'), 2),
            (('account', 'remove', '', '--account', 'personal', '--confirm'), 2),
            (('account', 'test', '', '--account', 'personal'), 2),
            (('account', 'remove', '', '--confirm'), 1),
            (('account', 'update', '', '--user', 'changed', '--confirm'), 1),
            (('account', 'test', ''), 1),
        ]
        for args, expected_status in cases:
            with self.subTest(args=args):
                self.path.write_bytes(before)
                self.fake.calls.clear()
                status, result = self.invoke(*args)
                self.assertEqual(status, expected_status)
                self.assertFalse(result['ok'])
                self.assertEqual(before, self.path.read_bytes())
                self.assertEqual(self.fake.calls, [])

    def test_account_remove_name_preview_confirm_and_readd(self):
        self.seed_saved_accounts()
        before = self.path.read_bytes()
        status, result = self.invoke('account', 'remove', 'personal')
        self.assertEqual(status, 0)
        self.assertTrue(result['data']['dry_run'])
        self.assertEqual(result['data']['default_account'], 'work')
        self.assertEqual(before, self.path.read_bytes())
        status, result = self.invoke('account', 'remove', 'personal', '--confirm')
        self.assertEqual(status, 0)
        self.assertFalse(result['data']['dry_run'])
        self.assertEqual(json.loads(self.path.read_text())['default_account'], 'work')
        status, _ = self.invoke('account', 'add', 'personal', '--preset', 'gmail', '--user', 'me@gmail.com', '--password-env', 'PASSWORD')
        self.assertEqual(status, 0)
        self.assertEqual(self.fake.calls, [])

    def test_noninteractive_remove_requires_explicit_name_and_ignores_env_default(self):
        self.seed_saved_accounts()
        os.environ['GIMAIL_ACCOUNT'] = 'personal'
        before = self.path.read_bytes()
        for confirm in (False, True):
            with patch('sys.stdin', io.StringIO('1\n')):
                status, result = self.invoke('account', 'remove', *(['--confirm'] if confirm else []))
            self.assertEqual(status, 2)
            self.assertIn('interactive terminal', result['error'])
            self.assertEqual(before, self.path.read_bytes())
        # An explicit --account is a supported alternative to the positional name.
        status, result = self.invoke('--account', 'work', 'account', 'remove', '--confirm')
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['account'], 'work')
        self.assertEqual(self.fake.calls, [])

    def test_account_remove_empty_and_missing_return_not_found(self):
        status, result = self.invoke('account', 'remove')
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'not_found')
        self.seed_saved_accounts()
        for args in (('account', 'remove', 'missing'), ('account', 'update', 'missing', '--user', 'new')):
            status, result = self.invoke(*args)
            self.assertEqual(status, 1)
            self.assertEqual(result['code'], 'not_found')
        self.assertEqual(self.fake.calls, [])

    def test_account_mutation_text_and_config_override(self):
        other = self.path.with_name('other.json')
        status, _ = self.invoke('account', 'add', 'personal', '--host', 'host', '--user', 'me',
                                '--password-env', 'PASSWORD', '--config', str(other))
        self.assertEqual(status, 0)
        status, result = self.invoke('--config', str(other), 'account', 'update', 'personal', '--user', 'new', '--text', text=True)
        self.assertEqual(status, 0)
        self.assertIn('DRY RUN: update account personal', result)
        self.assertIn('Changed fields: user', result)
        status, result = self.invoke('account', 'update', 'personal', '--user', 'new', '--confirm', '--text', '--config', str(other), text=True)
        self.assertEqual(status, 0)
        self.assertIn('APPLIED: update account personal', result)
        status, result = self.invoke('account', 'update', 'personal', '--user', 'new', '--confirm', '--text', '--config', str(other), text=True)
        self.assertEqual(status, 0)
        self.assertIn('NO CHANGES', result)
        status, result = self.invoke('account', 'remove', 'personal', '--text', '--config', str(other), text=True)
        self.assertEqual(status, 0)
        self.assertIn('DRY RUN: remove account personal', result)
        self.assertIn('Default account: (none)', result)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.fake.calls, [])

    def test_interactive_picker_names_are_terminal_safe(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        self.invoke('account', 'add', 'name\u202e', '--host', 'host', '--user', 'user', '--password-env', 'PASSWORD')
        output, terminal = io.StringIO(), TTY()
        with patch('sys.stdin', TTY('q\n')), contextlib.redirect_stdout(output), contextlib.redirect_stderr(terminal):
            status = main(['account', 'remove'])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())['data']['cancelled'])
        self.assertNotIn('\u202e', terminal.getvalue())
        self.assertIn(r'\u202e', terminal.getvalue())
        self.assertEqual(self.fake.calls, [])

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
