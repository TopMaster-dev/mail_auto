"""
Change the admin panel password and write it straight into .env.

Preferred over hand-editing, because a bcrypt hash contains `$` characters.
Passing one through a shell (`sed "s|...|...$2b$12$...|"`) silently expands
`$2`, `$1` as positional parameters and stores a corrupted hash — which then
looks exactly like a wrong password at the login screen.

Typed interactively, the password never reaches shell history or `ps` output.

    cd /opt/mail_automation
    venv/bin/python -m admin.change_password

Non-interactive (CI, scripted setup) — note this DOES land in shell history:

    printf 'new-password\\n' | venv/bin/python -m admin.change_password

Restart the panel afterwards for the change to take effect:

    sudo systemctl restart mail-automation-admin
"""
from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

import bcrypt

_KEY = "ADMIN_PASSWORD_HASH"
_MIN_LENGTH = 12
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_password() -> str:
    """Prompt on a terminal; otherwise read one line from stdin."""
    if not sys.stdin.isatty():
        pw = sys.stdin.readline().rstrip("\n")
        if not pw:
            sys.exit("パスワードが空です。")
        return pw

    pw = getpass("新しいパスワード: ")
    if not pw:
        sys.exit("パスワードが空です。")
    if pw != getpass("確認のためもう一度: "):
        sys.exit("パスワードが一致しません。変更していません。")
    return pw


def _write_hash(env_path: Path, new_hash: str) -> None:
    """Replace the hash line in place, preserving the file's 600 permissions.

    Opened for truncation rather than replaced, so the existing mode and owner
    survive — a rename would create a fresh file with default permissions.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.lstrip().startswith(_KEY):
            out.append(f"{_KEY}={new_hash}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{_KEY}={new_hash}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    if not _ENV_PATH.exists():
        sys.exit(f"{_ENV_PATH} がありません。先に .env を作成してください。")

    password = _read_password()
    if len(password) < _MIN_LENGTH:
        print(f"警告: {_MIN_LENGTH}文字未満です。管理画面が外部公開されている場合は"
              f"より長いパスワードを推奨します。", file=sys.stderr)

    new_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    _write_hash(_ENV_PATH, new_hash)

    # Read back and verify, so a bad write can never be reported as success.
    stored = ""
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(_KEY):
            stored = line.split("=", 1)[1].strip().strip("'\"")
            break
    if not bcrypt.checkpw(password.encode(), stored.encode()):
        sys.exit("書き込み後の検証に失敗しました。.env を確認してください。")

    print(f"パスワードを変更しました（{_ENV_PATH}）")
    print("反映するには次を実行してください:")
    print("    sudo systemctl restart mail-automation-admin")


if __name__ == "__main__":
    main()
