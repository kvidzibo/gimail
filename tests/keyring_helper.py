"""Disposable secret-tool stand-in. Never delegates to the real keyring."""
import shlex
import sys


def make_secret_tool(directory, body):
    directory.mkdir(parents=True, exist_ok=True)
    helper = directory / 'secret-tool'
    # An exec-only shim supports interpreter paths with spaces and has no child
    # left behind when the application kills a timed-out helper.
    helper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c '
                      + shlex.quote(body) + ' "$@"\n', encoding='utf-8')
    helper.chmod(0o700)
    return helper
