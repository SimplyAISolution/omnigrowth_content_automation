"""Bootstrap the first OWNER (ADR-0006): ``uv run poe create-owner --email you@example.com``.

The password is read interactively (never from argv, so it does not land in shell history)
or from ``OMNIGROWTH_OWNER_PASSWORD`` for scripted setups. Refuses if an OWNER exists.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from omnigrowth.core import aio
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.logging import configure_logging
from omnigrowth.core.settings import get_settings
from omnigrowth.services.auth import create_owner
from omnigrowth.storage.db import get_engine, get_sessionmaker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first OWNER user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default="Owner")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    password = os.environ.get("OMNIGROWTH_OWNER_PASSWORD")
    if not password:
        password = getpass.getpass("Password (min 12 chars): ")
        if password != getpass.getpass("Repeat password: "):
            sys.stderr.write("passwords do not match\n")
            return 2

    async def _run() -> str:
        try:
            async with get_sessionmaker()() as session:
                user = await create_owner(
                    session, email=args.email, display_name=args.display_name, password=password
                )
                return str(user.id)
        finally:
            await get_engine().dispose()

    try:
        user_id = aio.run(_run)
    except OmniGrowthError as exc:
        sys.stderr.write(f"error: {exc.code}: {exc.message}\n")
        return 1
    sys.stdout.write(f"OWNER created: {user_id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
