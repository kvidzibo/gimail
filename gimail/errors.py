"""Only deliberately safe messages cross the CLI's error boundary."""


class GimailError(Exception):
    def __init__(self, message, code="imap_error", exit_status=1):
        super().__init__(message)
        self.code = code
        self.exit_status = exit_status
