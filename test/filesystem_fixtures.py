"""Native directory aliases confined to a caller-owned temporary directory."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess


@contextmanager
def temporary_directory_alias(link: Path, target: Path, *, temporary_root: Path):
    """Use a symlink, or a Windows junction only when symlink privilege is absent."""
    root = temporary_root.resolve(strict=True)
    target = target.resolve(strict=True)
    link = link.parent.resolve(strict=True) / link.name
    if (not root.is_dir() or not target.is_dir() or target == root
            or not target.is_relative_to(root) or not link.is_relative_to(root)
            or link == target or target.is_relative_to(link)):
        raise ValueError("Directory alias fixtures must stay inside their temporary root")
    if os.path.lexists(link):
        raise FileExistsError(link)
    try:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            # ERROR_PRIVILEGE_NOT_HELD is the expected limitation on Windows
            # without developer mode. Access, path and filesystem errors fail.
            if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
                raise
            powershell = shutil.which("powershell.exe")
            if not powershell:
                raise RuntimeError("PowerShell is required for the native junction fixture") from exc
            environment = dict(os.environ,
                FINALLY_TEST_ALIAS_LINK=str(link), FINALLY_TEST_ALIAS_TARGET=str(target))
            result = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference = 'Stop'; "
                 "New-Item -ItemType Junction -Path $env:FINALLY_TEST_ALIAS_LINK "
                 "-Value $env:FINALLY_TEST_ALIAS_TARGET | Out-Null"],
                env=environment, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            if result.returncode:
                raise AssertionError("Native junction creation failed: "
                                     + (result.stderr or result.stdout).strip())
        if not link.is_dir() or link.resolve(strict=True) != target:
            raise AssertionError("Native directory alias does not resolve to its fixture target")
        yield link
    finally:
        if os.path.lexists(link):
            # Never recursively delete or follow the alias during cleanup.
            if (not link.parent.resolve(strict=True).is_relative_to(root)
                    or link.resolve(strict=True) != target):
                raise AssertionError("Directory alias moved outside its fixture cleanup scope")
            if link.is_symlink():
                link.unlink()
            elif getattr(link.lstat(), "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                link.rmdir()
            else:
                raise AssertionError("Directory alias was replaced by a regular directory")
