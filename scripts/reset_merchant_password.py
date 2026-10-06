"""Reset a merchant's password from the local machine.

This is the way back in when the password is forgotten: the account lives in a
local SQLite file and there is no email or second factor, so whoever can read
that file can set a new password.  The new password revokes every token that was
issued before it, so any browser still holding one has to sign in again.

    python scripts/reset_merchant_password.py --list
    python scripts/reset_merchant_password.py --name demo-shop
    python scripts/reset_merchant_password.py --name demo-shop --password 新的密码1
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from itp.garments import MerchantStore  # noqa: E402
from itp.merchant_auth import hash_password  # noqa: E402

ALPHABET = string.ascii_letters + string.digits


def generate_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(16))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", help="商家账号（登录名）")
    parser.add_argument("--password", help="新密码；省略则生成一个 16 位随机密码")
    parser.add_argument("--data-dir", default=None,
                        help="数据目录，默认 ./data（与 ITP_DATA_DIR 一致）")
    parser.add_argument("--list", action="store_true", help="只列出账号，不改动任何东西")
    args = parser.parse_args()

    root = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parents[1] / "data"
    if not (root / "merchants.sqlite3").exists():
        print(f"没有找到商家数据库：{root / 'merchants.sqlite3'}")
        return 1
    store = MerchantStore(root)

    if args.list or not args.name:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT name, display_name, created FROM merchants ORDER BY created"
            ).fetchall()
        if not rows:
            print("还没有任何商家账号，可在「商家后台」页面注册。")
            return 0
        print(f"{len(rows)} 个账号：")
        for name, display, _created in rows:
            print(f"  {name:<16} {display or '—'}")
        if not args.name:
            print("\n加上 --name <账号> 即可重置密码。")
        return 0

    merchant = store.merchant_by_name(args.name.strip())
    if not merchant:
        print(f"没有名为 {args.name!r} 的商家账号；用 --list 看看有哪些。")
        return 1
    if merchant.get("disabled"):
        print("注意：该账号当前处于禁用状态，重置密码后仍然无法登录。")

    password = args.password or generate_password()
    if not 8 <= len(password) <= 128:
        print("新密码长度需为 8-128 位。")
        return 1

    store.set_password(merchant["id"], hash_password(password))
    print(f"已重置 {merchant['name']}（{merchant.get('display_name') or '未命名'}）的密码：")
    print(f"    {password}")
    print("\n旧令牌已全部失效；请在「商家后台」用这个密码重新登录，并尽快改成自己的密码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
