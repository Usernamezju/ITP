"""Grant a membership to an account without a payment. Operator-only.

    python scripts/grant_membership.py --account fan --plan customer_monthly --months 12

This exists because a membership was once written into the live database by
hand: the row carried an `operator-membership:` reference that no code in this
repository could produce, so nobody could tell what had been granted, on what
authority, or how to do it again. The grant itself is legitimate — an operator
sometimes needs to hand out a membership — so it gets a real path instead of a
manual INSERT.

What it does *not* do is pretend money moved. No payment order, transaction or
wallet entry is created, and the subscription records the operator reference
rather than an order id. Everything else follows the paid path exactly,
because it calls the same `CommerceStore.grant_subscription` that a verified
payment calls: the period chains onto any membership of the same plan, the
entitlements are copied from the plan, and a first customer membership earns
its one-time 1000 points.

Granting several months writes one subscription per month, so the anniversary
anchor and the month-end renewal rules behave the same as a customer who kept
renewing. Benefits that are counted per active cycle — the five makeup cards,
for instance — are derived from the cycle in force at the time, not from the
number of rows, so a twelve-month grant does not hand out twelve months of
cards up front.

Running the same command twice on the same day changes nothing: the reference
is derived from the account, the plan, the month offset and the date.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from itp.commerce import CommerceError, CommerceStore  # noqa: E402
from itp.config import Settings  # noqa: E402
from itp.garments import MerchantStore  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def operator_reference(account: str, plan_id: str, offset: int, day: str) -> str:
    """A stable id for one granted period, so the same command is a no-op twice.

    Including the day means a deliberate second run tomorrow extends the
    membership rather than being swallowed as a replay.
    """
    key = f"itp-operator-membership|{account}|{plan_id}|{offset}|{day}"
    return f"operator-membership:{uuid5(NAMESPACE_URL, key).hex}"


def membership_end(conn, user_id: str, plan_id: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(ends) FROM subscriptions WHERE user_id=? AND plan_id=?",
        (user_id, plan_id),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def grant(settings: Settings, account: str, plan_id: str, months: int) -> dict:
    """Grant `months` periods of `plan_id` to `account`, in one transaction."""
    merchants = MerchantStore(settings.data_dir, settings)
    commerce = CommerceStore(merchants, settings)

    user = merchants.merchant_by_name(account)
    if not user:
        raise SystemExit(f"账号不存在：{account}")
    plan = next(
        (item for item in commerce.prices()["plans"] if item["id"] == plan_id), None
    )
    if not plan:
        raise SystemExit(f"套餐不存在或未启用：{plan_id}")

    day = datetime.now(TZ).date().isoformat()
    with merchants.connect() as conn:
        before = membership_end(conn, user["id"], plan["id"])
        points_before = conn.execute(
            "SELECT balance FROM point_wallets WHERE user_id=?", (user["id"],)
        ).fetchone()
        conn.execute("BEGIN IMMEDIATE")
        for offset in range(months):
            commerce.grant_subscription(
                conn, user["id"], plan, operator_reference(account, plan["id"], offset, day)
            )
        conn.commit()
        after = membership_end(conn, user["id"], plan["id"])
        points_after = conn.execute(
            "SELECT balance FROM point_wallets WHERE user_id=?", (user["id"],)
        ).fetchone()

    return {
        "account": account,
        "user_id": user["id"],
        "plan": plan,
        "months": months,
        "before": before,
        "after": after,
        "points_before": (points_before[0] if points_before else 0),
        "points_after": (points_after[0] if points_after else 0),
    }


def stamp(value: int | None) -> str:
    return datetime.fromtimestamp(value, TZ).strftime("%Y-%m-%d %H:%M") if value else "—"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--account", required=True, help="账号名，例如 fan")
    parser.add_argument("--plan", default="customer_monthly",
                        help="套餐 id，默认 customer_monthly（顾客月会员）")
    parser.add_argument("--months", type=int, default=1, help="发放几个月，默认 1")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="数据目录，默认取 ITP_DATA_DIR 或 ./data")
    args = parser.parse_args()
    if args.months < 1 or args.months > 120:
        raise SystemExit("--months 需在 1 到 120 之间")

    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    print(f"环境：{settings.environment}｜数据目录：{settings.data_dir.resolve()}")
    try:
        result = grant(settings, args.account, args.plan, args.months)
    except CommerceError as exc:
        raise SystemExit(f"发放失败：{exc}") from exc

    plan = result["plan"]
    print(
        f"\n{result['account']}（{result['user_id']}）\n"
        f"  套餐：{plan['name']}（{plan['id']}）× {result['months']}\n"
        f"  有效期：{stamp(result['before'])} → {stamp(result['after'])}\n"
        f"  积分：{result['points_before']} → {result['points_after']}"
    )
    print(
        "\n本次发放不产生支付订单、渠道交易或钱包流水，订阅记录的是运维编号"
        "\n（operator-membership:*）。首次顾客会员附带的 1000 积分每账号只发一次，"
        "\n重复执行不会重复发放。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
