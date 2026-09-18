"""Opt-in, local cookie continuity; never a claim that an account is logged in.

Only the application's cookies are stored. No passwords, DOM, localStorage,
IndexedDB, daily browser profiles or arbitrary file paths are accepted. Windows
uses current-user DPAPI; POSIX uses owner-only files in an owner-only directory.
A live OS lock prevents two processes from restoring/writing the same platform.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit

from .contracts import CrawlError

MAX_BYTES = 2_000_000
MAX_AGE = 7 * 24 * 3600


def _error(code='saved_session_invalid'):
    return CrawlError(code)


def _safe_file(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or (directory and not stat.S_ISDIR(info.st_mode))):
        raise _error('saved_session_unsafe')
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise _error('saved_session_unsafe')
    return info


def _dpapi(data: bytes, entropy: bytes, *, decrypt=False) -> bytes:
    """Use the current Windows account, without prompting or storing a key."""
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffers = [ctypes.create_string_buffer(value) for value in (data, entropy)]
    source, extra = [Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
                     for value, buf in zip((data, entropy), buffers)]
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; never CRYPTPROTECT_LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(result)):
        raise _error('saved_session_unreadable')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def _cookies(value, domains):
    """Accept only the documented cookie shape and reviewed platform domains."""
    if not isinstance(value, list) or len(value) > 1500:
        raise _error()
    out = []
    fields = {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'}
    for item in value:
        if not isinstance(item, dict) or not fields.issubset(item):
            raise _error()
        if any(not isinstance(item[k], str) for k in ('name', 'value', 'domain', 'path', 'sameSite')):
            raise _error()
        if item['domain'].lstrip('.').lower() not in domains:
            continue
        if (not item['name'] or len(item['name']) > 1024 or len(item['value']) > 65536
                or not item['path'].startswith('/') or len(item['path']) > 2048
                or item['sameSite'] not in {'Strict', 'Lax', 'None'}
                or type(item['secure']) is not bool or type(item['httpOnly']) is not bool
                or type(item['expires']) not in (int, float) or not math.isfinite(item['expires'])):
            raise _error()
        # Partitioned cookies require a separately reviewed top-level-site
        # binding. Omitting their key would turn them into unpartitioned cookies.
        if 'partitionKey' in item:
            continue
        out.append({k: item[k] for k in fields})
    return out


class SavedSession:
    """One workspace/platform lease. All methods run on the browser owner thread."""
    def __init__(self, workspace, adapter, *, backend, browser, network, clock=time.time):
        if not re.fullmatch(r'[a-z0-9_]{2,32}', adapter.key):
            raise _error()
        self.clock, self.status, self._lock = clock, 'empty', None
        self.root = Path(workspace).resolve() / '.radar-sessions'
        self.root.mkdir(mode=0o700, exist_ok=True)
        _safe_file(self.root, directory=True)
        self.path = self.root / (adapter.key + '.json')
        self.domains = set(adapter.domains) | set(adapter.login_hosts) | {urlsplit(adapter.search_base).hostname}
        self.binding = {'workspace': hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest(),
                        'platform': adapter.key, 'adapter': str(adapter.version), 'backend': backend,
                        'browser': browser, 'network': network}
        self.entropy = hashlib.sha256(json.dumps(self.binding, sort_keys=True).encode()).digest()
        lock_path = self.root / (adapter.key + '.lock')
        try:
            if lock_path.exists() or lock_path.is_symlink():
                _safe_file(lock_path)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            self._lock = os.fdopen(fd, 'r+b', buffering=0)
            _safe_file(lock_path)
            if os.name == 'nt':
                import msvcrt
                if not os.fstat(fd).st_size:
                    self._lock.write(b'0')
                self._lock.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            if self._lock:
                self._lock.close(); self._lock = None
            if isinstance(exc, CrawlError):
                raise
            raise _error('saved_session_busy') from None

    def _check(self):
        if self._lock is None:
            raise _error('saved_session_busy')
        _safe_file(self.root, directory=True)

    def restore(self):
        self._check()
        if not self.path.exists() and not self.path.is_symlink():
            return None
        try:
            if _safe_file(self.path).st_size > MAX_BYTES:
                raise _error()
            fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise _error()
            envelope = json.loads(raw)
            if envelope.get('version') != 1 or envelope.get('binding') != self.binding:
                raise _error('saved_session_incompatible')
            created = envelope['saved_at']
            if type(created) not in (int, float) or not math.isfinite(created) or created > self.clock() + 60:
                raise _error()
            if self.clock() - created > MAX_AGE:
                self.path.unlink()
                self.status = 'expired'
                return None  # Normal login may be needed; never promise account validity.
            codec = 'dpapi' if os.name == 'nt' else 'owner_only'
            if envelope.get('protection') != codec:
                raise _error('saved_session_incompatible')
            data = base64.b64decode(envelope['payload'], validate=True)
            if os.name == 'nt':
                data = _dpapi(data, self.entropy, decrypt=True)
            cookies = _cookies(json.loads(data), self.domains)
            self.status = 'restored_unverified'
            return {'cookies': cookies, 'origins': []}
        except CrawlError:
            raise
        except Exception:
            raise _error('saved_session_unreadable') from None

    def save(self, cookies):
        self._check()
        cookies = _cookies(cookies, self.domains)
        data = json.dumps(cookies, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > MAX_BYTES // 2:
            raise _error()
        if os.name == 'nt':
            data = _dpapi(data, self.entropy)
        envelope = {'version': 1, 'binding': self.binding, 'saved_at': self.clock(),
                    'protection': 'dpapi' if os.name == 'nt' else 'owner_only',
                    'payload': base64.b64encode(data).decode('ascii')}
        encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
        if self.path.exists() or self.path.is_symlink():
            _safe_file(self.path)
        fd, temporary = tempfile.mkstemp(prefix='.session-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.status = 'saved_unverified'
        finally:
            Path(temporary).unlink(missing_ok=True)

    def forget(self):
        self._check()
        if self.path.exists() or self.path.is_symlink():
            _safe_file(self.path)
            self.path.unlink()
        self.status = 'cleared'

    def close(self):
        # Do NOT unlink a lock file: another process could still lock that inode.
        if self._lock is not None:
            self._lock.close(); self._lock = None
