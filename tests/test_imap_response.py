import unittest

from gimail.errors import GimailError
from gimail.imap_response import fetch_metadata


class FetchMetadataTests(unittest.TestCase):
    def test_only_top_level_attributes_are_used(self):
        data = fetch_metadata(
            b'2 (ENVELOPE ("UID 999" (UID 888) "FLAGS (fake)") '
            b'FLAGS (UID 42 RFC822.SIZE 999 \\Seen) RFC822.SIZE 456 UID 7)'
        )
        self.assertEqual(data['uid'], 7)
        self.assertEqual(data['size'], 456)
        self.assertEqual(data['flags'], ['UID', '42', 'RFC822.SIZE', '999', r'\Seen'])

    def test_body_section_literals_and_quoted_extensions(self):
        data = fetch_metadata(
            b'2 (BODY[HEADER.FIELDS (DATE FROM SUBJECT)] {100} '
            b'X-EXT "escaped \\\" quote and \\\\ slash" FLAGS () UID 7 RFC822.SIZE 0)'
        )
        self.assertEqual(data, {'uid': 7, 'size': 0, 'flags': []})

    def test_attribute_names_are_case_insensitive(self):
        self.assertEqual(fetch_metadata(b'2 (uid 7 flags (\\seen) rfc822.size 4)'),
                         {'uid': 7, 'size': 4, 'flags': [r'\seen']})

    def test_flag_atoms_may_contain_square_brackets(self):
        data = fetch_metadata(b'2 (FLAGS ([open close] BODY[odd) UID 7)')
        self.assertEqual(data['flags'], ['[open', 'close]', 'BODY[odd'])

    def test_unsolicited_metadata_can_omit_fields(self):
        self.assertEqual(fetch_metadata(b'2 (FLAGS ())'), {'uid': None, 'size': None, 'flags': []})
        self.assertEqual(fetch_metadata(b'2 (UID 7)'), {'uid': 7, 'size': None, 'flags': None})

    def test_malformed_metadata_is_rejected_without_echoing_data(self):
        cases = [
            None, b'not a response', b'2 (UID 7', b'2 (UID 7))', b'2 (UID)',
            b'2 (UID "secret-value")', b'2 (UID 7 UID 8)', b'2 (UID 0)',
            b'2 (UID 4294967296)', b'2 (UID 999999999999999999999999)',
            b'2 (RFC822.SIZE -1 UID 7)', b'2 (FLAGS NIL UID 7)',
            b'2 (FLAGS ((nested)) UID 7)', b'2 (BODY[missing {0} UID 7)',
            b'2 (X-EXT "unterminated secret-value)', b'2 (X-EXT "escape\\)',
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(GimailError) as caught:
                fetch_metadata(response)
            self.assertEqual(caught.exception.code, 'imap_error')
            self.assertNotIn('secret-value', str(caught.exception))

    def test_deep_extension_values_do_not_recurse(self):
        data = fetch_metadata(b'2 (X-EXT ' + b'(' * 1200 + b'NIL' + b')' * 1200 + b' UID 7 FLAGS ())')
        self.assertEqual(data['uid'], 7)
