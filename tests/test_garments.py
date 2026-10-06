"""Storage-layer tests for merchants, garment metrics, looks and body profiles."""

import json
import threading

import pytest

from itp.garments import (
    AlreadyExists,
    MerchantStore,
    QuotaExceeded,
    normalize_body_profile,
    normalize_look,
    normalize_metrics,
    public_body_profile,
    public_garment,
    public_look,
    public_merchant,
)
from itp.merchant_auth import hash_password


@pytest.fixture
def store(tmp_path):
    return MerchantStore(tmp_path)


def make_merchant(store, name="shop-one", *, quota=50, password="secret-password"):
    return store.create_merchant(
        name=name,
        display_name=f"{name} 店铺",
        contact="owner@example.com",
        password_hash=hash_password(password),
        quota=quota,
    )


def make_metrics(**overrides):
    document = {"category": "上装", "name": "落肩针织开衫", "status": "draft"}
    document.update(overrides)
    return normalize_metrics(document)


# ------------------------------------------------------------------ merchants


def test_merchant_roundtrip_and_unique_name(store):
    merchant = make_merchant(store)
    assert store.merchant(merchant["id"])["name"] == "shop-one"
    assert store.merchant_by_name("shop-one")["id"] == merchant["id"]
    assert store.merchant("0" * 32) is None
    assert store.merchant(None) is None
    assert store.merchant_by_name("nobody") is None
    with pytest.raises(AlreadyExists):
        make_merchant(store)
    # Names are compared exactly, so casing forms a distinct account.
    other = make_merchant(store, name="Shop-One")
    assert other["id"] != merchant["id"]
    assert public_merchant(merchant, garment_count=0)["garment_count"] == 0


def test_password_and_disabled_flags_update(store):
    merchant = make_merchant(store)
    assert store.set_password(merchant["id"], hash_password("new-password")) is True
    assert store.merchant(merchant["id"])["password_hash"] != merchant["password_hash"]
    assert store.set_password("0" * 32, "x") is False
    assert store.set_disabled(merchant["id"], True) is True
    assert store.merchant(merchant["id"])["disabled"] is True
    assert store.set_disabled(merchant["id"], False) is True
    assert store.merchant(merchant["id"])["disabled"] is False
    assert store.set_quota(merchant["id"], 7) is True
    assert store.merchant(merchant["id"])["quota"] == 7


# ------------------------------------------------------------------- garments


def test_garments_are_scoped_to_their_merchant(store):
    first = make_merchant(store, "shop-one")
    second = make_merchant(store, "shop-two")
    mine = store.create_garment(first["id"], make_metrics(name="我的商品"))
    theirs = store.create_garment(second["id"], make_metrics(name="别人的商品"))

    assert store.garment(mine["id"])["merchant_id"] == first["id"]
    assert store.garment_for(first["id"], mine["id"]) is not None
    assert store.garment_for(first["id"], theirs["id"]) is None
    assert store.garment_for(second["id"], mine["id"]) is None
    assert store.count_garments(first["id"]) == 1
    assert store.count_garments(second["id"]) == 1
    assert store.garment("0" * 32) is None

    total, items = store.list_garments(first["id"])
    assert total == 1 and [item["id"] for item in items] == [mine["id"]]
    assert store.update_garment(first["id"], theirs["id"], make_metrics()) is None
    assert store.delete_garment(first["id"], theirs["id"]) is None


def test_garment_list_filters_status_and_pages(store):
    merchant = make_merchant(store)
    ids = [
        store.create_garment(merchant["id"], make_metrics(name=f"商品{index}", status=status))["id"]
        for index, status in enumerate(
            ["draft", "published", "draft", "published", "draft"]
        )
    ]
    total, everything = store.list_garments(merchant["id"], limit=100)
    assert total == 5 and len(everything) == 5
    assert {item["id"] for item in everything} == set(ids)

    draft_total, drafts = store.list_garments(merchant["id"], status="draft")
    assert draft_total == 3
    assert all(item["status"] == "draft" for item in drafts)

    first_page = store.list_garments(merchant["id"], limit=2)[1]
    second_page = store.list_garments(merchant["id"], limit=2, offset=2)[1]
    assert len(first_page) == 2 and len(second_page) == 2
    assert not {item["id"] for item in first_page} & {item["id"] for item in second_page}
    assert [item["id"] for item in first_page + second_page] == [
        item["id"] for item in everything[:4]
    ]


def test_garment_status_and_metrics_update(store):
    merchant = make_merchant(store)
    garment = store.create_garment(merchant["id"], make_metrics())
    merged = normalize_metrics(
        {"status": "published", "measurements": {"bust_cm": 92}}, base=garment["metrics"]
    )
    updated = store.update_garment(merchant["id"], garment["id"], merged)
    assert updated["status"] == "published"
    assert updated["metrics"]["measurements"]["bust_cm"] == 92
    assert updated["updated"] >= garment["updated"]


def test_quota_is_enforced_without_partial_rows(store):
    merchant = make_merchant(store, quota=2)
    store.create_garment(merchant["id"], make_metrics(name="一"))
    store.create_garment(merchant["id"], make_metrics(name="二"))
    with pytest.raises(QuotaExceeded):
        store.create_garment(merchant["id"], make_metrics(name="三"))
    assert store.count_garments(merchant["id"]) == 2

    store.set_quota(merchant["id"], 3)
    store.create_garment(merchant["id"], make_metrics(name="三"))
    assert store.count_garments(merchant["id"]) == 3


def test_sku_is_unique_within_a_merchant(store):
    first = make_merchant(store, "shop-one")
    second = make_merchant(store, "shop-two")
    store.create_garment(first["id"], make_metrics(sku="ITP-001"))
    with pytest.raises(AlreadyExists):
        store.create_garment(first["id"], make_metrics(sku="ITP-001", name="另一件"))
    store.create_garment(second["id"], make_metrics(sku="ITP-001"))
    # A patch may not collide with a sibling either.
    other = store.create_garment(first["id"], make_metrics(sku="ITP-002", name="第三件"))
    with pytest.raises(AlreadyExists):
        store.update_garment(
            first["id"], other["id"], normalize_metrics({"sku": "ITP-001"}, base=other["metrics"])
        )


def test_images_are_ordered_and_removed_with_the_garment(store):
    merchant = make_merchant(store)
    garment = store.create_garment(merchant["id"], make_metrics())
    images = [store.add_image(garment["id"], f"{index:032x}") for index in range(3)]
    assert [image["position"] for image in images] == [0, 1, 2]
    assert [item["id"] for item in store.images_for(garment["id"])] == [
        image["id"] for image in images
    ]
    assert store.images_for_many([garment["id"]])[garment["id"]] == store.images_for(garment["id"])
    assert store.images_for_many([]) == {}

    assert store.delete_image(merchant["id"], garment["id"], images[1]["id"]) == f"{1:032x}"
    assert store.delete_image(merchant["id"], garment["id"], images[1]["id"]) is None
    assert [item["position"] for item in store.images_for(garment["id"])] == [0, 2]

    asset_ids = store.delete_garment(merchant["id"], garment["id"])
    assert set(asset_ids) == {f"{0:032x}", f"{2:032x}"}
    assert store.garment(garment["id"]) is None
    assert store.images_for(garment["id"]) == []


def test_image_lookup_rejects_foreign_ids(store):
    merchant = make_merchant(store)
    garment = store.create_garment(merchant["id"], make_metrics())
    image = store.add_image(garment["id"], "a" * 32)
    assert store.image(image["id"]) is not None
    for bad in ("", "a" * 31, "A" * 32, "../evil", "a" * 33, "zz" + "a" * 30):
        assert store.image(bad) is None
    assert store.delete_image("0" * 32, garment["id"], image["id"]) is None
    assert store.delete_image(merchant["id"], garment["id"], "b" * 32) is None


# ---------------------------------------------------------------------- looks


def test_look_members_must_be_own_garments(store):
    first = make_merchant(store, "shop-one")
    second = make_merchant(store, "shop-two")
    mine = store.create_garment(first["id"], make_metrics(name="我的商品"))
    theirs = store.create_garment(second["id"], make_metrics(name="别人的商品"))

    with pytest.raises(ValueError):
        store.create_look(first["id"], normalize_look({
            "name": "越权穿搭", "status": "draft", "items": [theirs["id"]],
        }))
    with pytest.raises(ValueError):
        store.create_look(first["id"], normalize_look({
            "name": "幽灵穿搭", "status": "draft", "items": ["0" * 32],
        }))

    second_garment = store.create_garment(first["id"], make_metrics(name="第二件"))
    look = store.create_look(first["id"], normalize_look({
        "name": "通勤两件套",
        "story": "把锋利收进软结构里。",
        "style": "通勤",
        "season": "四季",
        "occasion": "通勤办公",
        "palette": ["#c9d6bd", "#4a5b52"],
        "status": "draft",
        "items": [mine["id"], second_garment["id"]],
    }))
    assert look["items"] == [mine["id"], second_garment["id"]]
    assert store.look_for(first["id"], look["id"])["name"] == "通勤两件套"
    assert store.look_for(second["id"], look["id"]) is None
    assert store.look("0" * 32) is None

    updated = store.update_look(first["id"], look["id"], normalize_look(
        {"items": [second_garment["id"]]}, base=look
    ))
    assert updated["items"] == [second_garment["id"]]
    assert store.update_look(second["id"], look["id"], normalize_look({}, base=look)) is None

    total, looks = store.list_looks(first["id"])
    assert total == 1 and looks[0]["id"] == look["id"]
    assert store.delete_look(second["id"], look["id"]) is False
    assert store.delete_look(first["id"], look["id"]) is True
    assert store.look(look["id"]) is None


def test_deleting_a_garment_drops_it_from_looks(store):
    merchant = make_merchant(store)
    garment = store.create_garment(merchant["id"], make_metrics())
    look = store.create_look(merchant["id"], normalize_look({
        "name": "单件穿搭", "status": "draft", "items": [garment["id"]],
    }))
    store.delete_garment(merchant["id"], garment["id"])
    assert store.look(look["id"])["items"] == []


def test_public_lists_only_published(store):
    merchant = make_merchant(store)
    published = store.create_garment(merchant["id"], make_metrics(
        name="已发布", status="published", style="通勤", season="四季", occasion="通勤办公",
    ))
    store.create_garment(merchant["id"], make_metrics(name="草稿", status="draft", style="通勤"))

    total, items = store.list_published_garments()
    assert total == 1 and [item["id"] for item in items] == [published["id"]]
    for filters in (
        {"style": "通勤"},
        {"season": "四季"},
        {"occasion": "通勤办公"},
        {"category": "上装"},
    ):
        assert store.list_published_garments(**filters)[0] == 1
    assert store.list_published_garments(style="街头")[0] == 0

    look = store.create_look(merchant["id"], normalize_look({
        "name": "已发布穿搭", "status": "published", "style": "通勤", "items": [published["id"]],
    }))
    store.create_look(merchant["id"], normalize_look({
        "name": "草稿穿搭", "status": "draft", "items": [published["id"]],
    }))
    total, looks = store.list_published_looks()
    assert total == 1 and looks[0]["id"] == look["id"]
    assert store.list_published_looks(style="街头")[0] == 0
    assert store.garments_by_ids([published["id"]], published_only=True) != []
    assert store.garments_by_ids([published["id"]], published_only=False) != []
    assert store.garments_by_ids([]) == []


def test_public_documents_shape(store):
    merchant = make_merchant(store)
    garment = store.create_garment(merchant["id"], make_metrics())
    image = store.add_image(garment["id"], "c" * 32)
    document = public_garment(garment, [image])
    assert document["images"][0]["url"] == f"/api/garment-images/{image['id']}"
    look = store.create_look(merchant["id"], normalize_look({
        "name": "形状检查", "status": "published", "items": [garment["id"]],
    }))
    composed = public_look(look, [garment], {garment["id"]: [image]})
    assert composed["items"][0]["id"] == garment["id"]
    assert composed["items"][0]["images"][0]["id"] == image["id"]


# --------------------------------------------------------------- body profile


def test_body_profile_job_priority_and_default_fallback(store):
    assert store.body_profile() is None
    default = store.save_body_profile(normalize_body_profile({"height_cm": 170, "weight_kg": 60}))
    assert default["job_id"] is None
    assert store.body_profile()["height_cm"] == 170

    specific = store.save_body_profile(
        normalize_body_profile({"height_cm": 180}), "job-alpha"
    )
    assert specific["job_id"] == "job-alpha"
    assert store.body_profile("job-alpha")["height_cm"] == 180
    # An unknown job falls back to the default profile for reads only.
    assert store.body_profile("job-beta")["height_cm"] == 170
    assert store.body_profile("job-beta", fallback=False) is None
    assert store.body_profile("job-alpha", fallback=False)["height_cm"] == 180
    assert store.body_profile()["height_cm"] == 170
    # Without a job id the default row is the profile itself, so the update path
    # still finds a merge base when fallback is off.
    assert store.body_profile(None, fallback=False)["height_cm"] == 170

    # Upserts replace rather than accumulate.
    store.save_body_profile(normalize_body_profile({"height_cm": 171}))
    store.save_body_profile(normalize_body_profile({"height_cm": 172}), "job-alpha")
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM body_profiles").fetchone()[0] == 2
    assert store.body_profile()["height_cm"] == 171
    assert store.body_profile("job-alpha")["height_cm"] == 172

    skeleton = public_body_profile(None)
    assert skeleton["job_id"] is None
    assert all(skeleton[field] is None for field in
               ("height_cm", "weight_kg", "shoulder_cm", "bust_cm", "waist_cm", "hip_cm"))


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("document", [
    {"name": "缺分类", "status": "draft"},
    {"category": "上装", "status": "draft"},
    {"category": "上装", "name": "缺状态"},
    {"category": "帽子", "name": "未知分类", "status": "draft"},
    {"category": "上装", "name": "未知字段", "status": "draft", "colour": "red"},
    {"category": "上装", "name": "未知状态", "status": "live"},
    {"category": "上装", "name": "未知尺寸", "status": "draft", "measurements": {"neck_cm": 40}},
    {"category": "上装", "name": "尺寸为零", "status": "draft", "measurements": {"bust_cm": 0}},
    {"category": "上装", "name": "尺寸越界", "status": "draft", "measurements": {"bust_cm": 301}},
    {"category": "上装", "name": "尺寸非数", "status": "draft", "measurements": {"bust_cm": "90"}},
    {"category": "上装", "name": "尺寸NaN", "status": "draft",
     "measurements": {"bust_cm": float("nan")}},
    {"category": "上装", "name": "区间倒置", "status": "draft",
     "fit_ranges": {"height_cm": [190, 150]}},
    {"category": "上装", "name": "区间越界", "status": "draft",
     "fit_ranges": {"height_cm": [100, 240]}},
    {"category": "上装", "name": "区间形状", "status": "draft", "fit_ranges": {"height_cm": [150]}},
    {"category": "上装", "name": "区间类型", "status": "draft", "fit_ranges": {"height_cm": 150}},
    {"category": "上装", "name": "区间非数", "status": "draft",
     "fit_ranges": {"height_cm": ["150", 160]}},
    {"category": "上装", "name": "未知属性", "status": "draft", "attributes": {"fabric": "棉"}},
    {"category": "上装", "name": "廓形错", "status": "draft", "attributes": {"silhouette": "紧身"}},
    {"category": "上装", "name": "弹力错", "status": "draft", "attributes": {"stretch": "低弹"}},
    {"category": "上装", "name": "长度错", "status": "draft",
     "attributes": {"length_type": "超长"}},
    {"category": "上装", "name": "颜色大写", "status": "draft", "attributes": {"color": "#AABBCC"}},
    {"category": "上装", "name": "颜色短", "status": "draft", "attributes": {"color": "#abc"}},
    {"category": "上装", "name": "颜色词", "status": "draft", "attributes": {"color": "red"}},
    {"category": "上装", "name": "克重低", "status": "draft", "attributes": {"weight_gsm": 19}},
    {"category": "上装", "name": "克重高", "status": "draft", "attributes": {"weight_gsm": 2001}},
    {"category": "上装", "name": "克重小数", "status": "draft",
     "attributes": {"weight_gsm": 120.5}},
    {"category": "上装", "name": "风格错", "status": "draft", "style": "商务"},
    {"category": "上装", "name": "季节错", "status": "draft", "season": "梅雨"},
    {"category": "上装", "name": "价格负数", "status": "draft", "price_cents": -1},
    {"category": "上装", "name": "价格非整数", "status": "draft", "price_cents": 199.5},
    {"category": "上装", "name": "价格过大", "status": "draft", "price_cents": 10**13},
    {"category": "上装", "name": "介绍过长", "status": "draft", "description": "长" * 1001},
    {"category": "上装", "name": "建议过多", "status": "draft",
     "tips": ["一", "二", "三", "四"]},
    {"category": "上装", "name": "建议过长", "status": "draft", "tips": ["长" * 121]},
    {"category": "上装", "name": "建议非数组", "status": "draft", "tips": "一条"},
    {"category": "上装", "name": "控制字符", "status": "draft", "sku": "A\u0001B"},
])
def test_metrics_reject_invalid_documents(document):
    with pytest.raises(ValueError):
        normalize_metrics(document)


def test_metrics_accept_the_documented_values():
    document = normalize_metrics({
        "category": "外套",
        "name": "落肩薄呢外套",
        "sku": "ITP-100",
        "brand": "示例品牌",
        "price_cents": 129900,
        "measurements": {"shoulder_cm": 52, "bust_cm": 108.5, "length_cm": 76},
        "fit_ranges": {"height_cm": [155, 185], "bust_cm": [80, 100]},
        "attributes": {
            "silhouette": "宽松", "stretch": "微弹", "weight_gsm": 320,
            "color": "#4a5b52", "length_type": "常规",
        },
        "style": "通勤",
        "season": "秋",
        "occasion": "通勤办公",
        "description": "肩线自然下落。",
        "tips": ["内搭保持同色"],
        "status": "published",
    })
    assert document["measurements"]["bust_cm"] == 108.5
    assert document["measurements"]["waist_cm"] is None
    assert document["fit_ranges"]["height_cm"] == [155.0, 185.0]
    assert document["attributes"]["weight_gsm"] == 320
    assert document["status"] == "published"
    assert set(document) == {
        "category", "name", "sku", "brand", "price_cents", "measurements", "fit_ranges",
        "attributes", "style", "season", "occasion", "description", "tips", "status",
    }
    # Optional fields clear with an explicit null.
    cleared = normalize_metrics({"sku": None, "description": None}, base=document)
    assert cleared["sku"] is None and cleared["description"] is None


def test_metrics_patch_merges_sections():
    base = normalize_metrics({
        "category": "上装", "name": "针织衫", "status": "draft",
        "measurements": {"bust_cm": 90, "waist_cm": 70},
        "attributes": {"color": "#ffffff", "stretch": "高弹"},
    })
    patched = normalize_metrics({"measurements": {"bust_cm": 95}}, base=base)
    assert patched["measurements"] == {
        "shoulder_cm": None, "bust_cm": 95.0, "waist_cm": 70.0,
        "hip_cm": None, "length_cm": None, "hem_cm": None,
    }
    assert patched["attributes"]["stretch"] == "高弹"
    emptied = normalize_metrics({"measurements": None}, base=base)
    assert all(value is None for value in emptied["measurements"].values())
    cleared = normalize_metrics({"attributes": {"color": None}}, base=base)
    assert cleared["attributes"]["color"] is None
    assert cleared["attributes"]["stretch"] == "高弹"


def test_look_and_body_profile_validation():
    with pytest.raises(ValueError):
        normalize_look({"name": "", "status": "draft"})
    with pytest.raises(ValueError):
        normalize_look({"name": "穿搭", "status": "published", "items": ["not-an-id"]})
    with pytest.raises(ValueError):
        normalize_look({"name": "穿搭", "status": "published", "items": ["a" * 32, "a" * 32]})
    with pytest.raises(ValueError):
        normalize_look({"name": "穿搭", "status": "published", "palette": ["#AABBCC"]})
    with pytest.raises(ValueError):
        normalize_look({"name": "穿搭", "status": "published", "extra": 1})
    with pytest.raises(ValueError):
        normalize_look({"name": "穿搭", "status": "published",
                        "items": [f"{index:032x}" for index in range(13)]})
    accepted = normalize_look({"name": "通勤", "status": "published"})
    assert accepted["items"] == [] and accepted["palette"] is None

    with pytest.raises(ValueError):
        normalize_body_profile({"height_cm": 99})
    with pytest.raises(ValueError):
        normalize_body_profile({"weight_kg": 301})
    with pytest.raises(ValueError):
        normalize_body_profile({"waist_cm": "70"})
    with pytest.raises(ValueError):
        normalize_body_profile({"chest_cm": 90})
    assert normalize_body_profile({"height_cm": None}) == {
        "height_cm": None, "weight_kg": None, "shoulder_cm": None,
        "bust_cm": None, "waist_cm": None, "hip_cm": None,
    }


def test_metrics_document_is_valid_json_round_trip(store):
    merchant = make_merchant(store)
    metrics = make_metrics(
        description="第一行\n第二行", tips=["收腰"], attributes={"color": "#123abc"}
    )
    garment = store.create_garment(merchant["id"], metrics)
    reloaded = json.loads(json.dumps(store.garment(garment["id"])["metrics"], ensure_ascii=False))
    assert reloaded == metrics


# --------------------------------------------------------------- concurrency


def test_concurrent_writes_keep_the_store_consistent(store):
    merchant = make_merchant(store, quota=1000)
    errors: list[Exception] = []

    def worker(index: int):
        try:
            for step in range(4):
                store.create_garment(
                    merchant["id"], make_metrics(name=f"{index}-{step}")
                )
        except Exception as exc:  # pragma: no cover - only on a broken lock
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert store.count_garments(merchant["id"]) == 24


def test_concurrent_registration_creates_one_account(store):
    results: list[str] = []
    errors: list[Exception] = []

    def worker():
        try:
            merchant = store.create_merchant(
                name="race-shop",
                display_name="并发店铺",
                contact="",
                password_hash=hash_password("secret-password"),
                quota=5,
            )
            results.append(merchant["id"])
        except AlreadyExists:
            errors.append(AlreadyExists("taken"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 3
    assert store.merchant_by_name("race-shop")["id"] == results[0]
