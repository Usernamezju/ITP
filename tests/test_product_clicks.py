"""Persisted clicks, business-day boundaries and merchant isolation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from itp.garments import MerchantStore, normalize_metrics
from test_merchant_api import env, auth, token_for


def product(client, token, **changes):
    import json
    response = client.post('/api/merchant/garments', headers=auth(token), data={'payload':
        json.dumps({'category': '上装', 'name': '点击商品', 'status': 'published',
                    'purchase_url': 'https://shop.example/item', **changes})})
    assert response.status_code == 201, response.text
    return response.json()


def test_click_api_persists_and_stats_cannot_be_read_by_another_merchant(env):
    client, settings, _ = env
    first, second = token_for(client), token_for(client, 'other-shop')
    mine, theirs = product(client, first), product(client, second)
    for _ in range(3):
        response = client.post(f"/api/garments/{mine['id']}/clicks")
        assert response.status_code == 201
        assert response.json()['purchase_url'] == 'https://shop.example/item'
    response = client.get('/api/merchant/analytics', headers=auth(first)).json()
    assert response['summary'] == {'today': 3, 'month': 3, 'total': 3}
    assert response['items'][0]['clicks'] == response['summary']
    assert response['trend'][-1]['clicks'] == 3
    assert response['timezone'] == 'Asia/Shanghai'
    other = client.get('/api/merchant/analytics', headers=auth(second)).json()
    assert other['summary']['total'] == 0
    assert [item['id'] for item in other['items']] == [theirs['id']]
    assert client.get('/api/merchant/analytics', headers=auth(first),
                      params={'garment_id': theirs['id']}).status_code == 404
    assert client.get('/api/merchant/analytics', params={'merchant_id': mine['merchant_id']}).status_code == 401
    with sqlite3.connect(settings.data_dir / 'merchants.sqlite3') as conn:
        assert conn.execute('SELECT COUNT(*) FROM garment_clicks').fetchone()[0] == 3
        columns = {row[1] for row in conn.execute('PRAGMA table_info(garment_clicks)')}
        assert columns == {'id', 'garment_id', 'garment_ref', 'merchant_id', 'clicked_at'}
        assert len(conn.execute('PRAGMA foreign_key_list(garment_clicks)').fetchall()) == 2


def test_hidden_missing_or_linkless_products_do_not_record(env):
    client, _, _ = env
    token = token_for(client)
    for changes in ({'status': 'draft'}, {'purchase_url': None}):
        garment = product(client, token, **changes)
        assert client.post(f"/api/garments/{garment['id']}/clicks").status_code == 404
    assert client.post('/api/garments/missing/clicks').status_code == 404
    assert client.get('/api/merchant/analytics', headers=auth(token)).json()['summary']['total'] == 0


def test_click_store_error_is_a_recoverable_api_failure(env, monkeypatch):
    client, _, _ = env
    def unavailable(_):
        raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(client.app.state.merchants.clicks, 'record', unavailable)
    assert client.post('/api/garments/example/clicks').status_code == 503


def test_business_day_month_boundaries_restart_concurrency_and_delete(tmp_path):
    store = MerchantStore(tmp_path)
    owner = store.create_merchant(name='shop', display_name='店铺', contact='', password_hash='x', quota=5)
    item = store.create_garment(owner['id'], normalize_metrics({'name': '商品', 'category': '上装',
        'status': 'published', 'purchase_url': 'https://shop.example/item'}))
    zone = ZoneInfo('Asia/Shanghai')
    now = datetime(2026, 10, 7, 12, tzinfo=zone).timestamp()
    times = [datetime(2026, 9, 30, 23, 59, tzinfo=zone), datetime(2026, 10, 1, tzinfo=zone),
             datetime(2026, 10, 6, 23, 59, tzinfo=zone), datetime(2026, 10, 7, tzinfo=zone)]
    with store.connect() as conn:
        for stamp in times:
            conn.execute('INSERT INTO garment_clicks VALUES (?,?,?,?,?)',
                         (uuid4().hex, item['id'], item['id'], owner['id'], int(stamp.timestamp())))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO garment_clicks VALUES (?,?,?,?,?)',
                         (uuid4().hex, 'missing', 'missing', owner['id'], int(now)))
    reopened = MerchantStore(tmp_path)
    assert reopened.clicks.counts(owner['id'], now=now)['summary'] == {'today': 1, 'month': 3, 'total': 4}
    # Independent connections retain every concurrent click.
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: reopened.clicks.record(item['id']), range(12)))
    assert reopened.clicks.counts(owner['id'])['summary']['total'] == 16
    reopened.delete_garment(owner['id'], item['id'])
    assert reopened.clicks.counts(owner['id'])['summary']['total'] == 16
    with reopened.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM garment_clicks WHERE garment_id IS NULL').fetchone()[0] == 16
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
