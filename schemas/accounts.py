"""
Account data shape - no logic, no UI. Just what fields make up one account.
"""

from dataclasses import dataclass


@dataclass
class Account:
    username: str
    salt: str
    password_hash: str

    def to_dict(self):
        return {
            "username": self.username,
            "salt": self.salt,
            "password_hash": self.password_hash,
        }

    @staticmethod
    def from_dict(data):
        return Account(
            username=data["username"],
            salt=data["salt"],
            password_hash=data["password_hash"],
        )