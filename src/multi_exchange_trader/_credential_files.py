"""Private local file and platform-protection boundaries; no provider access."""

import ctypes
import importlib
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, cast


class CredentialStoreError(RuntimeError):
    """Fixed, safe codes only; never carry paths, OS diagnostics or input values."""

    def __init__(self, code: str) -> None:
        if code not in {"UNSAFE_PATH", "INVALID_STORE", "INVALID_VALUES", "INVALID_IMPORT",
                        "REVISION_CONFLICT", "LOCK_TIMEOUT", "PERSISTENCE_FAILED",
                        "PROTECTION_UNAVAILABLE", "INVALID_ARGUMENTS"}:
            code = "INVALID_STORE"
        super().__init__(code)

    @property
    def code(self) -> str:
        return str(self)


class Protector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class WindowsDPAPI:
    """Current-user DPAPI, CRYPTPROTECT_UI_FORBIDDEN, no machine/plaintext fallback."""

    @staticmethod
    def _apply(value: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise CredentialStoreError("PROTECTION_UNAVAILABLE")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise CredentialStoreError("PROTECTION_UNAVAILABLE")
        library = win_dll("crypt32", use_last_error=True)
        kernel = win_dll("kernel32", use_last_error=True)
        local_free = kernel.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        if decrypt:
            operation = library.CryptUnprotectData
            operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.POINTER(Blob)]
            arguments = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        else:
            operation = library.CryptProtectData
            operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.POINTER(Blob)]
            arguments = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        operation.restype = wintypes.BOOL
        if not operation(*arguments):
            raise CredentialStoreError("INVALID_STORE" if decrypt else "PROTECTION_UNAVAILABLE")
        try:
            if target.size > 65536:
                raise CredentialStoreError("INVALID_STORE")
            return ctypes.string_at(target.data, target.size)
        finally:
            local_free(target.data)

    def protect(self, value: bytes) -> bytes:
        return self._apply(value, decrypt=False)

    def unprotect(self, value: bytes) -> bytes:
        return self._apply(value, decrypt=True)


def _linked(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def check_path(path: Path, *, private: bool = True) -> None:
    """Reject traversal and linked components without resolving away evidence."""
    if not path.is_absolute() or ".." in path.parts:
        raise CredentialStoreError("UNSAFE_PATH")
    for item in (*reversed(path.parents), path):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if _linked(metadata):
            raise CredentialStoreError("UNSAFE_PATH")
        if item != path and not stat.S_ISDIR(metadata.st_mode):
            raise CredentialStoreError("UNSAFE_PATH")
        if item == path and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise CredentialStoreError("UNSAFE_PATH")
        if os.name == "posix" and private and item in (path, path.parent):
            expected_mode = 0o600 if item == path else 0o700
            if metadata.st_uid != _current_uid() or stat.S_IMODE(metadata.st_mode) != expected_mode:
                raise CredentialStoreError("UNSAFE_PATH")


def identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def current_identity(path: Path) -> tuple[int, int, int, int] | None:
    check_path(path)
    try:
        return identity(path.lstat())
    except FileNotFoundError:
        return None


def _check_open(metadata: os.stat_result, *, private: bool) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or _linked(metadata):
        raise CredentialStoreError("UNSAFE_PATH")
    if os.name == "posix" and private and (
        metadata.st_uid != _current_uid() or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise CredentialStoreError("UNSAFE_PATH")


def _current_uid() -> int:
    # Dynamic access keeps Windows type checking honest without pretending that
    # Windows has POSIX ownership APIs. This executes only on POSIX branches.
    getter = getattr(os, "getuid", None)
    if getter is None:
        raise CredentialStoreError("PROTECTION_UNAVAILABLE")
    return cast(int, getter())


def read_checked(path: Path, limit: int, *, private: bool = True) -> bytes | None:
    check_path(path, private=private)
    try:
        before = identity(path.lstat())
    except FileNotFoundError:
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        _check_open(metadata, private=private)
        if identity(metadata) != before or metadata.st_size > limit:
            raise CredentialStoreError("INVALID_STORE")
        content = stream.read(limit + 1)
        if len(content) > limit or identity(os.fstat(stream.fileno())) != before:
            raise CredentialStoreError("INVALID_STORE")
    check_path(path, private=private)
    if identity(path.lstat()) != before:
        raise CredentialStoreError("INVALID_STORE")
    return content


def create_private_parent(path: Path) -> None:
    check_path(path)
    # Every newly created component is private, including an absent private root.
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    check_path(path)


LOCK_TIMEOUT_SECONDS = 5.0


@contextmanager
def private_lock(path: Path, *, lock_name: Literal[
    ".credentials.lock", ".account-profiles.lock",
] = ".credentials.lock") -> Iterator[Callable[[], None]]:
    """Fixed persistent OS lock; never unlink it or silently break another lock."""
    if lock_name not in {".credentials.lock", ".account-profiles.lock"}:
        raise CredentialStoreError("UNSAFE_PATH")
    lock = path.parent / lock_name
    check_path(lock)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock, flags, 0o600)
    acquired = False
    try:
        _check_open(os.fstat(descriptor), private=True)
        check_path(lock)
        if identity(os.fstat(descriptor)) != identity(lock.lstat()):
            raise CredentialStoreError("UNSAFE_PATH")
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                if os.name == "nt":
                    msvcrt = importlib.import_module("msvcrt")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise CredentialStoreError("PROTECTION_UNAVAILABLE")
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CredentialStoreError("LOCK_TIMEOUT") from None
                time.sleep(0.025)
        def verify_lock() -> None:
            check_path(lock)
            if identity(os.fstat(descriptor)) != identity(lock.lstat()):
                raise CredentialStoreError("UNSAFE_PATH")

        verify_lock()
        yield verify_lock
        verify_lock()
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    msvcrt = importlib.import_module("msvcrt")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def sync_directory(path: Path) -> None:
    # Windows has no stdlib directory-fsync equivalent. File fsync still runs.
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                             getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_private(
    path: Path, content: bytes, original: tuple[int, int, int, int] | None,
    verify_lock: Callable[[], None], *,
    prefix: Literal[".credentials-", ".account-profiles-"],
    identify: Callable[[Path], tuple[int, int, int, int] | None] = current_identity,
    sync: Callable[[Path], None] = sync_directory,
    before_replace: Callable[[], None] = lambda: None,
) -> None:
    """Shared private publication; callers own CAS, confirmation and safe errors.

    Credential defaults and its existing filesystem seams remain unchanged.
    The profile issuer's final freshness check executes after file fsync, while
    the fixed lock is held, immediately before target verification/replacement.
    """
    if prefix not in {".credentials-", ".account-profiles-"}:
        raise CredentialStoreError("UNSAFE_PATH")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            if identify(temporary) != identity(os.fstat(stream.fileno())):
                raise CredentialStoreError("UNSAFE_PATH")
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_identity = identity(os.fstat(stream.fileno()))
        before_replace()
        if identify(path) != original:
            raise CredentialStoreError("REVISION_CONFLICT")
        verify_lock()
        if identify(temporary) != temporary_identity:
            raise CredentialStoreError("UNSAFE_PATH")
        os.replace(temporary, path)
        temporary = None
        sync(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
