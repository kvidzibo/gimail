import imaplib
import os
import ssl
import unittest
from email.message import EmailMessage
from unittest.mock import patch

from gimail.accounts import Account
from gimail.errors import GimailError
from gimail.imap_client import MailClient, mailbox, message_data, quote
from tests.helpers import FakeIMAP, HEADERS


class ImapTests(unittest.TestCase):
    def setUp(self):
        self.account = Account('work', 'imap.example.org', 993, 'me', password='secret-value')
        self.fake = FakeIMAP()
        self.factory = patch('gimail.imap_client.imaplib.IMAP4_SSL', return_value=self.fake)
        self.mock_factory = self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_tls_verified_and_logout(self):
        with MailClient(self.account) as client:
            context = client.select()
            self.assertEqual(context['uidvalidity'], 1234)
        kwargs = self.mock_factory.call_args.kwargs
        self.assertTrue(kwargs['ssl_context'].check_hostname)
        self.assertEqual(kwargs['ssl_context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(kwargs['timeout'], 30)
        self.assertIn(('SELECT', '"INBOX"', True), self.fake.calls)
        self.assertEqual(self.fake.calls[-1], ('LOGOUT',))

    def test_starttls_precedes_login_and_verifies_certificates(self):
        self.account.security = 'starttls'
        with patch('gimail.imap_client.imaplib.IMAP4', return_value=self.fake):
            with MailClient(self.account):
                pass
        self.assertEqual(self.fake.calls[0][0], 'STARTTLS')
        self.assertTrue(self.fake.calls[0][1].check_hostname)
        self.assertEqual(self.fake.calls[1][0], 'LOGIN')

    def test_starttls_failure_sends_no_credentials(self):
        self.account.security = 'starttls'
        self.fake.failure = 'STARTTLS'
        # Keep the real IMAP4 error classes when patching the constructor.
        with patch('gimail.imap_client.imaplib.IMAP4', return_value=self.fake):
            with self.assertRaises(GimailError):
                with MailClient(self.account):
                    pass
        self.assertFalse(any(call[0] == 'LOGIN' for call in self.fake.calls))

    def test_auth_failure_does_not_echo_secret_and_cleans_up(self):
        self.fake.failure = 'LOGIN'
        with self.assertRaises(GimailError) as caught:
            with MailClient(self.account):
                pass
        self.assertEqual(caught.exception.code, 'auth_failed')
        self.assertNotIn('secret-value', str(caught.exception))
        self.assertEqual(self.fake.calls[-1], ('LOGOUT',))

    def test_connection_error_is_sanitized(self):
        self.mock_factory.side_effect = OSError('secret-value')
        with self.assertRaises(GimailError) as caught:
            with MailClient(self.account):
                pass
        self.assertNotIn('secret-value', str(caught.exception))
        self.assertEqual(caught.exception.code, 'imap_error')

    def test_uid_search_limit_sort_and_peek(self):
        with MailClient(self.account) as client:
            client.select()
            data = client.search(unread=True, limit=2)
        self.assertEqual([item['uid'] for item in data['messages']], [99, 42])
        self.assertEqual(data['count'], 2)
        self.assertIn(('SEARCH', b'UNSEEN'), self.fake.calls)
        self.assertTrue(data['messages'][0]['unread'])
        self.assertFalse(data['messages'][1]['unread'])
        self.assertEqual(data['messages'][0]['subject'], 'Hello ✓')
        fetches = [call for call in self.fake.calls if call[0] == 'FETCH']
        self.assertEqual(len(fetches), 2)
        self.assertTrue(all('BODY.PEEK[' in call[2] for call in fetches))

    def test_search_empty_response(self):
        with MailClient(self.account) as client:
            client.select()
            for response in ([b''], [None], []):
                with self.subTest(response=response), patch.object(self.fake, 'uid', return_value=('OK', response)):
                    self.assertEqual(client.search()['messages'], [])

    def test_search_malformed_response_is_not_silent_empty_success(self):
        with MailClient(self.account) as client:
            client.select()
            for response in ([b'bad'], [b'0'], [b'4294967296'], [b'1:*'], ['7'], [(b'7', b'body')], b'7', None):
                with self.subTest(response=response), patch.object(self.fake, 'uid', return_value=('OK', response)):
                    with self.assertRaises(GimailError) as caught:
                        client.search()
                    self.assertEqual(caught.exception.code, 'imap_error')

    def test_search_skips_vanished_messages(self):
        del self.fake.flags[99]
        with MailClient(self.account) as client:
            client.select()
            self.assertEqual([message['uid'] for message in client.search()['messages']], [42, 7])

    def test_non_ascii_credentials_use_sasl_plain(self):
        self.account.user = 'mé@example.org'
        self.account.password = 'sëcret'
        with MailClient(self.account):
            pass
        self.assertIn(('AUTHENTICATE', 'PLAIN', '\0mé@example.org\0sëcret'.encode()), self.fake.calls)
        self.assertFalse(any(call[0] == 'LOGIN' for call in self.fake.calls))

    def test_structured_search_quoting_unicode_and_raw_query(self):
        with MailClient(self.account) as client:
            client.select()
            client.search(sender='a"b\\c', subject='café', query='SINCE 01-Jan-2026', limit=1)
        call = next(call for call in self.fake.calls if call[0] == 'SEARCH')
        self.assertEqual(call, ('SEARCH', 'CHARSET', 'UTF-8', b'FROM', b'"a\\"b\\\\c"',
                                b'SUBJECT', '"café"'.encode(), b'SINCE 01-Jan-2026'))

    def test_query_injection_is_rejected(self):
        with MailClient(self.account) as client:
            client.select()
            for query in ('ALL\r\nLOGOUT', 'ALL\x00', ''):
                with self.subTest(query=query), self.assertRaises(GimailError):
                    client.search(query=query)
        self.assertFalse(any(call[0] == 'SEARCH' for call in self.fake.calls))

    def test_show_body_and_not_found(self):
        with MailClient(self.account) as client:
            client.select()
            self.assertIn('Hello body.', client.fetch(7, include_body=True)['text'])
            with self.assertRaises(GimailError) as caught:
                client.fetch(123)
            self.assertEqual(caught.exception.code, 'not_found')
        self.assertIn(('FETCH', '7', '(UID FLAGS RFC822.SIZE BODY.PEEK[])'), self.fake.calls)

    def test_fetch_uid_and_flags_after_literal_with_unsolicited_updates(self):
        # FETCH attributes may arrive in any order; UID need not precede BODY.
        response = ('OK', [
            b'1 (UID 99 FLAGS (\\Seen))',
            (b'2 (BODY[HEADER.FIELDS (SUBJECT)] {10}', HEADERS),
            b' UID 7 FLAGS (\\Seen) RFC822.SIZE 456)',
            b'3 (UID 42 FLAGS ())',
        ])
        with MailClient(self.account) as client:
            client.select()
            with patch.object(self.fake, 'uid', return_value=response):
                data = client.fetch(7)
        self.assertEqual(data['uid'], 7)
        self.assertFalse(data['unread'])
        self.assertEqual(data['size'], 456)

    def test_folder_missing(self):
        with MailClient(self.account) as client:
            with self.assertRaises(GimailError) as caught:
                client.select('Missing')
        self.assertEqual(caught.exception.code, 'not_found')

    def test_mutations_preview_and_single_uid_only(self):
        with MailClient(self.account) as client:
            client.select()
            for action in ('mark', 'move', 'delete'):
                with self.subTest(action=action):
                    result = client.mutate(action, 7, read=True, destination='Other Folder')
                    self.assertTrue(result['dry_run'])
                    self.assertIn('--confirm', result['hint'])
        self.assertFalse(any(call[0] in ('STORE', 'MOVE', 'COPY', 'EXPUNGE') for call in self.fake.calls))

    def test_mark_and_soft_delete_never_expunge(self):
        with MailClient(self.account) as client:
            client.select(readonly=False)
            client.mutate('mark', 7, confirm=True, read=True)
            self.assertIn(r'\Seen', self.fake.flags[7])
            client.mutate('mark', 7, confirm=True, read=False)
            self.assertNotIn(r'\Seen', self.fake.flags[7])
            result = client.mutate('delete', 7, confirm=True)
        self.assertFalse(result['expunge_requested'])
        self.assertIn(r'\Deleted', self.fake.flags[7])
        self.assertFalse(any(call[0] == 'EXPUNGE' for call in self.fake.calls))

    def test_native_move(self):
        with MailClient(self.account) as client:
            client.select(readonly=False)
            result = client.mutate('move', 7, confirm=True, destination='Archive Stuff')
        self.assertTrue(result['expunge_requested'])
        self.assertIn(('MOVE', '7', '"Archive Stuff"'), self.fake.calls)
        self.assertFalse(any(call[0] in ('COPY', 'STORE', 'EXPUNGE') for call in self.fake.calls))

    def test_move_fallback_scopes_expunge_with_uidplus(self):
        self.fake.advertised = b'IMAP4rev1 UIDPLUS'
        with MailClient(self.account) as client:
            client.select(readonly=False)
            result = client.mutate('move', 7, confirm=True, destination='Archive')
        operations = [call for call in self.fake.calls if call[0] in ('COPY', 'STORE', 'EXPUNGE')]
        self.assertEqual(operations, [('COPY', '7', '"Archive"'), ('STORE', '7', '+FLAGS', r'(\Deleted)'), ('EXPUNGE', '7')])
        self.assertTrue(result['expunge_requested'])

    def test_move_fallback_without_uidplus_does_not_expunge(self):
        self.fake.advertised = b'IMAP4rev1'
        with MailClient(self.account) as client:
            client.select(readonly=False)
            result = client.mutate('move', 7, confirm=True, destination='Archive')
        self.assertFalse(result['expunge_requested'])
        self.assertIn('not expunged', result['note'])
        self.assertFalse(any(call[0] == 'EXPUNGE' for call in self.fake.calls))

    def test_failed_copy_does_not_delete_source(self):
        self.fake.advertised = b'IMAP4rev1 UIDPLUS'
        self.fake.failure = 'COPY'
        with MailClient(self.account) as client:
            client.select(readonly=False)
            with self.assertRaises(GimailError):
                client.mutate('move', 7, confirm=True, destination='Missing')
        self.assertFalse(any(call[0] in ('STORE', 'EXPUNGE') for call in self.fake.calls))

    def test_partial_move_warns_about_already_copied_message(self):
        self.fake.advertised = b'IMAP4rev1 UIDPLUS'
        self.fake.failure = 'STORE'
        with MailClient(self.account) as client:
            client.select(readonly=False)
            with self.assertRaisesRegex(GimailError, 'copied'):
                client.mutate('move', 7, confirm=True, destination='Archive')
        self.assertFalse(any(call[0] == 'EXPUNGE' for call in self.fake.calls))

    def test_store_no_ack_does_not_report_success(self):
        self.fake.omit_store_ack = True
        with MailClient(self.account) as client:
            client.select(readonly=False)
            with self.assertRaisesRegex(GimailError, 'did not acknowledge'):
                client.mutate('mark', 7, confirm=True, read=True)

    def test_move_to_same_folder_rejected(self):
        with MailClient(self.account) as client:
            client.select()
            with self.assertRaises(GimailError):
                client.mutate('move', 7, confirm=True, destination='inbox')

    def test_mailbox_encoding_and_quoting(self):
        self.assertEqual(mailbox('A & B'), '"A &- B"')
        self.assertEqual(mailbox('台北'), '"&U,BTFw-"')
        self.assertEqual(mailbox('a"b\\c'), '"a\\"b\\\\c"')
        for value in ('bad\nmailbox', 'bad\x00mailbox'):
            with self.assertRaises(GimailError):
                mailbox(value)

    def test_mime_text_html_attachment_and_unknown_charset(self):
        msg = EmailMessage()
        msg['Subject'] = 'MIME ✓'
        msg.set_content('Plain ✓')
        msg.add_alternative('<p>HTML ✓</p>', subtype='html')
        msg.add_attachment(b'secret attachment bytes', maintype='application', subtype='octet-stream', filename='file.bin')
        data = message_data(7, b'2 (UID 7 FLAGS () RFC822.SIZE 900)', msg.as_bytes(), True)
        self.assertIn('Plain ✓', data['text'])
        self.assertIn('<p>HTML ✓</p>', data['html'])
        self.assertEqual(data['attachments'][0]['filename'], 'file.bin')
        self.assertNotIn('secret attachment bytes', data['text'])
        raw = HEADERS[:-2] + b'Content-Type: text/plain; charset=not-a-codec\r\n\r\nHi \xff'
        self.assertEqual(message_data(7, b'2 (UID 7 FLAGS ())', raw, True)['text'], 'Hi �')
