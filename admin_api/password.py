"""Generate a password hash without ever writing the plaintext to a file."""

from getpass import getpass

from argon2 import PasswordHasher


def main() -> None:
    password = getpass("New admin password: ")
    confirmation = getpass("Repeat password: ")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(PasswordHasher().hash(password))


if __name__ == "__main__":
    main()
