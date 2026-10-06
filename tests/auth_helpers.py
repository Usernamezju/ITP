"""Explicit authenticated/funded clients for legacy modeling regressions."""

from itp.merchant_auth import encode_token, hash_password, resolve_jwt_secret


def fund_client(client, *, existing_jobs=False):
    store = client.app.state.merchants
    user = store.merchant_by_name("model-tester")
    if not user:
        user = store.create_merchant(
            name="model-tester",
            display_name="Model Tester",
            contact="",
            password_hash=hash_password("model-test-password"),
            quota=0,
            role="customer",
        )
    token, _ = encode_token(
        resolve_jwt_secret(client.app), user["id"], hours=12, password_hash=user["password_hash"]
    )
    client.headers["Authorization"] = "Bearer " + token
    client.headers["Idempotency-Key"] = "test-model-request-key"
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        store.commerce.credit_verified_order(conn, user["id"], 1000000, "verified-test-recharge")
    if existing_jobs:
        for job in client.app.state.store.jobs():
            job["owner_id"] = user["id"]
            client.app.state.store.save_job(job)
    return client
