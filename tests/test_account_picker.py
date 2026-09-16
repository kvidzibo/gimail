"""Real CLI/terminal tests; all account data and credentials are disposable fixtures."""
import errno
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from gimail.accounts import Account, load_config, save_config, update_account


ROOT = Path(__file__).resolve().parents[1]
SECRET = 'picker-fixture-not-a-real-password'


class AccountPickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'accounts.json'
        self.personal = Account('personal', 'imap.gmail.com', 993, 'personal', password=SECRET)
        self.work = Account('work', 'imap.example.org', 993, 'work', password_env='WORK_PASSWORD')
        save_config(self.path, [self.personal, self.work], 'personal')
        self.env = {
            'PATH': os.environ.get('PATH', ''), 'HOME': self.temp.name, 'LANG': 'C.UTF-8',
            'GIMAIL_CONFIG': str(self.path), 'GIMAIL_ACCOUNT': 'personal',
            'GIMAIL_HOST': 'not-used.invalid', 'GIMAIL_USER': 'not-used', 'GIMAIL_PASSWORD': SECRET,
        }

    def picker(self, reply=b'2\n', confirm=False, while_waiting=None, interrupt=False, text=False):
        master, slave = pty.openpty()
        process = None
        transcript = bytearray()
        try:
            command = [sys.executable, str(ROOT / 'gimail.py'), 'account', 'remove']
            if confirm:
                command.append('--confirm')
            if text:
                command.append('--text')
            process = subprocess.Popen(command, stdin=slave, stderr=slave, stdout=subprocess.PIPE,
                                       env=self.env, cwd=self.temp.name)
            os.close(slave)
            slave = None
            deadline = time.monotonic() + 8
            while b'Choose index' not in transcript:
                if time.monotonic() > deadline:
                    self.fail('Timed out waiting for numbered account picker')
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 8192)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        chunk = b''
                    if not chunk:
                        self.fail('CLI exited before showing the numbered picker')
                    transcript.extend(chunk)
            if while_waiting is not None:
                while_waiting()
            if interrupt:
                process.send_signal(signal.SIGINT)
            else:
                os.write(master, reply)
            output, _ = process.communicate(timeout=8)
            while select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 8192)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                transcript.extend(chunk)
            self.assertNotIn(SECRET.encode(), output)
            self.assertNotIn(SECRET.encode(), transcript)
            result = output.decode() if text else json.loads(output)
            return process.returncode, result, transcript.decode('utf-8', 'replace')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate()
            if slave is not None:
                os.close(slave)
            os.close(master)

    def test_bare_remove_shows_numbered_list_and_previews_chosen_index(self):
        before = self.path.read_bytes()
        status, result, terminal = self.picker()
        self.assertEqual(status, 0)
        self.assertIn('1) personal (default)', terminal)
        self.assertIn('2) work', terminal)
        self.assertEqual(result['data']['account'], 'work')
        self.assertTrue(result['data']['dry_run'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name('accounts.json.lock').exists())

    def test_confirmed_picker_removes_selected_profile_and_repairs_default(self):
        status, result, _ = self.picker(reply=b'1\n', confirm=True)
        self.assertEqual(status, 0)
        self.assertFalse(result['data']['dry_run'])
        self.assertEqual(result['data']['account'], 'personal')
        self.assertEqual(result['data']['default_account'], 'work')
        self.assertEqual(load_config(self.path), ([self.work], 'work'))

    def test_cancel_enter_zero_q_and_eof_leave_config_unchanged(self):
        before = self.path.read_bytes()
        for reply in (b'\n', b'0\n', b'q\n', b'\x04'):
            with self.subTest(reply=reply):
                status, result, _ = self.picker(reply=reply, confirm=True)
                self.assertEqual(status, 0)
                self.assertTrue(result['data']['cancelled'])
                self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name('accounts.json.lock').exists())

    def test_invalid_index_returns_json_error_without_removing_anything(self):
        before = self.path.read_bytes()
        for reply in (b'99\n', b'-1\n', b'1,2\n', b'not-an-index\n', '２\n'.encode()):
            with self.subTest(reply=reply):
                status, result, _ = self.picker(reply=reply, confirm=True)
                self.assertEqual(status, 2)
                self.assertEqual(result['code'], 'imap_error')
                self.assertIn('Invalid account index', result['error'])
                self.assertEqual(self.path.read_bytes(), before)

    def test_ctrl_c_cancels_without_config_changes(self):
        before = self.path.read_bytes()
        status, result, _ = self.picker(confirm=True, interrupt=True)
        self.assertEqual(status, 130)
        self.assertFalse(result['ok'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_picker_does_not_lock_while_waiting_and_rejects_changed_target(self):
        def concurrent_update():
            update_account(self.path, 'work', {'host': 'changed.example.org'}, confirm=True)
        status, result, _ = self.picker(confirm=True, while_waiting=concurrent_update)
        self.assertEqual(status, 1)
        self.assertEqual(result['code'], 'imap_error')
        self.assertIn('changed while choosing', result['error'])
        accounts, _ = load_config(self.path)
        self.assertEqual([account.name for account in accounts], ['personal', 'work'])
        self.assertEqual(accounts[1].host, 'changed.example.org')

    def test_picker_keeps_displayed_identity_when_order_changes(self):
        def reorder():
            save_config(self.path, [self.work, self.personal], 'personal')
        status, result, _ = self.picker(confirm=True, while_waiting=reorder)
        self.assertEqual(status, 0)
        self.assertEqual(result['data']['account'], 'work')
        self.assertEqual(load_config(self.path), ([self.personal], 'personal'))

    def test_text_picker_and_cancellation(self):
        status, text, _ = self.picker(text=True)
        self.assertEqual(status, 0)
        self.assertIn('DRY RUN: remove account work', text)
        status, text, _ = self.picker(text=True, reply=b'q\n')
        self.assertEqual(status, 0)
        self.assertEqual(text.strip(), 'Cancelled. No config changes.')
