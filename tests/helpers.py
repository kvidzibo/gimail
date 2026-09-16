import imaplib


HEADERS = (
    b'From: Sender <sender@example.org>\r\nTo: me@example.org\r\n'
    b'Subject: =?utf-8?b?SGVsbG8g4pyT?=\r\n'
    b'Date: Tue, 15 Sep 2026 12:00:00 +0000\r\nMessage-ID: <one@example.org>\r\n\r\n'
)
BODY = HEADERS[:-2] + b'Content-Type: text/plain; charset=utf-8\r\n\r\nHello body.\r\n'


class FakeIMAP:
    def __init__(self, capabilities=b'IMAP4rev1 MOVE UIDPLUS'):
        self.advertised = capabilities
        self.calls = []
        self.flags = {7: [], 42: [r'\Seen'], 99: []}
        self.debug = 0
        self.failure = None
        self.omit_store_ack = False

    def login(self, user, password):
        self.calls.append(('LOGIN', user, password))
        if self.failure == 'LOGIN':
            raise imaplib.IMAP4.error('server echoed secret: ' + password)
        return 'OK', [b'authenticated']

    def authenticate(self, mechanism, callback):
        self.calls.append(('AUTHENTICATE', mechanism, callback(None)))
        return 'OK', [b'authenticated']

    def capability(self):
        return 'OK', [self.advertised]

    def starttls(self, ssl_context):
        self.calls.append(('STARTTLS', ssl_context))
        return ('NO' if self.failure == 'STARTTLS' else 'OK'), [b'tls']

    def select(self, folder, readonly=False):
        self.calls.append(('SELECT', folder, readonly))
        return ('NO' if folder == '"Missing"' else 'OK'), [b'3']

    def response(self, name):
        return name, [b'1234']

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if self.failure == command:
            return 'NO', [b'diagnostic intentionally suppressed']
        if command == 'SEARCH':
            return 'OK', [b'99 7 42']
        uid = int(args[0])
        if command == 'FETCH':
            if uid not in self.flags:
                return 'OK', [None]
            flags = ' '.join(self.flags[uid]).encode()
            literal = BODY if 'BODY.PEEK[]' in args[1] else HEADERS
            return 'OK', [
                (b'2 (UID ' + str(uid).encode() + b' FLAGS (' + flags + b') RFC822.SIZE 456 BODY[] {10}', literal),
                b')',
            ]
        if command == 'STORE':
            flag = args[2][1:-1]
            if args[1] == '+FLAGS' and flag not in self.flags[uid]:
                self.flags[uid].append(flag)
            elif args[1] == '-FLAGS' and flag in self.flags[uid]:
                self.flags[uid].remove(flag)
            if self.omit_store_ack:
                return 'OK', [None]
            return 'OK', [f'2 (UID {uid} FLAGS ({" ".join(self.flags[uid])}))'.encode()]
        if command in ('COPY', 'MOVE', 'EXPUNGE'):
            return 'OK', [b'done']
        raise AssertionError(command)

    def logout(self):
        self.calls.append(('LOGOUT',))
        return 'BYE', [b'bye']

    def shutdown(self):
        self.calls.append(('SHUTDOWN',))

    def close(self):
        raise AssertionError('CLOSE must never be used: it expunges unrelated messages')

    def expunge(self):
        raise AssertionError('Mailbox-wide EXPUNGE must never be used')
