"""Seed the garment database through the merchant API.

Registers a merchant (or logs in when it already exists), imports a small set of
labelled example garments, and composes two looks from them.  The numbers are
演示用估算, not real measurements, so every description says so.

    python scripts/seed_garments.py --name demo-shop --password demo-pass-123

Needs a running server; see docs/modules/MERCHANT.md for the API itself.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

GARMENTS = [
    {
        "category": "上装", "name": "细罗纹半高领针织", "status": "published",
        "brand": "示例品牌", "price_cents": 26900,
        "measurements": {"shoulder_cm": 39, "bust_cm": 100, "length_cm": 62, "hem_cm": 96},
        "fit_ranges": {"height_cm": [158, 176], "bust_cm": [86, 96],
                       "waist_cm": [64, 78], "shoulder_cm": [36, 40]},
        "attributes": {"silhouette": "标准", "stretch": "微弹", "weight_gsm": 260,
                       "color": "#c9d6bd", "length_type": "常规"},
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
        "description": "雾面罗纹针织，贴身穿不扎，可外穿也可内搭。（示例数据，尺寸为演示用估算）",
        "tips": ["上衣下摆收进裤腰，抬高腰线", "肩宽偏窄的人可选落肩结构"],
    },
    {
        "category": "下装", "name": "高腰直筒西裤", "status": "published",
        "brand": "示例品牌", "price_cents": 32900,
        "measurements": {"waist_cm": 72, "hip_cm": 104, "length_cm": 100, "hem_cm": 44},
        "fit_ranges": {"height_cm": [162, 176], "waist_cm": [66, 76], "hip_cm": [90, 100]},
        "attributes": {"silhouette": "标准", "stretch": "无弹", "weight_gsm": 240,
                       "color": "#4a5b52", "length_type": "常规"},
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
        "description": "垂坠感直筒西裤，高腰设计拉长下身比例。（示例数据，尺寸为演示用估算）",
    },
    {
        "category": "外套", "name": "落肩薄呢外套", "status": "published",
        "brand": "示例品牌", "price_cents": 59900,
        "measurements": {"shoulder_cm": 44, "bust_cm": 108, "length_cm": 92},
        "fit_ranges": {"height_cm": [160, 178], "bust_cm": [84, 100], "shoulder_cm": [34, 42]},
        "attributes": {"silhouette": "宽松", "stretch": "无弹", "weight_gsm": 380,
                       "color": "#2f3a34", "length_type": "长款"},
        "style": "通勤", "season": "秋", "occasion": "商务会议",
        "description": "落肩结构弱化肩线，适合肩背宽阔的体型。（示例数据，尺寸为演示用估算）",
    },
    {
        "category": "上装", "name": "落肩连帽卫衣", "status": "published",
        "brand": "示例品牌", "price_cents": 19900,
        "measurements": {"shoulder_cm": 52, "bust_cm": 124, "length_cm": 68},
        "fit_ranges": {"height_cm": [158, 182], "bust_cm": [88, 112], "shoulder_cm": [36, 48]},
        "attributes": {"silhouette": "oversize", "stretch": "微弹", "weight_gsm": 420,
                       "color": "#5c6b58", "length_type": "常规"},
        "style": "街头", "season": "秋", "occasion": "日常出行",
        "description": "宽松落肩卫衣，出街叠穿不挑身材。（示例数据，尺寸为演示用估算）",
    },
    {
        "category": "下装", "name": "宽直筒工装裤", "status": "published",
        "brand": "示例品牌", "price_cents": 25900,
        "measurements": {"waist_cm": 78, "hip_cm": 112, "length_cm": 102},
        "fit_ranges": {"height_cm": [160, 182], "waist_cm": [68, 86], "hip_cm": [92, 112]},
        "attributes": {"silhouette": "宽松", "stretch": "无弹", "weight_gsm": 320,
                       "color": "#3f4a3c", "length_type": "长款"},
        "style": "街头", "season": "秋", "occasion": "日常出行",
        "description": "宽腿工装剪裁，垂坠不贴腿。（示例数据，尺寸为演示用估算）",
    },
    {
        "category": "鞋履", "name": "方头乐福鞋", "status": "published",
        "brand": "示例品牌", "price_cents": 45900,
        "measurements": {"length_cm": 25},
        "fit_ranges": {"height_cm": [155, 180]},
        "attributes": {"silhouette": "标准", "stretch": "无弹", "weight_gsm": 700,
                       "color": "#8d7a63", "length_type": "常规"},
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
        "description": "低跟方头，鞋型收敛不抢线条。（示例数据，尺寸为演示用估算）",
    },
]

LOOKS = [
    {"name": "示例 · 柔雾通勤", "story": "上装塞进高腰裤，把偏长的腿身比还给身高。",
     "style": "通勤", "season": "四季", "occasion": "通勤办公",
     "palette": ["#c9d6bd", "#4a5b52", "#8d7a63"], "status": "published",
     "items": ["细罗纹半高领针织", "高腰直筒西裤", "方头乐福鞋"]},
    {"name": "示例 · 街头层次", "story": "宽松落肩卫衣配宽腿工装裤，把体量藏在层次里。",
     "style": "街头", "season": "秋", "occasion": "日常出行",
     "palette": ["#5c6b58", "#3f4a3c"], "status": "published",
     "items": ["落肩连帽卫衣", "宽直筒工装裤"]},
]


def call(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 300:
        print(f"  {method} {path} -> {response.status_code} {response.text[:200]}")
        return None
    return response.json() if response.content else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--name", default="demo-shop", help="商家登录名")
    parser.add_argument("--password", default="demo-pass-123", help="至少 8 位")
    parser.add_argument("--display-name", default="示例商家")
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    with httpx.Client(base_url=base, timeout=30) as client:
        credentials = {"name": args.name, "password": args.password}
        registered = call(client, "POST", "api/merchant/register", json={
            **credentials, "display_name": args.display_name, "contact": "demo@example.com"})
        if registered:
            print(f"registered merchant {args.name}")
        login = call(client, "POST", "api/merchant/login", json=credentials)
        if not login:
            print("login failed; nothing imported")
            return 1
        client.headers["Authorization"] = f"Bearer {login['access_token']}"
        print("logged in")

        created: dict[str, str] = {}
        for garment in GARMENTS:
            payload = json.dumps(garment, ensure_ascii=False)
            made = call(client, "POST", "api/merchant/garments",
                        data={"payload": payload})
            if made is None:
                continue
            created[garment["name"]] = made["id"]
            print(f"  + 单品 {garment['name']} ({garment['category']})")

        for look in LOOKS:
            items = [created[name] for name in look["items"] if name in created]
            if not items:
                continue
            payload = {key: value for key, value in look.items() if key != "items"}
            payload["items"] = items
            made = call(client, "POST", "api/merchant/looks", json=payload)
            if made:
                print(f"  + 套装 {look['name']} ({len(items)} 件)")

    print("\n完成。打开 穿搭推荐 页即可看到商家发布的商品；"
          "它们在「人体建模」页填写身高/三围后按尺码指标排序。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
