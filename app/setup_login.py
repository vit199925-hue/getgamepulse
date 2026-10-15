"""Generate a local account and write only its password hash to .env."""

import argparse
import re
import secrets
from pathlib import Path

from app.auth import password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", args.username):
        parser.error("username must use 1-100 letters, digits, dots, underscores or hyphens")
    env = Path(".env")
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    password = secrets.token_urlsafe(15)
    values = {
        "WEB_USERNAME": args.username,
        "WEB_PASSWORD_HASH": password_hash(password),
        "SESSION_SECRET": secrets.token_urlsafe(48),
    }
    for name, value in values.items():
        # Single quotes also prevent Docker Compose from expanding the $ separators in the hash.
        line = f"{name}='{value}'"
        if re.search(rf"^{name}=.*$", text, re.MULTILINE):
            text = re.sub(rf"^{name}=.*$", lambda match: line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
    env.write_text(text, encoding="utf-8")
    Path(".local-credentials.txt").write_text(
        f"URL: http://127.0.0.1:8000\nLogin: {args.username}\nPassword: {password}\n", encoding="utf-8"
    )
    print("Login created. Credentials saved to .local-credentials.txt; .env contains the password hash.")


if __name__ == "__main__":
    main()
