import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
type Order = { id: string; kind: string; provider: string; amount_cents: number; state: string;
  description: string; created: number; expires: number; checkout: { mock: boolean } | null };

async function openPayments(page: Page, enabled = true) {
  let balance = 8765;
  let member = false;
  const orders: Order[] = [];
  const writes: { body: Record<string, unknown>; key: string | undefined }[] = [];
  await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'payment-token'));
  await page.route('**/*', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/') return route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    if (path.startsWith('/assets/')) {
      const name = path.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) return route.fulfill({ status: 404 });
      return route.fulfill({ body: await readFile(`${dist}/assets/${name}`), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    }
    if (path === '/api/capabilities') return route.fulfill({ json: { geometry: false, pose: false, tryon_providers: {}, model: '3.1' } });
    if (path === '/api/jobs') return route.fulfill({ json: [] });
    if (path === '/api/account/me') return route.fulfill({ json: { id: 'alice', name: 'alice', display_name: 'Alice',
      contact: '', role: 'customer', created: 1 } });
    if (path === '/api/pricing') return route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [
      { id: 'customer_annual', name: '个性化推荐年会员', audience: 'customer', price_cents: 4567, period_months: 12, purchasable: true, entitlements: {} }] } });
    if (path === '/api/payments/methods') return route.fulfill({ json: { methods: enabled ? [{ id: 'mock', name: '模拟支付（仅开发测试）', ready: true }]
      : [{ id: 'alipay', name: '支付宝', ready: false }, { id: 'wechat', name: '微信支付', ready: false }] } });
    if (path === '/api/account/commerce') return route.fulfill({ json: { balance_cents: balance, entitlements: { personalized_recommendation: member }, subscriptions: [], upload_usage: null } });
    if (path === '/api/account/ledger') return route.fulfill({ json: { items: [] } });
    if (path === '/api/account/orders') {
      expect(request.headers()['authorization']).toBe('Bearer payment-token');
      if (request.method() === 'POST') {
        const body = request.postDataJSON();
        writes.push({ body, key: request.headers()['idempotency-key'] });
        const order = { id: 'order-' + orders.length, kind: body.kind, provider: 'mock', amount_cents: body.kind === 'membership' ? 4567 : body.amount_cents,
          description: body.kind === 'membership' ? '个性化推荐年会员' : 'ITP 钱包充值', state: 'pending', created: 1, expires: 1800000000, checkout: { mock: true } };
        orders.unshift(order);
        return route.fulfill({ status: 201, json: order });
      }
      return route.fulfill({ json: { items: orders } });
    }
    if (path.startsWith('/api/account/orders/')) {
      const id = path.split('/')[4];
      const order = orders.find((item) => item.id === id)!;
      if (path.endsWith('/mock-pay') && order.state !== 'paid') {
        order.state = 'paid';
        if (order.kind === 'recharge') balance += order.amount_cents;
        else member = true;
      }
      return route.fulfill({ json: order });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await page.getByRole('menuitem', { name: '账号设置' }).click();
  return writes;
}

test('recharge is integer cents and balance changes only after server confirmation', async ({ page }) => {
  const writes = await openPayments(page);
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
  await page.getByLabel('充值金额（元）').fill('39.99');
  await page.getByRole('button', { name: '创建充值订单' }).click();
  await expect(page.getByRole('region', { name: '支付订单' }).getByRole('status')).toHaveText('等待支付');
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
  expect(writes[0].body.amount_cents).toBe(3999);
  expect(writes[0].key).toBeTruthy();
  expect(writes[0].body).not.toHaveProperty('paid');
  await page.getByRole('button', { name: '模拟付款（仅开发测试）', exact: true }).click();
  await expect(page.getByText('¥127.64', { exact: true })).toBeVisible();
});

test('membership sends only the plan and uses server-provided pricing', async ({ page }) => {
  const writes = await openPayments(page);
  await page.getByRole('button', { name: '购买个性化推荐年会员 · ¥45.67' }).click();
  expect(writes[0].body.plan_id).toBe('customer_annual');
  expect(writes[0].body).not.toHaveProperty('amount_cents');
  await page.getByRole('button', { name: '模拟付款（仅开发测试）', exact: true }).click();
  await expect(page.getByText('个性化推荐权益：已开通')).toBeVisible();
});

test('unconfigured production payment does not fall back to mock or ask for keys', async ({ page }) => {
  await openPayments(page, false);
  await expect(page.getByText('平台暂未开放在线支付')).toBeVisible();
  await expect(page.getByRole('button', { name: '创建充值订单' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '模拟付款（仅开发测试）' })).toHaveCount(0);
  await expect(page.getByText('API Key', { exact: true })).toHaveCount(0);
});

test('sub-cent recharge is rejected locally without creating an order', async ({ page }) => {
  const writes = await openPayments(page);
  await page.getByLabel('充值金额（元）').fill('1.001');
  await page.getByRole('button', { name: '创建充值订单' }).click();
  await expect(page.getByRole('alert')).toHaveText('请输入大于 0 且最多两位小数的充值金额');
  expect(writes).toHaveLength(0);
});
