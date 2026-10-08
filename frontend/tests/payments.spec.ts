import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
/** A real 1x1 PNG, so the collection code is an image the browser loads. */
const MANUAL_QR = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');
type Order = { id: string; kind: string; provider: string; amount_cents: number; state: string;
  description: string; created: number; expires: number;
  checkout: { mock?: boolean; qr_image?: string } | null };
type Ledger = { id: string; kind: string; delta_cents: number; balance_cents: number; reference: string; created: number };

async function openPayments(page: Page, enabled = true, options: { orders?: Order[]; ledger?: Ledger[];
  failCreateOnce?: boolean; failRefreshOnce?: boolean; failWalletOnce?: boolean;
  /** Offer the operator's own collection code instead of the test channel. */
  manual?: boolean } = {}) {
  let balance = 8765;
  let member = false;
  const orders: Order[] = options.orders || [];
  let createFails = Boolean(options.failCreateOnce);
  let refreshFails = Boolean(options.failRefreshOnce);
  let walletFails = Boolean(options.failWalletOnce);
  const ledger = options.ledger || [];
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
    if (path === '/api/account/points') return route.fulfill({ json: { balance_points: 200, model_price_points: 800, recommend_limit: 2, recommend_used: 0, first_month: true, first_month_ends: 1800000000 } });
    if (path === '/api/account/points/ledger') return route.fulfill({ json: { items: [] } });
    if (path === '/api/pricing') return route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [
      { id: 'customer_annual', name: '个性化推荐年会员', audience: 'customer', price_cents: 4567, period_months: 12, purchasable: true, entitlements: {} }] } });
    if (path.startsWith('/api/payments/manual/qr/')) return route.fulfill({ body: MANUAL_QR, contentType: 'image/png' });
    if (path === '/api/payments/methods') return route.fulfill({ json: { methods: !enabled
      ? [{ id: 'alipay', name: '支付宝', ready: false }, { id: 'wechat', name: '微信支付', ready: false }]
      : options.manual ? [{ id: 'manual_wechat', name: '微信收款码（人工确认）', ready: true }]
      : [{ id: 'mock', name: '模拟支付（仅开发测试）', ready: true }] } });
    if (path === '/api/account/commerce') {
      expect(request.headers()['authorization']).toBe('Bearer payment-token');
      if (walletFails) { walletFails = false; return route.abort('failed'); }
      return route.fulfill({ json: { balance_cents: balance, entitlements: { personalized_recommendation: member }, subscriptions: [], upload_usage: null } });
    }
    if (path === '/api/account/ledger') {
      expect(request.headers()['authorization']).toBe('Bearer payment-token');
      const query = new URL(request.url()).searchParams;
      const offset = Number(query.get('offset') || 0), limit = Number(query.get('limit') || 50);
      return route.fulfill({ json: { items: ledger.slice(offset, offset + limit) } });
    }
    if (path === '/api/account/orders') {
      expect(request.headers()['authorization']).toBe('Bearer payment-token');
      if (request.method() === 'POST') {
        const body = request.postDataJSON();
        writes.push({ body, key: request.headers()['idempotency-key'] });
        if (createFails) { createFails = false; return route.abort('failed'); }
        const manual = body.provider === 'manual_wechat';
        const order: Order = { id: 'order-' + orders.length, kind: body.kind,
          provider: manual ? 'manual_wechat' : 'mock',
          amount_cents: body.kind === 'membership' ? 4567 : body.amount_cents,
          description: body.kind === 'membership' ? '个性化推荐年会员' : 'ClothiNation 钱包充值', state: 'pending',
          created: 1, expires: 1800000000,
          checkout: manual ? { qr_image: '/api/payments/manual/qr/' + 'a'.repeat(32) } : { mock: true } };
        orders.unshift(order);
        return route.fulfill({ status: 201, json: order });
      }
      const query = new URL(request.url()).searchParams;
      const offset = Number(query.get('offset') || 0), limit = Number(query.get('limit') || 50);
      return route.fulfill({ json: { items: orders.slice(offset, offset + limit) } });
    }
    if (path.startsWith('/api/account/orders/')) {
      const id = path.split('/')[4];
      const order = orders.find((item) => item.id === id)!;
      if (path.endsWith('/refresh') && refreshFails) { refreshFails = false; return route.abort('failed'); }
      if (path.endsWith('/mock-pay') && order.state !== 'paid') {
        order.state = 'paid';
        if (order.kind === 'recharge') balance += order.amount_cents;
        else member = true;
        if (order.kind === 'recharge') ledger.unshift({ id: order.id, kind: 'recharge', delta_cents: order.amount_cents,
          balance_cents: balance, reference: order.id, created: Math.floor(Date.now() / 1000) });
      }
      return route.fulfill({ json: order });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '账户与设置' }).click();
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
  await page.getByRole('button', { name: '查看会员详情' }).click();
  await expect(page.getByText('个性化推荐权益：已开通')).toBeVisible();
});

test('a manual collection code is shown, and refreshing never marks it paid', async ({ page }) => {
  const writes = await openPayments(page, true, { manual: true });
  // The customer page is untouched: same select, same button, same refresh.
  await expect(page.getByLabel('支付方式')).toHaveValue('manual_wechat');
  await page.getByLabel('充值金额（元）').fill('50');
  await page.getByRole('button', { name: '创建充值订单' }).click();

  const panel = page.getByRole('region', { name: '支付订单' });
  await expect(panel.getByRole('status')).toHaveText('等待支付');
  await expect(panel.getByAltText('扫码支付二维码'))
    .toHaveAttribute('src', /^\/api\/payments\/manual\/qr\/[a-f0-9]{32}$/);
  expect(writes[0].body.provider).toBe('manual_wechat');

  // Refreshing only reads the stored state: a payer cannot confirm anything.
  await panel.getByRole('button', { name: '刷新支付状态' }).click();
  await expect(panel.getByRole('status')).toHaveText('等待支付');
  await panel.getByRole('button', { name: '刷新支付状态' }).click();
  await expect(panel.getByRole('status')).toHaveText('等待支付');
  // There is no browser-side "I paid" control on a manual order either.
  await expect(panel.getByRole('button', { name: '模拟付款（仅开发测试）' })).toHaveCount(0);
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
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

test('network retry preserves the order idempotency key and never claims payment success', async ({ page }) => {
  const writes = await openPayments(page, true, { failCreateOnce: true });
  await expect(page.getByRole('button', { name: '立即充值', exact: true })).toHaveCSS('background-color', 'rgb(200, 40, 28)');
  await expect(page.getByRole('button', { name: '创建充值订单' })).toHaveCSS('background-color', 'rgb(200, 40, 28)');
  await page.getByRole('button', { name: '立即充值', exact: true }).click();
  await expect(page.getByLabel('充值金额（元）')).toBeFocused();
  await page.getByLabel('充值金额（元）').fill('30');
  await page.getByRole('button', { name: '创建充值订单' }).click();
  await expect(page.getByRole('alert')).toContainText('避免重复创建订单');
  await page.getByRole('button', { name: '创建充值订单' }).click();
  await expect(page.getByRole('region', { name: '支付订单' }).getByRole('status')).toHaveText('等待支付');
  expect(writes).toHaveLength(2);
  expect(writes[0].key).toBe(writes[1].key);
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
});

test('expired uncertain orders retain query and recover from network errors', async ({ page }) => {
  await openPayments(page, true, { failRefreshOnce: true, orders: [{ id: 'expired-order', kind: 'recharge', provider: 'mock',
    amount_cents: 3000, state: 'uncertain', description: '超时充值', created: 1, expires: 2, checkout: null }] });
  await page.getByRole('button', { name: /超时充值/ }).click();
  await expect(page.getByText('付款时限已到', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '刷新支付状态' }).click();
  await expect(page.getByRole('alert')).toContainText('已支付时请勿重复付款');
  await page.getByRole('button', { name: '刷新支付状态' }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '支付订单' }).getByRole('status')).toHaveText('支付状态待确认');
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
});

test('ledger pagination shows actual refunds and balances without mobile overflow', async ({ page }, testInfo) => {
  await openPayments(page, true, { ledger: Array.from({ length: 11 }, (_, index) => ({ id: `ledger-${index}`,
    kind: index === 0 ? 'model_refund' : 'model_debit', delta_cents: index === 0 ? 2345 : -2345,
    balance_cents: 8765, reference: `business-reference-${index}`, created: 1700000000 - index })) });
  const ledger = page.getByRole('region', { name: '最近资金流水' });
  await expect(ledger.getByText('已退款到账')).toBeVisible();
  await expect(ledger.getByText('余额 ¥87.65')).toHaveCount(10);
  await page.getByRole('button', { name: '下一页流水' }).click();
  await expect(ledger.getByText('业务编号：business-reference-10')).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页流水' })).toBeDisabled();
  await page.getByRole('button', { name: '上一页流水' }).click();
  await expect(ledger.getByText('已退款到账')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `/tmp/itp-wallet-${testInfo.project.name}.png`, fullPage: true });
});

test('wallet query failures can be refreshed without losing account controls', async ({ page }) => {
  await openPayments(page, true, { failWalletOnce: true });
  await expect(page.getByRole('alert')).toContainText('钱包信息读取失败');
  await page.getByRole('button', { name: '刷新钱包与流水' }).click();
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('orders paginate and recharge limit is checked before any write', async ({ page }) => {
  const writes = await openPayments(page, true, { orders: Array.from({ length: 11 }, (_, index) => ({ id: `old-${index}`,
    kind: 'recharge', provider: 'mock', amount_cents: 3000, state: 'paid', description: `历史充值-${index}`,
    created: 1700000000 - index, expires: 1800000000, checkout: null })) });
  await page.getByRole('button', { name: '下一页订单' }).click();
  await expect(page.getByRole('button', { name: /历史充值-10/ })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页订单' })).toBeDisabled();
  await page.getByLabel('充值金额（元）').fill('100000.01');
  await page.getByRole('button', { name: '创建充值订单' }).click();
  await expect(page.getByRole('alert')).toHaveText('单次充值不能超过 100000 元');
  expect(writes).toHaveLength(0);
});
