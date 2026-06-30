"""
Generate a bcrypt hash for the admin panel password.

Usage:
    python -m admin.set_password "your-strong-password"

Copy the printed hash into .env as ADMIN_PASSWORD_HASH=...
"""
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    pw = sys.argv[1].encode()
    print(bcrypt.hashpw(pw, bcrypt.gensalt()).decode())


if __name__ == "__main__":
    main()
