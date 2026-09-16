import json
import select
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gimail.accounts import (
    Account, add_account, config_lock, load_config, remove_account,
    save_config, update_account,
)
from gimail.errors import GimailError


class AccountMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'accounts.json'
        self.personal = Account('personal', 'imap.gmail.com', 993, 'me@gmail.com', password='old-private-secret')
        self.work = Account('work', 'imap.example.org', 143, 'me@example.org', 'starttls', password_env='WORK_PASSWORD')
        save_config(self.path, [self.personal, self.work], 'personal')

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns, set(self.path.parent.iterdir())

    def test_update_preview_preserves_config_and_never_exposes_credentials(self):
        before = self.snapshot()
        with patch.object(Account, 'secret', side_effect=AssertionError('must not resolve credentials')):
            result = update_account(self.path, 'personal', {'password_env': 'GMAIL_APP_PASSWORD'})
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['changed_fields'], ['password', 'password_env'])
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('old-private-secret', json.dumps(result))

    def test_update_preserves_unspecified_fields_other_accounts_and_order(self):
        result = update_account(self.path, 'work', {'host': 'new.example.org'}, confirm=True)
        accounts, default = load_config(self.path)
        self.assertEqual(accounts[0], self.personal)
        self.assertEqual(accounts[1].record(), {**self.work.record(), 'host': 'new.example.org'})
        self.assertEqual(default, 'personal')
        self.assertEqual(result['changed_fields'], ['host'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_credentials_can_switch_both_directions(self):
        update_account(self.path, 'personal', {'password_env': 'UNSET_TEST_PASSWORD'}, confirm=True)
        saved = json.loads(self.path.read_text())['accounts'][0]
        self.assertNotIn('password', saved)
        self.assertEqual(saved['password_env'], 'UNSET_TEST_PASSWORD')
        result = update_account(self.path, 'personal', {'password': 'new-private-secret'}, confirm=True)
        saved = json.loads(self.path.read_text())['accounts'][0]
        self.assertNotIn('password_env', saved)
        self.assertEqual(saved['password'], 'new-private-secret')
        self.assertNotIn('new-private-secret', json.dumps(result))

    def test_switching_between_all_three_sources_preserves_other_settings(self):
        sources = [{'password': 'new-private-secret'}, {'password_env': 'MAIL_PASSWORD'}, {'password_keyring': True}]
        keys = {'password', 'password_env', 'password_keyring'}
        fields = {key: value for key, value in self.personal.record().items() if key not in keys}
        for old in sources:
            for new in sources:
                if old == new:
                    continue
                with self.subTest(old=list(old), new=list(new)):
                    saved = Account.from_record({**fields, **old})
                    save_config(self.path, [saved, self.work], 'personal')
                    before = self.path.read_bytes()
                    with patch.object(Account, 'secret', side_effect=AssertionError('metadata must not retrieve passwords')):
                        preview = update_account(self.path, 'personal', new)
                        self.assertTrue(preview['dry_run'])
                        self.assertEqual(before, self.path.read_bytes())
                        result = update_account(self.path, 'personal', new, confirm=True)
                    accounts, default = load_config(self.path)
                    self.assertEqual(accounts[0].record(), {**fields, **new})
                    self.assertEqual(accounts[1], self.work)
                    self.assertEqual(default, 'personal')
                    self.assertNotIn('new-private-secret', json.dumps(result))

    def test_preview_does_not_echo_an_old_password_mistaken_for_an_env_name(self):
        account = Account('mistake', 'host', 993, 'user', password_env='thisWasReallyThePassword')
        save_config(self.path, [account], 'mistake')
        result = update_account(self.path, 'mistake', {'password_env': 'MAIL_PASSWORD'})
        self.assertNotIn('thisWasReallyThePassword', json.dumps(result))

    def test_update_default_only_and_idempotent_updates(self):
        preview = update_account(self.path, 'work', {}, make_default=True)
        self.assertEqual(preview['default_account'], 'work')
        self.assertEqual(load_config(self.path)[1], 'personal')
        update_account(self.path, 'work', {}, make_default=True, confirm=True)
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        result = update_account(self.path, 'work', {'host': self.work.host}, make_default=True, confirm=True)
        self.assertEqual(result['changed_fields'], [])
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(load_config(self.path)[1], 'work')

    def test_security_changes_do_not_silently_change_port(self):
        update_account(self.path, 'personal', {'security': 'starttls'}, confirm=True)
        self.assertEqual(load_config(self.path)[0][0].port, 993)
        update_account(self.path, 'personal', {'port': 143}, confirm=True)
        self.assertEqual(load_config(self.path)[0][0].port, 143)

    def test_invalid_updates_never_change_config(self):
        before = self.path.read_bytes()
        cases = [{}, {'name': 'renamed'}, {'port': 65536}, {'host': 'bad\r\nvalue'},
                 {'password_env': 'not-a-variable'}, {'password': ''},
                 {'password': 'private-secret', 'password_env': 'PASSWORD'},
                 {'password_keyring': True, 'password_env': 'PASSWORD'},
                 {'password_keyring': True, 'password': 'private-secret'},
                 {'password_keyring': 'true'}, {'password_keyring': False}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(GimailError):
                update_account(self.path, 'personal', changes, confirm=True)
            self.assertEqual(self.path.read_bytes(), before)
        # Errors must release the writer lock.
        update_account(self.path, 'personal', {'host': 'still-works.example.org'}, confirm=True)

    def test_missing_accounts_return_not_found(self):
        before = self.path.read_bytes()
        for action in (lambda: update_account(self.path, 'missing', {'user': 'new'}, confirm=True),
                       lambda: remove_account(self.path, 'missing', confirm=True)):
            with self.assertRaises(GimailError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'not_found')
            self.assertEqual(self.path.read_bytes(), before)

    def test_remove_preview_is_read_only(self):
        before = self.snapshot()
        result = remove_account(self.path, 'personal')
        self.assertEqual(result['default_account'], 'work')
        self.assertEqual(result['remaining_accounts'], 1)
        self.assertTrue(result['dry_run'])
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('old-private-secret', json.dumps(result))

    def test_remove_nondefault_preserves_default_and_remaining_account(self):
        remove_account(self.path, 'work', confirm=True)
        self.assertEqual(load_config(self.path), ([self.personal], 'personal'))

    def test_remove_default_promotes_first_remaining_and_last_clears_default(self):
        remove_account(self.path, 'personal', confirm=True)
        self.assertEqual(load_config(self.path), ([self.work], 'work'))
        result = remove_account(self.path, 'work', confirm=True)
        self.assertEqual(load_config(self.path), ([], None))
        self.assertIsNone(result['default_account'])
        self.assertEqual(result['remaining_accounts'], 0)
        self.assertNotIn('default_account', json.loads(self.path.read_text()))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        # The user's remove-then-add workflow must work.
        add_account(self.path, self.personal)
        self.assertEqual(load_config(self.path), ([self.personal], 'personal'))

    def test_implicit_default_is_valid_after_remove(self):
        save_config(self.path, [self.personal, self.work], None)
        result = remove_account(self.path, 'personal', confirm=True)
        self.assertEqual(result['default_account'], 'work')
        self.assertEqual(load_config(self.path), ([self.work], None))

    def test_changed_picker_target_is_rejected(self):
        selected = load_config(self.path)[0][1]
        update_account(self.path, 'work', {'host': 'changed.example.org'}, confirm=True)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(GimailError, 'changed while choosing'):
            remove_account(self.path, 'work', confirm=True, expected_account=selected)
        self.assertEqual(self.path.read_bytes(), before)

    def test_reordered_picker_target_is_removed_by_identity_not_index(self):
        selected = load_config(self.path)[0][1]
        save_config(self.path, [self.work, self.personal], 'personal')
        remove_account(self.path, 'work', confirm=True, expected_account=selected)
        self.assertEqual(load_config(self.path), ([self.personal], 'personal'))

    def test_all_writers_fail_cleanly_if_config_is_busy(self):
        before = self.path.read_bytes()
        with config_lock(self.path):
            actions = [lambda: add_account(self.path, Account('new', 'host', 993, 'me', password_env='PASSWORD')),
                       lambda: update_account(self.path, 'work', {'user': 'changed'}, confirm=True),
                       lambda: remove_account(self.path, 'work', confirm=True)]
            for action in actions:
                with self.assertRaisesRegex(GimailError, 'busy'):
                    action()
                self.assertEqual(self.path.read_bytes(), before)
            self.assertTrue(update_account(self.path, 'work', {'user': 'changed'})['dry_run'])
            self.assertTrue(remove_account(self.path, 'work')['dry_run'])
        remove_account(self.path, 'work', confirm=True)

    def test_writer_lock_is_exclusive_across_processes(self):
        code = '''
import sys
from pathlib import Path
from gimail.accounts import config_lock
with config_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.read(1)
'''
        before = self.path.read_bytes()
        with subprocess.Popen([sys.executable, '-c', code, str(self.path)],
                              cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as writer:
            try:
                self.assertTrue(select.select([writer.stdout], [], [], 8)[0], 'Writer did not become ready')
                self.assertEqual(writer.stdout.readline(), 'locked\n')
                for action in (lambda: update_account(self.path, 'work', {'user': 'new'}, confirm=True),
                               lambda: remove_account(self.path, 'work', confirm=True),
                               lambda: add_account(self.path, Account('new', 'host', 993, 'me', password_env='PASSWORD'))):
                    with self.assertRaisesRegex(GimailError, 'busy'):
                        action()
                self.assertEqual(before, self.path.read_bytes())
            finally:
                try:
                    writer.communicate(input='x', timeout=8)
                except subprocess.TimeoutExpired:
                    writer.kill()
                    writer.communicate()
            self.assertEqual(writer.returncode, 0)
        remove_account(self.path, 'work', confirm=True)

    def test_private_regular_lock_is_required(self):
        lock = self.path.with_name('accounts.json.lock')
        before = self.path.read_bytes()
        lock.symlink_to(self.path)
        with self.assertRaises(GimailError):
            remove_account(self.path, 'work', confirm=True)
        lock.unlink()
        lock.write_text('')
        lock.chmod(0o644)
        with self.assertRaises(GimailError):
            update_account(self.path, 'work', {'user': 'new'}, confirm=True)
        self.assertEqual(before, self.path.read_bytes())

    def test_failed_atomic_write_preserves_config_and_cleans_temporary_file(self):
        before = self.path.read_bytes()
        with patch('gimail.accounts.os.replace', side_effect=OSError('private-secret')):
            with self.assertRaises(GimailError) as caught:
                update_account(self.path, 'personal', {'user': 'new'}, confirm=True)
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual({file.name for file in self.path.parent.iterdir()}, {'accounts.json', 'accounts.json.lock'})
        update_account(self.path, 'personal', {'user': 'new'}, confirm=True)

    def test_preview_missing_config_creates_nothing(self):
        missing = Path(self.temp.name) / 'absent' / 'accounts.json'
        for action in (lambda: update_account(missing, 'missing', {'user': 'new'}),
                       lambda: remove_account(missing, 'missing')):
            with self.assertRaises(GimailError):
                action()
            self.assertFalse(missing.parent.exists())
