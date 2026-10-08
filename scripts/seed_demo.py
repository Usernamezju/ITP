"""Seed the two demonstration shops. Development and test deployments only.

    ITP_DEMO_ENABLED=true ITP_ENVIRONMENT=development \
        python scripts/seed_demo.py

Creates two differently named virtual merchants with example products, and
gives both the server-side demonstration entitlement.  The entitlement lifts
the upload, recommendation, AI-description and modelling limits; it does not
write a balance, so a demonstration shop cannot be mistaken for a funded one,
and `BenefitsStore.grant_demo` refuses outright whenever the deployment is not
allowed to have demo privileges.

The passwords are random unless `ITP_DEMO_PASSWORD` supplies one.  They are
stored only as hashes, printed once to this terminal so the operator can sign
in, and never written to a file.  Running the script again leaves existing
accounts, their passwords and their products alone.

Works directly on the data directory, so no server has to be running.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from itp.benefits import BenefitsStore  # noqa: E402
from itp.commerce import CommerceError, CommerceStore  # noqa: E402
from itp.config import Settings  # noqa: E402
from itp.garments import AlreadyExists, MerchantStore, normalize_metrics  # noqa: E402
from itp.merchant_auth import hash_password  # noqa: E402

# Two shops that are recognisably not each other, so a demonstration can show a
# product from one beside a product from the other.
SHOPS = [
    {
        "name": "demo-atelier",
        "display_name": "演示店铺 · 织间",
        "contact": "演示用途，请勿付款",
        "garments": [
            {
                "category": "上装", "name": "细罗纹半高领针织", "status": "published",
                "brand": "演示品牌", "price_cents": 26900,
                "measurements": {"shoulder_cm": 39, "bust_cm": 100, "length_cm": 62,
                                 "hem_cm": 96},
                "fit_ranges": {"height_cm": [158, 176], "bust_cm": [86, 96],
                               "waist_cm": [64, 78], "shoulder_cm": [36, 40]},
                "attributes": {"silhouette": "标准", "stretch": "微弹", "weight_gsm": 260,
                               "color": "#c9d6bd", "length_type": "常规"},
                "style": "通勤", "season": "四季", "occasion": "通勤办公",
                "description": "雾面罗纹针织，可外穿也可内搭。（演示数据，尺寸为估算值）",
                "tips": ["上衣下摆收进裤腰，抬高腰线"],
            },
            {
                "category": "下装", "name": "高腰直筒西裤", "status": "published",
                "brand": "演示品牌", "price_cents": 32900,
                "measurements": {"waist_cm": 72, "hip_cm": 104, "length_cm": 100,
                                 "hem_cm": 44},
                "fit_ranges": {"height_cm": [162, 176], "waist_cm": [66, 76],
                               "hip_cm": [90, 100]},
                "attributes": {"silhouette": "标准", "stretch": "无弹", "weight_gsm": 240,
                               "color": "#4a5b52", "length_type": "常规"},
                "style": "通勤", "season": "四季", "occasion": "通勤办公",
                "description": "垂坠西裤，裤线笔直。（演示数据，尺寸为估算值）",
                "tips": ["选择与上衣同色系更显高"],
            },
        ],
    },
    {
        "name": "demo-outfitters",
        "display_name": "演示店铺 · 屿岸",
        "contact": "演示用途，请勿付款",
        "garments": [
            {
                "category": "外套", "name": "落肩轻薄风衣", "status": "published",
                "brand": "演示品牌", "price_cents": 58900,
                "measurements": {"shoulder_cm": 48, "bust_cm": 116, "length_cm": 108},
                "fit_ranges": {"height_cm": [165, 185], "bust_cm": [92, 112]},
                "attributes": {"silhouette": "宽松", "stretch": "无弹", "weight_gsm": 300,
                               "color": "#8d7a63", "length_type": "长款"},
                "style": "休闲", "season": "秋", "occasion": "日常出行",
                "description": "落肩风衣，内搭空间充足。（演示数据，尺寸为估算值）",
                "tips": ["内搭不超过两层，避免堆叠"],
            },
            {
                "category": "上装", "name": "亚麻短袖衬衫", "status": "published",
                "brand": "演示品牌", "price_cents": 19900,
                "measurements": {"shoulder_cm": 44, "bust_cm": 106, "length_cm": 70},
                "fit_ranges": {"height_cm": [160, 180], "bust_cm": [86, 104]},
                "attributes": {"silhouette": "宽松", "stretch": "无弹", "weight_gsm": 160,
                               "color": "#e8ddc8", "length_type": "常规"},
                "style": "度假", "season": "夏", "occasion": "旅行度假",
                "description": "亚麻短袖衬衫，透气轻薄。（演示数据，尺寸为估算值）",
                "tips": ["浅色反射热量，适合高温"],
            },
        ],
    },
]


def resolve_password(shop: str) -> tuple[str, bool]:
    """The password to use for one shop, and whether the operator supplied it.

    Each shop gets its own random password unless the operator injected one, so
    a single leaked demonstration login does not open the other shop.
    """
    supplied = os.environ.get("ITP_DEMO_PASSWORD", "")
    if supplied:
        if len(supplied) < 8:
            raise SystemExit("ITP_DEMO_PASSWORD 至少 8 位")
        return supplied, True
    del shop  # the value is random per call; the name is here for readability
    return secrets.token_urlsafe(12), False


def seed(settings: Settings) -> int:
    merchants = MerchantStore(settings.data_dir, settings)
    commerce = CommerceStore(merchants, settings)
    benefits = BenefitsStore(commerce, settings)

    created_accounts: list[tuple[str, str]] = []
    reused_accounts: list[str] = []
    products = 0
    supplied = bool(os.environ.get("ITP_DEMO_PASSWORD", ""))

    for shop in SHOPS:
        existing = merchants.merchant_by_name(shop["name"])
        if existing:
            merchant = existing
            reused_accounts.append(shop["name"])
            print(f"  已存在，保留原密码：{shop['name']}")
        else:
            password, supplied = resolve_password(shop["name"])
            try:
                merchant = merchants.create_merchant(
                    name=shop["name"],
                    display_name=shop["display_name"],
                    contact=shop["contact"],
                    password_hash=hash_password(password),
                    quota=settings.merchant_quota,
                )
            except AlreadyExists:
                merchant = merchants.merchant_by_name(shop["name"])
            else:
                created_accounts.append((shop["name"], password))
                print(f"  已创建：{shop['name']}（{shop['display_name']}）")

        owned = {item["metrics"]["name"] for item in merchants.all_garments(merchant["id"])}
        for document in shop["garments"]:
            if document["name"] in owned:
                continue
            merchants.create_garment(merchant["id"], normalize_metrics(dict(document)))
            products += 1

        try:
            benefits.grant_demo(merchant["id"])
        except CommerceError as exc:
            raise SystemExit(f"无法授予演示特权：{exc}") from exc

    print(f"\n商品：新增 {products} 件；演示特权已授予 {len(SHOPS)} 个账号。")
    if created_accounts and supplied:
        print("\n账号已创建，密码由 ITP_DEMO_PASSWORD 提供，此处不再回显。")
    elif created_accounts:
        print("\n登录信息（只显示这一次，请立即记录）：")
        for name, secret in created_accounts:
            print(f"  {name}  /  {secret}")
    if reused_accounts:
        print(f"\n未改动密码的账号：{'、'.join(reused_accounts)}")
    print(
        "\n演示特权由 ITP_DEMO_ENABLED 控制，且只在 development/test 且未绑定公网域名时"
        "\n才允许开启；生产部署既无法开启该开关，也无法通过注册接口取得这份特权。"
        "\n撤销：python scripts/seed_demo.py --revoke"
    )
    return 0


def revoke(settings: Settings) -> int:
    """Take the entitlement away, leaving the accounts and their products."""
    merchants = MerchantStore(settings.data_dir, settings)
    benefits = BenefitsStore(CommerceStore(merchants, settings), settings)
    if not benefits.demo_enabled:
        print("演示特权未启用：ITP_DEMO_ENABLED 未设置为 true，无需撤销。")
        return 0
    marked = set(benefits.demo_accounts())
    removed = 0
    for name in [shop["name"] for shop in SHOPS]:
        merchant = merchants.merchant_by_name(name)
        if merchant and merchant["id"] in marked:
            benefits.grant_demo(merchant["id"], granted=False)
            removed += 1
    print(f"已撤销 {removed} 个账号的演示特权；账号与商品保留。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="数据目录，默认取 ITP_DATA_DIR 或 ./data")
    parser.add_argument("--revoke", action="store_true",
                        help="只撤销演示特权，不创建任何账号或商品")
    args = parser.parse_args()

    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))

    # The setting is the lock: it cannot be true outside a local development or
    # test deployment, and the settings model refuses the combination outright.
    if not settings.demo_enabled and not args.revoke:
        raise SystemExit(
            "演示特权未启用。请以 ITP_ENVIRONMENT=development ITP_DEMO_ENABLED=true 运行；"
            "生产部署不会开启该选项。"
        )
    if args.revoke:
        return revoke(settings)

    print(f"环境：{settings.environment}｜数据目录：{settings.data_dir.resolve()}")
    return seed(settings)


if __name__ == "__main__":
    sys.exit(main())
