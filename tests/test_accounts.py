import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gimail.accounts import Account, add_account, config_path, environment_account, load_config, select_account
from gimail.errors import GimailError


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'gimail' / 'accounts.json'
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.account = Account.from_record(dict(name='work', host='imap.example.org', user='me', password_env='MAIL_PASSWORD'))

    def raw(self, value, mode=0o600):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(value), encoding='utf-8')
        self.path.chmod(mode)

    def test_atomic_private_add_and_no_secret_in_public_record(self):
        os.environ['MAIL_PASSWORD'] = 'super-secret'
        public = add_account(self.path, self.account)
        self.assertNotIn('super-secret', json.dumps(public))
        self.assertNotIn('super-secret', self.path.read_text())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        accounts, default = load_config(self.path)
        self.assertEqual(default, 'work')
        self.assertEqual(accounts[0].secret(), 'super-secret')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_duplicate_does_not_overwrite(self):
        add_account(self.path, self.account)
        before = self.path.read_bytes()
        with self.assertRaises(GimailError):
            add_account(self.path, self.account)
        self.assertEqual(before, self.path.read_bytes())

    def test_plaintext_is_not_in_repr_or_public(self):
        account = Account.from_record(dict(name='x', host='example.org', user='x', password='very-secret'))
        self.assertNotIn('very-secret', repr(account))
        self.assertNotIn('very-secret', json.dumps(account.public()))
        add_account(self.path, account)
        self.assertEqual(load_config(self.path)[0][0].secret(), 'very-secret')

    def test_insecure_permissions_are_rejected(self):
        self.raw({'accounts': [self.account.record()]}, 0o644)
        with self.assertRaisesRegex(GimailError, 'chmod 600'):
            load_config(self.path)

    def test_symlink_is_rejected(self):
        self.raw({'accounts': []})
        link = self.path.with_name('link.json')
        link.symlink_to(self.path)
        with self.assertRaises(GimailError):
            load_config(link)

    def test_invalid_config_shapes(self):
        cases = [[], {}, {'accounts': {}}, {'accounts': [None]},
                 {'accounts': [self.account.record()] * 2}, {'accounts': [], 'default_account': 'missing'}]
        for raw in cases:
            with self.subTest(raw=raw):
                self.raw(raw)
                with self.assertRaises(GimailError):
                    load_config(self.path)

    def test_malformed_json_does_not_echo_contents(self):
        self.raw({})
        self.path.write_text('not-json-secret', encoding='utf-8')
        with self.assertRaises(GimailError) as caught:
            load_config(self.path)
        self.assertNotIn('not-json-secret', str(caught.exception))

    def test_account_validation(self):
        for update in ({'port': True}, {'port': 0}, {'port': 65536}, {'security': 'none'},
                       {'host': 'bad\r\nINJECT'}, {'password_env': 'BAD-NAME'},
                       {'password': 'secret'}, {'password_env': None}):
            with self.subTest(update=update), self.assertRaises(GimailError):
                Account.from_record({**self.account.record(), **update})

    def test_missing_password_does_not_fall_back(self):
        with self.assertRaises(GimailError) as caught:
            self.account.secret()
        self.assertEqual(caught.exception.code, 'auth_failed')

    def test_environment_generic_gmail_and_selection_precedence(self):
        add_account(self.path, self.account)
        os.environ.update(GIMAIL_HOST='mail.example.net', GIMAIL_USER='other', GIMAIL_PASSWORD='secret')
        self.assertEqual(select_account(self.path).host, 'mail.example.net')
        self.assertEqual(select_account(self.path, 'work').name, 'work')
        os.environ['GIMAIL_ACCOUNT'] = 'work'
        self.assertEqual(select_account(self.path).name, 'work')
        del os.environ['GIMAIL_ACCOUNT']
        del os.environ['GIMAIL_HOST']
        os.environ['GIMAIL_PRESET'] = 'gmail'
        gmail = environment_account()
        self.assertEqual((gmail.host, gmail.port, gmail.security), ('imap.gmail.com', 993, 'ssl'))

    def test_environment_custom_password_variable_and_starttls(self):
        os.environ.update(GIMAIL_HOST='host', GIMAIL_USER='user', GIMAIL_SECURITY='starttls',
                          GIMAIL_PASSWORD_ENV='MY_PASSWORD', MY_PASSWORD='secret')
        self.assertEqual(environment_account().port, 143)
        self.assertEqual(environment_account().secret(), 'secret')
        os.environ['GIMAIL_PORT'] = 'bad-secret'
        with self.assertRaises(GimailError) as caught:
            environment_account()
        self.assertNotIn('bad-secret', str(caught.exception))

    def test_default_and_not_found(self):
        add_account(self.path, self.account)
        other = Account.from_record({**self.account.record(), 'name': 'other'})
        add_account(self.path, other, make_default=True)
        self.assertEqual(select_account(self.path).name, 'other')
        with self.assertRaises(GimailError) as caught:
            select_account(self.path, 'missing')
        self.assertEqual(caught.exception.code, 'not_found')

    def test_config_path_overrides(self):
        os.environ['XDG_CONFIG_HOME'] = '/tmp/xdg-example'
        self.assertEqual(config_path(), Path('/tmp/xdg-example/gimail/accounts.json'))
        os.environ['GIMAIL_CONFIG'] = '/tmp/custom-example.json'
        self.assertEqual(config_path(), Path('/tmp/custom-example.json'))
        self.assertEqual(config_path('/tmp/explicit-example.json'), Path('/tmp/explicit-example.json'))
