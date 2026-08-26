import os
import pwd
import stat
import sys
from typing import NoReturn

_PRIVATE_DIRECTORY_MODE = 0o700
_DEFAULT_STORAGE_ROOT = "/data"


def fail() -> NoReturn:
    raise SystemExit("container storage initialization failed")


def validated_storage_root() -> str:
    value = os.environ.get("EXAM_GURU_STORAGE_ROOT", _DEFAULT_STORAGE_ROOT)
    if (
        not value
        or len(value) > 1_024
        or not os.path.isabs(value)
        or value == "/"
        or value != value.strip()
        or not value.isprintable()
        or "\\" in value
        or any(segment in {"", ".", ".."} for segment in value.split("/")[1:])
    ):
        fail()
    return value


def runtime_identity() -> tuple[int, int]:
    account = pwd.getpwnam("exam-guru")
    if os.environ.get("EXAM_GURU_STORAGE_BACKEND", "local") != "local":
        return account.pw_uid, account.pw_gid

    root = validated_storage_root()
    try:
        details = os.lstat(root)
    except OSError:
        fail()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        fail()

    runtime_uid = details.st_uid if details.st_uid != 0 else account.pw_uid
    runtime_gid = details.st_gid if details.st_gid != 0 else account.pw_gid
    try:
        if os.geteuid() == 0:
            os.chown(root, runtime_uid, runtime_gid)
        elif details.st_uid != os.geteuid():
            fail()
        os.chmod(root, _PRIVATE_DIRECTORY_MODE)
    except OSError:
        fail()
    return runtime_uid, runtime_gid


def main() -> None:
    if len(sys.argv) < 2:
        fail()
    uid, gid = runtime_identity()
    account = pwd.getpwnam("exam-guru")
    os.environ["HOME"] = account.pw_dir
    os.environ["LOGNAME"] = account.pw_name
    os.environ["USER"] = account.pw_name
    os.umask(0o077)
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    elif os.geteuid() != uid:
        fail()
    os.execvp(sys.argv[1], sys.argv[1:])  # noqa: S606 - container argv is the trusted image command


if __name__ == "__main__":
    main()
