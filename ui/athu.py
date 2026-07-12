"""
Account storage - reading/writing accounts to a simple JSON file on disk.
No page/layout code here at all; ui/account_page.py and
ui/select_account_page.py call into this.

Storage location: ~/.timecard_app/accounts.json
A list of accounts, e.g.:
[
  {"username": "Omar", "salt": "...", "password_hash": "..."},
  {"username": "Seif", "salt": "...", "password_hash": "..."}
]

NOTE: password is salted + hashed before being written, so it's never
stored in plain text. It is NOT currently re-checked anywhere, since the
agreed flow is "tap an account tile to log straight in" (no re-entering
a password). The hash is kept on disk mainly so it exists if a real
login/verification step gets added later.

TODO: if this app ever needs real per-user security (shared machine,
sensitive data), this plain JSON file is not enough on its own -
consider OS-level credential storage (e.g. the `keyring` package) or
at least encrypting this file.
"""

import os
import json
import hashlib
import secrets

from schemas.accounts import Account

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".timecard_app")
ACCOUNTS_FILE = os.path.join(CONFIG_DIR, "accounts.json")


def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, hashed


def list_accounts():
    """Returns a list of Account objects. Empty list if no accounts yet."""
    if not os.path.isfile(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Account.from_dict(a) for a in raw]


def accounts_exist():
    return len(list_accounts()) > 0


def username_taken(username):
    return any(a.username.lower() == username.lower() for a in list_accounts())


def save_account(username, password):
    """Creates a new account and appends it to the accounts file.
    Raises ValueError if the username is already taken."""
    accounts = list_accounts()
    if username_taken(username):
        raise ValueError(f"An account named '{username}' already exists.")

    salt, hashed = _hash_password(password)
    accounts.append(Account(username=username, salt=salt, password_hash=hashed))
    _write_accounts(accounts)


def verify_password(username, password):
    """Return True when the provided password matches the stored hash."""
    for account in list_accounts():
        if account.username.lower() != username.lower():
            continue
        salt, expected_hash = account.salt, account.password_hash
        _, actual_hash = _hash_password(password, salt=salt)
        return actual_hash == expected_hash
    return False


def _write_accounts(accounts):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump([a.to_dict() for a in accounts], f, indent=2)