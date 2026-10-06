"""Create the platform administrator account from the local machine.

The admin console (/admin) is read-only, and the public registration API
refuses the admin role outright, so this script is the only way to mint an
administrator.  It never generates or prints the password: the operator picks
one, either with --password, through ITP_ADMIN_PASSWORD, or interactively.

    python scripts/create_admin.py --name admin
    python scripts/create_admin.py --name ops --display-name "平台运维"
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from itp.garments import MerchantStore  # noqa: E402
from itp.merchant_auth import hash_password  # noqa: E402


def resolve_password(args) -> str | None:
    password = args.password or os.environ.get("ITP_ADMIN_PASSWORD")
    if password:
        return password
    first = getpass.getpass("管理员密码（8-128 位）：")
    if first != getpass.getpass("再输入一次："):
        print("两次输入不一致。")
        return None
    return first


def resolve_data_dir(args) -> Path:
    """Where the accounts live: --data-dir, then ITP_DATA_DIR, then the app .env.

    The deployed box keeps its data dir in the app .env (written by
    deploy/autodl/configure.py) and the documented command runs from the
    repository root, so the CLI must read the same file the API reads.
    """
    if args.data_dir:
        return Path(args.data_dir)
    if value := os.environ.get("ITP_DATA_DIR", "").strip():
        return Path(value)
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "ITP_DATA_DIR" and (value := value.strip().strip("'\"")):
                return Path(value)
    return Path(__file__).resolve().parents[1] / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="admin", help="管理员登录名，默认 admin")
    parser.add_argument("--display-name", default="平台管理员", help="显示名")
    parser.add_argument("--password", default=None,
                        help="密码；省略则读 ITP_ADMIN_PASSWORD 或交互式输入")
    parser.add_argument("--data-dir", default=None,
                        help="数据目录；默认取 ITP_DATA_DIR 环境变量或应用 .env，再退回 ./data")
    args = parser.parse_args()

    name = args.name.strip()
    if not name:
        print("管理员登录名不能为空。")
        return 1

    root = resolve_data_dir(args)
    if not (root / "merchants.sqlite3").exists():
        print(f"没有找到账号数据库：{root / 'merchants.sqlite3'}")
        print("先启动一次后端完成建库，再运行本脚本。")
        return 1

    store = MerchantStore(root)
    existing = store.merchant_by_name(name)
    if existing:
        print(f"账号 {name!r} 已存在（角色：{existing.get('role')}），拒绝覆盖。")
        print("如需该账号，可用 scripts/reset_merchant_password.py 重置其密码。")
        return 1

    password = resolve_password(args)
    if password is None:
        return 1
    if not 8 <= len(password) <= 128:
        print("密码长度需为 8-128 位。")
        return 1

    account = store.create_merchant(
        name=name,
        display_name=args.display_name.strip() or "平台管理员",
        contact="",
        password_hash=hash_password(password),
        quota=0,
        role="admin",
    )
    print(f"已创建管理员账号 {account['name']}（{account['display_name']}）。")
    print("该账号可登录 /admin 管理后台。后台只读；修改模型服务配置请编辑服务器 .env 后重启后端。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
