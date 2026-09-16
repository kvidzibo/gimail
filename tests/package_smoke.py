"""Optional packaging checks: run under xvfb-run with a project-local Python.

Requires uv, but no Python test dependencies. All tool installs, caches, configs,
and credential fixtures live in a temporary directory, never the user's tools.
"""
import argparse
import configparser
import functools
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from gimail import __version__
from tests.test_wire import WireTests


ROOT = Path(__file__).resolve().parents[1]


def run(command, env, cwd, expected=0):
    result = subprocess.run(command, env=env, cwd=cwd, text=True, capture_output=True, timeout=120)
    if result.returncode != expected:
        raise AssertionError(f'Packaging command failed ({result.returncode}): {command!r}\n'
                             + result.stdout + result.stderr)
    return result


def copy_source(destination):
    # Copy only intentional source inputs, never .git, .venv, real configs or
    # other local files that might be present in a developer's checkout.
    files = ['pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'gimail.py', 'examples/accounts.json']
    files += [str(path.relative_to(ROOT)) for directory in ('gimail', 'tests')
              for path in (ROOT / directory).glob('*.py')]
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return {name for name in files if name.startswith('gimail/')}


def check_archives(wheel, sdist, modules, private_marker):
    with zipfile.ZipFile(wheel) as archive:
        wheel_files = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    with tarfile.open(sdist, 'r:gz') as archive:
        source_files = {}
        for member in archive.getmembers():
            if member.isfile():
                with archive.extractfile(member) as stream:
                    source_files[member.name.split('/', 1)[1]] = stream.read()
    for files in (wheel_files, source_files):
        assert modules <= files.keys(), 'Package modules missing from distribution'
        assert not any(private_marker.encode() in content for content in files.values()), 'Private fixture leaked into distribution'
        assert not any('__pycache__' in name or name.endswith('.pyc') for name in files)
    assert {name for name in wheel_files if name.startswith('gimail/')} == modules
    assert not any(name.startswith('tests/') or name == 'gimail.py' for name in wheel_files)
    assert {'gimail.py', 'tests/test_wire.py', 'examples/accounts.json', 'uv.lock', 'LICENSE', 'README.md'} <= source_files.keys()
    metadata_name = next(name for name in wheel_files if name.endswith('.dist-info/METADATA'))
    metadata = BytesParser().parsebytes(wheel_files[metadata_name])
    assert metadata['Name'] == 'gimail'
    assert metadata['Version'] == __version__, 'pyproject.toml and __version__ disagree'
    assert metadata['Requires-Python'] == '>=3.9'
    assert metadata['License-Expression'] == 'MIT'
    assert metadata.get_all('Requires-Dist') is None, 'Runtime must stay stdlib-only'
    info_dir = metadata_name.rsplit('/', 1)[0]
    assert info_dir + '/licenses/LICENSE' in wheel_files
    entry_points = configparser.ConfigParser()
    entry_points.read_string(wheel_files[info_dir + '/entry_points.txt'].decode())
    assert entry_points['console_scripts']['gimail'] == 'gimail.cli:main'


def check_installed(command, env, cwd):
    result = run([str(command), '--version'], env, cwd)
    assert result.stdout.strip() == 'gimail ' + __version__ and result.stderr == ''
    result = run([str(command), 'account', 'update', '--help'], env, cwd)
    assert '--keyring' in result.stdout and result.stderr == ''
    result = run([str(command), 'list', '--limit', '0'], env, cwd, expected=2)
    assert json.loads(result.stdout)['ok'] is False and result.stderr == ''
    python = Path(env['UV_TOOL_DIR']) / 'gimail' / 'bin' / 'python'
    code = '''import importlib.metadata as m, json, gimail
print(json.dumps({'version': m.version('gimail'), 'module_version': gimail.__version__,
                  'file': gimail.__file__, 'requires': m.requires('gimail')}))
'''
    metadata = json.loads(run([str(python), '-c', code], env, cwd).stdout)
    assert metadata['version'] == metadata['module_version'] == __version__
    assert not metadata['requires']
    assert Path(metadata['file']).resolve().is_relative_to(Path(env['UV_TOOL_DIR']).resolve()), 'Imported checkout instead of installed wheel'
    # Reuse real IMAP protocol tests, but force every CLI call through the
    # installed command. The fake keyring helper never reaches the real bus.
    for method in ('test_first_use_from_clone_with_environment_only',
                   'test_normal_cli_retrieves_keyring_password_internally_and_sends_it_to_imap',
                   'test_failing_keyring_helper_cannot_leak_or_fall_back_to_environment_login'):
        case = WireTests(method)
        try:
            case.setUp()
            case.env = {**env, **{key: value for key, value in case.env.items() if key.startswith('GIMAIL_')}}
            case.invoke = functools.partial(case.invoke, executable=[str(command)])
            getattr(case, method)()
        finally:
            assert case.doCleanups(), 'Installed CLI test cleanup failed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git-ref', help='also install this committed ref via a local Git URL (e.g. HEAD)')
    args = parser.parse_args()
    uv = shutil.which('uv')
    if uv is None:
        parser.error('uv is required for packaging checks; the core unittest suite does not require it')
    with tempfile.TemporaryDirectory(prefix='gimail-package-') as temporary:
        root = Path(temporary)
        home, source, artifacts, outside = (root / name for name in ('home', 'source', 'dist', 'outside'))
        home.mkdir()
        outside.mkdir()
        env = {
            'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'LANG': 'C.UTF-8',
            'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_DATA_HOME': str(home / 'data'),
            'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_RUNTIME_DIR': str(home / 'run'),
            'UV_TOOL_DIR': str(root / 'tools'), 'UV_TOOL_BIN_DIR': str(root / 'bin'),
            'UV_CACHE_DIR': str(root / 'uv-cache'), 'UV_PYTHON_DOWNLOADS': 'never', 'UV_LINK_MODE': 'copy',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(root / 'missing-bus'),
            'GIMAIL_CONFIG': str(home / 'missing.json'),
        }
        modules = copy_source(source)
        private_marker = secrets.token_hex(32)
        for name in ('accounts.json', '.env', 'gimail/accounts.json', 'gimail/accounts.json.lock',
                     'gimail/nested/secret.json', 'gimail/.env', 'gimail/.env.local', 'gimail/app.log'):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(private_marker)
        run([uv, '--no-config', 'build', '--python', sys.executable, '--out-dir', str(artifacts), str(source)], env, outside)
        wheels, sdists = list(artifacts.glob('*.whl')), list(artifacts.glob('*.tar.gz'))
        assert len(wheels) == len(sdists) == 1
        check_archives(wheels[0], sdists[0], modules, private_marker)
        # uv build normally builds its wheel from the sdist. Check a direct
        # source-to-wheel build too, where local private files still exist.
        direct = root / 'direct-wheel'
        run([uv, '--no-config', 'build', '--wheel', '--python', sys.executable,
             '--out-dir', str(direct), str(source)], env, outside)
        direct_wheels = list(direct.glob('*.whl'))
        assert len(direct_wheels) == 1
        check_archives(direct_wheels[0], sdists[0], modules, private_marker)
        # The original no-install entry point must still work without site-packages.
        result = run([sys.executable, '-S', str(source / 'gimail.py'), '--version'], env, outside)
        assert result.stdout.strip() == 'gimail ' + __version__
        sources = [str(direct_wheels[0]), str(sdists[0])]
        if args.git_ref:
            revision = run(['git', 'rev-parse', '--verify', '--end-of-options', args.git_ref + '^{commit}'], env, ROOT).stdout.strip()
            sources.append('git+' + ROOT.as_uri() + '@' + revision)
        command = Path(env['UV_TOOL_BIN_DIR']) / 'gimail'
        for package in sources:
            run([uv, '--no-config', 'tool', 'install', '--python', sys.executable, package], env, outside)
            check_installed(command, env, outside)
            run([uv, '--no-config', 'tool', 'uninstall', 'gimail'], env, outside)
            assert not command.exists()
        print(f'Packaging OK: {len(sources)} isolated uv tool installs (wheel, sdist'
              + (', Git' if args.git_ref else '') + '); entry point, IMAP, keyring, metadata, and archive privacy verified.')


if __name__ == '__main__':
    main()
