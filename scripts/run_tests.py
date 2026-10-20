"""Run isolated, offline tests from any checkout or CI interpreter.

No arguments runs pytest; ``--pytest [args]`` selects pytest explicitly.
Existing arguments remain unittest arguments; ``--unittest`` is also accepted.
The Python network guard is supplementary, not an operating-system firewall.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


OFFLINE_GUARD = r'''
import ipaddress
import os
import re
import shlex
import socket
import sys
import threading
from urllib.parse import urlsplit

class OfflineADBProcessError(BaseException):
    """A test attempted to start the real Android Debug Bridge executable."""

_ADB_NAMES = {'adb', 'adb.exe'}
_adb_violation_count = 0
_adb_violation_lock = threading.Lock()
_SHELL_NAMES = {
    'bash', 'bash.exe', 'cmd', 'cmd.exe', 'fish', 'fish.exe', 'powershell',
    'powershell.exe', 'pwsh', 'pwsh.exe', 'sh', 'sh.exe', 'zsh', 'zsh.exe',
}

def _basename(value):
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8', 'replace')
        except Exception:
            return ''
    if not isinstance(value, str):
        return ''
    value = value.strip().strip('\\"').strip("'")
    return re.split(r'[/\\]', value)[-1].casefold()

def _command_tokens(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    if isinstance(value, str):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
            value = value[1:-1]
        try:
            return shlex.split(value, posix=(os.name != 'nt'))
        except ValueError:
            return value.split()
    if isinstance(value, (list, tuple)):
        return list(value)
    return []

def _is_adb_launch(executable, args):
    if _basename(executable) in _ADB_NAMES:
        return True
    tokens = _command_tokens(args)
    if tokens and _basename(tokens[0]) in _ADB_NAMES:
        return True
    # `shell=True` is commonly represented as a command string. Windows and
    # POSIX shells also appear as explicit argv[0] in some Popen call forms.
    shell_command = _basename(executable) in _SHELL_NAMES
    if tokens and _basename(tokens[0]) in _SHELL_NAMES:
        shell_command = True
        tokens = tokens[1:]
    if shell_command:
        nested = [part for token in tokens for part in _command_tokens(token)]
        return any(_basename(token) in _ADB_NAMES for token in nested)
    return False

def _block_adb_process(event, args):
    global _adb_violation_count
    if event == 'subprocess.Popen' and len(args) >= 2:
        executable, command = args[0], args[1]
        if _is_adb_launch(executable, command):
            with _adb_violation_lock:
                _adb_violation_count += 1
            raise OfflineADBProcessError(
                'Real ADB access is disabled by the isolated test runner.')

def _offline_adb_violation_count():
    with _adb_violation_lock:
        return _adb_violation_count

sys.addaudithook(_block_adb_process)

def _local(host):
    if isinstance(host, bytes):
        try:
            host = host.decode('ascii')
        except UnicodeError:
            return False
    if str(host).lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False

def _address(address):
    if not isinstance(address, tuple) or not address or not _local(address[0]):
        raise OSError('External network is disabled by the offline test runner.')

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_sendto = socket.socket.sendto
def connect(self, address):
    _address(address)
    return _connect(self, address)
def connect_ex(self, address):
    _address(address)
    return _connect_ex(self, address)
def sendto(self, data, *args):
    _address(args[-1] if args else None)
    return _sendto(self, data, *args)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.sendto = sendto

_getaddrinfo = socket.getaddrinfo
_gethostbyname = socket.gethostbyname
_gethostbyname_ex = socket.gethostbyname_ex
_gethostbyaddr = socket.gethostbyaddr
_getnameinfo = socket.getnameinfo
def getaddrinfo(host, *args, **kwargs):
    if host is not None and not _local(host):
        raise OSError('External DNS is disabled by the offline test runner.')
    return _getaddrinfo(host, *args, **kwargs)
def _dns(original):
    def resolve(host, *args, **kwargs):
        if not _local(host):
            raise OSError('External DNS is disabled by the offline test runner.')
        return original(host, *args, **kwargs)
    return resolve
def getnameinfo(address, *args, **kwargs):
    _address(address)
    return _getnameinfo(address, *args, **kwargs)
socket.getaddrinfo = getaddrinfo
socket.gethostbyname = _dns(_gethostbyname)
socket.gethostbyname_ex = _dns(_gethostbyname_ex)
socket.gethostbyaddr = _dns(_gethostbyaddr)
socket.getnameinfo = getnameinfo

def guard_http(cls):
    original = cls.request
    def request(self, method, url, *args, **kwargs):
        if not _local(urlsplit(str(url)).hostname):
            raise OSError('External HTTP is disabled by the offline test runner.')
        return original(self, method, url, *args, **kwargs)
    cls.request = request

try:
    import requests.sessions
    guard_http(requests.sessions.Session)
except ImportError:
    pass
try:
    import curl_cffi.requests
    guard_http(curl_cffi.requests.Session)
    guard_http(curl_cffi.requests.AsyncSession)
except ImportError:
    pass
'''


PYTEST_ADB_GUARD_PLUGIN = r'''
import sys

def pytest_sessionfinish(session, exitstatus):
    import sitecustomize
    violations = sitecustomize._offline_adb_violation_count()
    if violations:
        print(
            f'Offline runner blocked {violations} real ADB process attempt(s); '
            'pytest is failing.',
            file=sys.stderr,
        )
        session.exitstatus = 1
'''


def test_command(arguments: list[str]) -> list[str]:
    if not arguments:
        module, arguments = "pytest", ["-q", "test"]
    elif arguments[0] == "--pytest":
        module, arguments = "pytest", arguments[1:] or ["-q", "test"]
    else:
        module = "unittest"
        if arguments[0] == "--unittest":
            arguments = arguments[1:] or ["discover", "-s", "test", "-q"]
    return [sys.executable, "-B", "-X", "utf8", "-m", module, *arguments]


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    command = test_command(sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix="venom-offline-") as folder:
        root = Path(folder)
        guard_dir, runtime = root / "guard", root / "runtime"
        guard_dir.mkdir()
        runtime.mkdir()
        (guard_dir / "sitecustomize.py").write_text(OFFLINE_GUARD, encoding="utf-8")
        for name in ("aws-credentials", "aws-config", "boto-config"):
            (root / name).write_text("", encoding="utf-8")
        private_prefixes = ("QMONEY_", "MINUTE_", "AWS_", "HOSTINGER_", "EGO4D_",
                            "CROWTADO_", "CLARU_", "NYMERIA_")
        excluded_keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                         "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE",
                         "GOOGLE_APPLICATION_CREDENTIALS", "BOTO_CONFIG",
                         "PLAYWRIGHT_BROWSERS_PATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(private_prefixes)
                       and key.upper() not in excluded_keys}
        environment.update(
            QMONEY_USER_ROOT=str(root), QMONEY_LIBRARY_ROOT=str(root),
            QMONEY_RUNTIME_ROOT=str(runtime), MINUTE_VPN_ENFORCE="0",
            MINUTE_REQUIRE_CURL="0", MINUTE_PUBLISH_APP_OPENED="0",
            AWS_SHARED_CREDENTIALS_FILE=str(root / "aws-credentials"),
            AWS_CONFIG_FILE=str(root / "aws-config"), BOTO_CONFIG=str(root / "boto-config"),
            AWS_EC2_METADATA_DISABLED="true", PYTHONIOENCODING="utf-8",
            PYTHONPATH=os.pathsep.join((str(guard_dir), str(project))),
            PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        )
        if len(command) > 5 and command[5] == "pytest":
            plugin_name = "_offline_adb_pytest_plugin"
            (guard_dir / f"{plugin_name}.py").write_text(
                PYTEST_ADB_GUARD_PLUGIN, encoding="utf-8")
            environment["PYTEST_PLUGINS"] = plugin_name
        return subprocess.run(
            command, cwd=project, env=environment,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
