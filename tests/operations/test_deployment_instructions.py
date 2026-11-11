"""Exercise the documented installer only against disposable paths, without sudo."""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ("/etc/trader/runtime.env", "runtime.env.example"),
    ("/etc/trader/credentials.env", "credentials.env.example"),
    ("/etc/systemd/system/trader.service", "trader.service"),
    ("/etc/systemd/system/trader.socket", "trader.socket"),
    ("/etc/nginx/conf.d/trader.conf", "nginx.conf"),
)
AUTH_FILE = "/etc/nginx/trader.htpasswd"


def install_block() -> str:
    document = (ROOT / "docs/operations/LINUX_DEPLOYMENT_KO.md").read_text(encoding="utf-8")
    return next(block for block in re.findall(r"```sh\n(.*?)```", document, re.DOTALL)
                if "sudo useradd" in block)


@pytest.mark.parametrize("target,_source", TARGETS)
def test_each_documented_install_refuses_existing_files_and_links(target: str, _source: str) -> None:
    # Portable contract check; Linux below executes these same commands and checks effects.
    block = install_block()
    path = re.escape(target)
    assert re.search(
        rf"sudo test ! -e {path}\s*&&\s*sudo test ! -L {path}\s*&&\s*"
        rf"sudo install [^\n]* {path}\s*\|\| exit 1", block,
    ), f"{target} must reject both files and dangling links, then stop on failure"


def test_documented_install_refuses_existing_password_file_or_link() -> None:
    block = install_block()
    path = re.escape(AUTH_FILE)
    assert re.search(
        rf"sudo test ! -e {path}\s*&&\s*sudo test ! -L {path}\s*&&\s*"
        rf"sudo install -o root -g www-data -m 0640 /dev/null {path}\s*&&\s*"
        rf"sudo htpasswd -B {path} trader-admin\s*&&\s*"
        rf"sudo chown root:www-data {path}\s*&&\s*sudo chmod 0640 {path}", block,
    ), "The operator password file must not overwrite an existing file or link"


def run_disposable_install(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("sh")
    assert shell, "Linux operations CI requires a real POSIX shell"
    block = install_block().replace("/etc/", f"{tmp_path.as_posix()}/etc/")
    block = block.replace("/var/lib/trader", f"{tmp_path.as_posix()}/var/lib/trader")
    # Reject every destination outside tmp_path. Never invoke real sudo/useradd or change owners.
    shim = r'''
sudo() {
  operation=$1
  shift
  case "$operation" in
    useradd) return 0 ;;
    test)
      [ "$#" -eq 3 ] || return 90
      case "$3" in "$sandbox"/*) command test "$@" ;; *) return 90 ;; esac
      ;;
    install)
      directory=no
      if [ "$1" = -d ]; then directory=yes; shift; fi
      [ "$1" = -o ] && [ "$3" = -g ] && [ "$5" = -m ] || return 90
      mode=$6
      shift 6
      if [ "$directory" = yes ]; then
        [ "$#" -eq 1 ] || return 90
        case "$1" in "$sandbox"/*) command mkdir -p "$1" ;; *) return 90 ;; esac
      else
        [ "$#" -eq 2 ] || return 90
        case "$2" in
          "$sandbox"/*) ;;
          *) return 90 ;;
        esac
        case "$1" in
          deploy/linux/*) ;;
          /dev/null) [ "$2" = "$sandbox/etc/nginx/trader.htpasswd" ] || return 90 ;;
          *) return 90 ;;
        esac
        command install -m "$mode" "$1" "$2"
      fi
      ;;
    htpasswd)
      [ "$#" -eq 3 ] && [ "$1" = -B ] && [ "$3" = trader-admin ] || return 90
      case "$2" in "$sandbox"/*) printf '%s\n' 'trader-admin:$2y$synthetic-hash' >> "$2" ;;
        *) return 90 ;;
      esac
      ;;
    chown|chmod)
      [ "$#" -eq 2 ] || return 90
      case "$2" in "$sandbox"/*) return 0 ;; *) return 90 ;;
      esac
      ;;
    *) return 90 ;;
  esac
}
'''
    # Parent directories normally provided by the host are disposable too.
    (tmp_path / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/nginx/conf.d").mkdir(parents=True, exist_ok=True)
    program = f"sandbox={shlex.quote(tmp_path.as_posix())}\n{shim}\n{block}"
    return subprocess.run([shell], input=program, cwd=ROOT, capture_output=True,
                          text=True, check=False, timeout=10)


@pytest.mark.skipif(sys.platform != "linux", reason="Real POSIX installer requires Linux CI")
def test_documented_first_install_copies_all_five_templates(tmp_path: Path) -> None:
    result = run_disposable_install(tmp_path)
    assert result.returncode == 0, result.stderr
    for target, source in TARGETS:
        assert (tmp_path / target.lstrip("/")).read_bytes() == (
            ROOT / "deploy/linux" / source
        ).read_bytes()
    password_file = tmp_path / AUTH_FILE.lstrip("/")
    assert password_file.read_text(encoding="utf-8") == "trader-admin:$2y$synthetic-hash\n"
    assert password_file.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(sys.platform != "linux", reason="Real POSIX installer requires Linux CI")
@pytest.mark.parametrize("existing", ("file", "symlink", "dangling_symlink"))
def test_documented_install_preserves_existing_password_file(tmp_path: Path, existing: str) -> None:
    destination = tmp_path / AUTH_FILE.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"existing operator hash\n"
    referent = tmp_path / "preserved-auth-original"
    if existing == "file":
        destination.write_bytes(sentinel)
    else:
        if existing == "symlink":
            referent.write_bytes(sentinel)
        destination.symlink_to(referent)
    result = run_disposable_install(tmp_path)
    assert result.returncode != 0, "The install block must not replace an existing password file"
    if existing == "file":
        assert not destination.is_symlink()
        assert destination.read_bytes() == sentinel
    else:
        assert destination.is_symlink()
        assert destination.readlink() == referent
        if existing == "symlink":
            assert referent.read_bytes() == sentinel
        else:
            assert not referent.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Real POSIX installer requires Linux CI")
@pytest.mark.parametrize("target,_source", TARGETS)
@pytest.mark.parametrize("existing", ("file", "symlink", "dangling_symlink"))
def test_documented_reinstall_preserves_existing_target_and_stops(
    tmp_path: Path, target: str, _source: str, existing: str,
) -> None:
    destination = tmp_path / target.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"existing private configuration\n"
    referent = tmp_path / "preserved-original"
    if existing == "file":
        destination.write_bytes(sentinel)
    else:
        if existing == "symlink":
            referent.write_bytes(sentinel)
        destination.symlink_to(referent)
    result = run_disposable_install(tmp_path)
    assert result.returncode != 0, "The pasted block must stop when a target already exists"
    if existing == "file":
        assert not destination.is_symlink()
        assert destination.read_bytes() == sentinel
    else:
        assert destination.is_symlink()
        assert destination.readlink() == referent
        if existing == "symlink":
            assert referent.read_bytes() == sentinel
        else:
            assert not referent.exists()
    later_targets = [item[0] for item in TARGETS]
    for later in later_targets[later_targets.index(target) + 1:]:
        assert not (tmp_path / later.lstrip("/")).exists()
