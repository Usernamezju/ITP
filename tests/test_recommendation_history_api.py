"""Public compact preferences do not expose private models or account authority."""
from test_merchant_api import env, auth, token_for
from test_product_clicks import product


def test_compact_history_changes_public_ranking_without_persisting_customer_data(env):
    client, _, _ = env
    token = token_for(client)
    product(client, token, name='通勤商品', style='通勤')
    product(client, token, name='休闲商品', style='休闲')
    cold = client.get('/api/outfits').json()
    ranked = client.post('/api/outfits/discover', json={
        'history_preferences': {'styles': {'通勤': 20}}})
    assert ranked.status_code == 200, ranked.text
    result = ranked.json()['recommendations']
    assert result[0]['name'] == '通勤商品'
    assert any('近期更常查看通勤风' in reason for reason in result[0]['ranking']['reasons'])
    assert all(item['ranking']['cold_start'] for item in cold['recommendations'])
    with client.app.state.merchants.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM body_profiles').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM garment_clicks').fetchone()[0] == 0


def test_public_discovery_rejects_private_data_and_raw_history(env):
    client, _, _ = env
    for payload in ({'measurements': {'height_cm': 170}}, {'asset_id': 'a' * 32},
                    {'history_preferences': {'customer_id': 'other'}},
                    {'history_preferences': {'styles': {'通勤': 1000000}}},
                    {'history_preferences': {'styles': {'通勤': True}}},
                    {'history_preferences': {'events': [{'url': 'https://example.com'}]}}):
        assert client.post('/api/outfits/discover', json=payload).status_code == 422
    assert client.post('/api/outfits/recommend', json={}).status_code == 401
    token = token_for(client)
    response = client.post('/api/outfits/recommend', headers={**auth(token), 'Idempotency-Key': 'recommendation-test-key'}, json={
        'history_preferences': {'categories': {'上装': 5}}})
    assert response.status_code == 200, response.text
