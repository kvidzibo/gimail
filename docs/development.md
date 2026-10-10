# Development and tests

[Quickstart](../README.md) · [Usage](usage.md)

## Development

Python **runtime** dependencies: none. Packaging uses `uv_build` as a build-only backend (bundled with compatible uv versions), not as an application dependency. The optional `--keyring` backend still needs the system `secret-tool` executable and an accessible Secret Service keyring.

From the clone, the preferred development workflow is:

```sh
uv sync --locked
uv run --locked gimail --help
xvfb-run -a uv run --locked python -W error -m unittest discover -v
uv build
xvfb-run -a uv run --locked python -W error -m tests.package_smoke
```

The packaging check builds wheels and an sdist, installs each with `uv tool install` into temporary directories, and exercises the installed command outside the checkout. It verifies metadata/version consistency, no Python runtime dependencies, credential/log-file exclusions, and IMAP/keyring behavior using only fixtures. Add `--git-ref HEAD` after committing to also test installation through a local Git URL; CI checks all three install sources. It does not replace your installed tools or touch real mail/keyrings. Keep real configs and secrets outside the package directory.

`pyproject.toml` defines the `gimail = gimail.cli:main` entry point and flat package layout. `uv.lock` records the project with no runtime dependencies. Keep the project version in `pyproject.toml` aligned with `gimail/__init__.py`.

For core tests without uv or installation:

```sh
python3 -m venv --without-pip .venv
xvfb-run -a .venv/bin/python -W error -m unittest discover -v
```

The tests use fake IMAP/SMTP connections, a loopback IMAP test server with real `imaplib`/CLI subprocesses, and pseudo-terminals for the numbered picker. Picker tests exercise stdout/stderr separation, cancellation, and changes to the config while the user is choosing. CI runs terminal UI tests under `xvfb-run`; no display is required by the CLI itself. Keyring tests use disposable fake `secret-tool` executables and an inaccessible test D-Bus address, never the real desktop keyring. They verify internal credential retrieval through real CLI/IMAP subprocesses, timeout cleanup, and output secrecy. Tests need no mail account, keyring, secrets, or internet and do not touch your real config. CI runs the same suite on Python 3.9, 3.11, and 3.14. Live Gmail/generic-server access is not part of the offline suite; use `account test` with your own credentials.

Code lives in `gimail/`: `accounts.py` (private config/credential selection), `keyring.py` (optional secret-tool lookup), `imap_client.py` (IMAP/MIME), `smtp_client.py` (safe message composition and SMTP submission), `drafts.py` (Drafts discovery and IMAP saving), `imap_response.py` (structured FETCH metadata), and `cli.py` (arguments/output). `gimail.py` and `gimail/__main__.py` are entry points. Contributions should include focused stdlib `unittest` coverage, especially for changes to mutation safety.
