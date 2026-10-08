"""Maintenance for encrypted private assets; keys are backed up separately."""

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from itp.config import Settings
from itp.private_assets import PrivateAssets


def backup(vault, destination):
    directory = destination / ("private-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    directory.mkdir(mode=0o700, parents=True)
    with vault.connect() as lock:
        lock.execute("BEGIN IMMEDIATE")
        with vault.connect() as source, sqlite3.connect(directory / "index.sqlite3") as target:
            source.backup(target)
        objects = directory / "objects"
        objects.mkdir(mode=0o700)
        for (key,) in lock.execute("SELECT object_key FROM objects WHERE object_key IS NOT NULL"):
            shutil.copy2(vault.objects / key, objects / key)
    (directory / "index.sqlite3").chmod(0o600)
    return directory


def main():
    parser = argparse.ArgumentParser(description="私有空间清理与加密备份；密钥需单独保管")
    parser.add_argument("action", choices=["cleanup", "backup"])
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    vault = PrivateAssets(Settings())
    if args.action == "cleanup":
        print(vault.cleanup())
    elif args.destination:
        print(backup(vault, args.destination))
    else:
        parser.error("backup requires --destination")


if __name__ == "__main__":
    main()
