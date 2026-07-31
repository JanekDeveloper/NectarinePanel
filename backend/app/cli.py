"""Administrative command-line operations."""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionFactory
from app.models.entities import User, UserRole


async def create_admin(username: str, password: str, *, if_not_exists: bool = False) -> bool:
    """Create the single owner account and return whether it was created."""
    async with SessionFactory() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            if if_not_exists:
                return False
            raise RuntimeError(f"User {username!r} already exists")
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=UserRole.OWNER,
            )
        )
        await session.commit()
        return True


def main() -> None:
    """Parse CLI arguments and run the selected command."""
    parser = argparse.ArgumentParser(description="NectarinePanel administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin")
    create.add_argument("--username", default="admin")
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from standard input",
    )
    create.add_argument(
        "--if-not-exists",
        action="store_true",
        help="Exit successfully when the owner already exists",
    )
    args = parser.parse_args()
    if args.command == "create-admin":
        password = (
            sys.stdin.readline().rstrip("\r\n")
            if args.password_stdin
            else getpass.getpass("Admin password: ")
        )
        if len(password) < 12:
            parser.error("Password must contain at least 12 characters")
        created = asyncio.run(
            create_admin(args.username, password, if_not_exists=args.if_not_exists)
        )
        print("Owner created" if created else "Owner already exists")


if __name__ == "__main__":
    main()
