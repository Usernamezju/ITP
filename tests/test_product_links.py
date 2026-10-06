"""External destinations and additive garment schema migration."""
import sqlite3

import pytest

from itp.garments import MerchantStore, normalize_metrics, normalize_purchase_url
from test_merchant_api import env, auth, token_for


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hi", "//evil.test",
    "ftp://shop.test/item", "https://", "https://user:pass@shop.test/item",
    "https://shop.test\n/item", "https://shop.test\\@evil.test", "https://shop.test:99999",
    "https://shop.test/white space", "https://shop.test/" + "a" * 2048, 42])
def test_dangerous_links_are_rejected(url):
    with pytest.raises(ValueError):
        normalize_purchase_url(url)


def test_optional_url_roundtrip_edit_and_permission(env):
    client, settings, _ = env
    owner = auth(token_for(client))
    other = auth(token_for(client, "other-shop"))
    response = client.post("/api/merchant/garments", headers=owner, data={"payload":
        '{"category":"上装","name":"商品","status":"published",'
        '"purchase_url":"https://item.taobao.com/item.htm?id=123"}'})
    assert response.status_code == 201, response.text
    product = response.json()
    path = f"/api/merchant/garments/{product['id']}"
    assert client.patch(path, headers=other, json={"purchase_url": "https://jd.com"}).status_code == 404
    for bad in ("javascript:alert(1)", "//evil.test"):
        assert client.patch(path, headers=owner, json={"purchase_url": bad}).status_code == 422
    assert client.get(f"/api/garments/{product['id']}").json()["metrics"]["purchase_url"].endswith("id=123")
    recommendation = client.get('/api/outfits').json()['recommendations'][0]
    assert recommendation['items'][0]['garment_id'] == product['id']
    assert recommendation['items'][0]['merchant_id'] == product['merchant_id']
    assert recommendation['items'][0]['purchase_url'].endswith('id=123')
    edited = client.patch(path, headers=owner, json={"purchase_url": "http://shop.example/item"})
    assert edited.json()["metrics"]["purchase_url"] == "http://shop.example/item"
    # Omitted fields survive patches, null explicitly clears the destination.
    assert client.patch(path, headers=owner, json={"name": "新名称"}).json()["metrics"]["purchase_url"] == "http://shop.example/item"
    assert client.patch(path, headers=owner, json={"purchase_url": None}).json()["metrics"]["purchase_url"] is None
    with sqlite3.connect(settings.data_dir / "merchants.sqlite3") as conn:
        assert conn.execute("SELECT purchase_url FROM garments WHERE id=?", (product["id"],)).fetchone() == (None,)


def test_additive_migration_preserves_legacy_records(tmp_path):
    store = MerchantStore(tmp_path)
    owner = store.create_merchant(name="legacy", display_name="旧账号", contact="",
                                  password_hash="unchanged-hash", quota=5)
    garment = store.create_garment(owner["id"], normalize_metrics({
        "name": "旧商品", "category": "上装", "status": "published"}))
    with store.connect() as conn:
        store.commerce.credit_verified_order(conn, owner["id"], 1000, "old-credit")
    with store.connect() as conn:
        conn.execute("ALTER TABLE garments DROP COLUMN purchase_url")
        conn.execute("UPDATE garments SET metrics=json_remove(metrics, '$.purchase_url')")
    for _ in range(2):
        migrated = MerchantStore(tmp_path)
        assert migrated.merchant(owner["id"])["password_hash"] == "unchanged-hash"
        assert migrated.garment(garment["id"])["metrics"]["purchase_url"] is None
        assert migrated.commerce.summary(owner["id"])["balance_cents"] == 1000


def test_look_prices_and_purchase_entries_only_include_published_members(env):
    import json
    client, _, _ = env
    headers = auth(token_for(client))
    members = []
    for price, status in [(12900, 'published'), (5900, 'published'), (99900, 'draft')]:
        created = client.post('/api/merchant/garments', headers=headers, data={'payload': json.dumps({
            'category': '上装', 'name': f'商品-{price}', 'status': status, 'price_cents': price,
            'purchase_url': 'https://shop.example/item',
        })}).json()
        members.append(created['id'])
    client.post('/api/merchant/looks', headers=headers, json={
        'name': '套装', 'status': 'published', 'items': members})
    look = client.get('/api/outfits').json()['recommendations'][0]
    assert look['price_cents'] == 18800
    assert [item['garment_id'] for item in look['items']] == members[:2]
    client.patch(f'/api/merchant/garments/{members[0]}', headers=headers, json={'price_cents': None})
    assert client.get('/api/outfits').json()['recommendations'][0]['price_cents'] is None
