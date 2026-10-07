import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'a'.repeat(32), name: 'ops', display_name: '运维', contact: '', role: 'admin', created: 1 };

const status = {
  version: '0.1.0',
  services: {
    geometry: true, pose: false, segmentation: true, outfit_images: '360图片',
    faceverse: { configured: true, reachable: true, model: 'faceverse-1',
      status: 'ok', cuda: '12.1', gpu: 'RTX 4090' },
    tryon: {
      seedream: { ready: true, model: 'seedream-5' },
      flux: { ready: false, model: 'flux-2-pro' },
      flux_max: { ready: false, model: 'flux-2-max' },
      flux_klein: { ready: true, model: 'flux-2-klein' },
      flux_klein_9b: { ready: false, model: 'flux-2-klein-9b' },
      gpt_image: { ready: false, model: 'gpt-image-2' },
    },
    flux_klein: { ready: true, model: 'flux-2-klein' },
    flux_klein_9b: { ready: false, model: 'flux-2-klein-9b' },
  },
  payments: [{ id: 'mock', name: '模拟支付（仅开发测试）', ready: true }],
};
// The endpoint never carries a secret value, only whether one is configured.
const settings = {
  tencent_endpoint: 'ai3d.tencentcloudapi.com', tencent_region: 'ap-guangzhou',
  tencent_model: '3.1', tencent_secret_id_set: true, tencent_secret_key_set: true,
  pose_endpoint: 'dashscope.aliyuncs.com', pose_model: 'qwen-image-edit', pose_api_key_set: true,
  seedream_endpoint: '', seedream_model: 'seedream-5', seedream_api_key_set: false,
  flux_endpoint: 'api.bfl.ai', flux_model: 'flux-2-pro', flux_api_key_set: false,
  flux_max_endpoint: '', flux_max_model: 'flux-2-max', flux_max_api_key_set: false,
  flux_klein_endpoint: 'http://127.0.0.1:8100', flux_klein_model: 'flux-2-klein',
  flux_klein_api_key_set: false,
  flux_klein_9b_endpoint: '', flux_klein_9b_model: 'flux-2-klein-9b',
  flux_klein_9b_api_key_set: false,
  gpt_image_endpoint: '', gpt_image_model: 'gpt-image-2', gpt_image_api_key_set: false,
  faceverse_endpoint: 'http://127.0.0.1:8200/v1/face-refine', faceverse_model: 'faceverse-1',
  faceverse_api_key_set: true,
  image_provider: 'so', unsplash_access_key_set: false, pixabay_api_key_set: false,
};
const accounts = {
  total: 2,
  items: [
    { ...account, disabled: false, quota: 0, garment_count: 0 },
    { id: 'c'.repeat(32), name: 'alice', display_name: 'Alice', contact: '', role: 'customer',
      created: 1790671718, disabled: true, quota: 20, garment_count: 3 },
  ],
};
const usage = {
  model_charges: {
    reserved: { count: 1, amount_cents: 2345 },
    completed: { count: 4, amount_cents: 9380 },
    refunded: { count: 0, amount_cents: 0 },
  },
  wallets: { count: 2, total_balance_cents: 8765 },
  recent_ledger: [{ id: 'l'.repeat(32), user_id: 'c'.repeat(32), account_name: 'alice',
    delta_cents: -2345, balance_cents: 6300, kind: 'model_debit',
    reference: 'j'.repeat(32), created: 1790671818 }],
  garments: { total: 3, draft: 1, published: 2 },
  looks: { total: 1, draft: 0, published: 1 },
  orders: {
    created: { count: 1, amount_cents: 1000 }, submitting: { count: 0, amount_cents: 0 },
    pending: { count: 0, amount_cents: 0 }, paid: { count: 2, amount_cents: 20000 },
    uncertain: { count: 0, amount_cents: 0 },
  },
};
const orders = {
  total: 3,
  items: [{ id: 'o'.repeat(32), kind: 'recharge', provider: 'mock', amount_cents: 10000,
    currency: 'CNY', state: 'paid', created: 1790671818, updated: 1790671900, expires: null,
    paid_at: 1790671850, description: '钱包充值', plan_id: null,
    user_id: 'c'.repeat(32), account_name: 'alice' }],
};
const jobs = {
  jobs: [{ id: 'j'.repeat(32), owner_id: 'c'.repeat(32), state: 'awaiting_review',
    created: 1790671818, updated: 1790671900,
    steps: [{ name: 'geometry', status: 'done' }, { name: 'pose', status: 'running' }] }],
  tryons: [{ id: 't'.repeat(32), owner_id: 'c'.repeat(32), state: 'ready',
    created: 1790671818, model: 'seedream-5', provider: 'seedream' }],
  face_refinements: [],
  note: '仅显示服务端当前保留的任务；顾客确认保存后服务端副本即被删除，没有历史任务记录。',
};

const feedback = {
  total: 2,
  items: [
    { id: 'f'.repeat(32), user_id: 'a'.repeat(32), account_name: 'alice', role: 'customer',
      kind: '问题反馈', body: '手机端提交按钮太小，点不动。', contact: 'fan@example.com',
      page: '/outfits', created: 1790671818 },
    { id: 'e'.repeat(32), user_id: null, account_name: null, role: null,
      kind: '功能建议', body: '希望支持批量导入商品。', contact: null, page: '/merchant',
      created: 1790671718 },
  ],
};

/** One dashboard section, so equal-looking values elsewhere cannot match. */
function section(page: Page, title: string) {
  return page.locator('.admin-section', { has: page.getByRole('heading', { name: title }) });
}

const paymentCallbacks = {
  alipay: 'https://pay.example.org/api/payments/callbacks/alipay',
  wechat: 'https://pay.example.org/api/payments/callbacks/wechat',
};
/** Nothing configured yet: the console must offer the form and say why. */
/** A real 1x1 PNG, so the upload carries a decodable image. */
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');

const manualChannels = () => ({
  manual_wechat: { label: '微信', qr_set: false, updated: null, qr_key: '',
    ready: false, reason: '平台未启用人工收款' },
  manual_alipay: { label: '支付宝', qr_set: false, updated: null, qr_key: '',
    ready: false, reason: '平台未启用人工收款' },
});
const manualOrdersSeed: Record<string, unknown>[] = [{
  id: 'm'.repeat(32), kind: 'recharge', provider: 'manual_wechat', amount_cents: 5000,
  currency: 'CNY', state: 'pending', created: 1790671818, updated: 1790671818,
  expires: 1800000000, paid_at: null, description: 'ClothiNation 钱包充值', plan_id: null,
  checkout: { qr_image: '/api/payments/manual/qr/' + 'a'.repeat(32) },
  user_id: 'c'.repeat(32), account_name: 'alice',
}];

const payments = {
  settings: {
    alipay: { app_id_set: false, seller_id_set: false, private_key_set: false, public_key_set: false },
    wechat: { app_id_set: false, mch_id_set: false, merchant_serial_set: false,
      private_key_set: false, api_v3_key_set: false, platform_key_ids: [] },
  },
  status: {
    notify_origin: 'https://pay.example.org',
    channels: [
      { id: 'alipay', ready: false, reason: '尚未填写商户参数' },
      { id: 'wechat', ready: false, reason: '尚未填写商户参数' },
    ],
    callbacks: paymentCallbacks,
  },
};
/** The answer to a saved configuration: only readiness and the probe result. */
const configuredPayments = {
  settings: {
    alipay: { app_id_set: true, seller_id_set: false, private_key_set: true, public_key_set: false },
    wechat: { app_id_set: false, mch_id_set: false, merchant_serial_set: false,
      private_key_set: false, api_v3_key_set: false, platform_key_ids: [] },
  },
  status: {
    notify_origin: 'https://pay.example.org',
    channels: [
      { id: 'alipay', ready: true, reason: '' },
      { id: 'wechat', ready: false, reason: '尚未填写商户参数' },
    ],
    callbacks: paymentCallbacks,
  },
  checks: [
    { channel: 'alipay', ok: true, message: '凭据已通过官方接口校验' },
    { channel: 'wechat', ok: false, message: '尚未填写商户参数' },
  ],
};

const productAi = {
  settings: { endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    model: 'qwen-vl-max', key_set: false, key_from_pose: true, ready: true },
};
/** The answer to a saved configuration: the model the operator asked for. */
const savedProductAi = {
  settings: { endpoint: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    model: 'doubao-seed-2-1-lite-260915', key_set: true, key_from_pose: false, ready: true },
  check: { ok: true, message: 'doubao-seed-2-1-lite-260915 已返回：正常' },
};

type Options = {
  /** Sign in before the page loads; false exercises the console's own login form. */
  startSignedIn?: boolean;
  role?: 'admin' | 'customer';
  /** Admin endpoints that answer 500. */
  fail?: string[];
  /** Every admin endpoint answers 401, as an expired admin session would. */
  unauthorized?: boolean;
  /** Answers the payment-config POST; the payload is asserted by the test. */
  paymentConfig?: (payload: unknown) => unknown;
  /** Answers the AI-config POST; the payload is asserted by the test. */
  productAiConfig?: (payload: unknown) => unknown;
  /** Serve the manual collection document, its orders and its writes. */
  manual?: boolean;
};

/** `/admin` with the platform's admin endpoints mocked, like the other specs. */
async function openAdmin(page: Page, options: Options = {}) {
  const { startSignedIn = true, role = 'admin', fail = [], unauthorized = false } = options;
  const calls: string[] = [];
  // A fresh copy per call: these fixtures are mutated as the console works.
  const manual = { enabled: false, channels: manualChannels() };
  const pending = options.manual ? manualOrdersSeed.map((order) => ({ ...order })) : [];
  const body: Record<string, unknown> = {
    '/api/admin/status': status, '/api/admin/settings': settings,
    '/api/admin/accounts': accounts, '/api/admin/usage': usage,
    '/api/admin/orders': orders, '/api/admin/jobs': jobs, '/api/admin/feedback': feedback,
    '/api/admin/payments': payments, '/api/admin/product-ai': productAi,
  };
  let token = startSignedIn ? 'admin-token' : '';
  if (startSignedIn) {
    await page.addInitScript((value) => localStorage.setItem('itp.merchant.token', value), 'admin-token');
  }
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/admin/product-ai/config' && options.productAiConfig) {
      calls.push(path);
      await route.fulfill({ json: options.productAiConfig(route.request().postDataJSON()) });
    } else if (path === '/api/admin/payments/config' && options.paymentConfig) {
      calls.push(path);
      await route.fulfill({ json: options.paymentConfig(route.request().postDataJSON()) });
    } else if (options.manual && path === '/api/admin/payments/manual/qr') {
      // The real route is multipart and only accepts the bare channel name,
      // so a wrong field value is answered exactly as the server answers it.
      calls.push(path);
      const payload = route.request().postData() || '';
      const channel = /name="channel"\r?\n\r?\n([^\r\n]+)/.exec(payload)?.[1] ?? '';
      if (channel === 'alipay') {
        manual.channels.manual_alipay = { label: '支付宝', qr_set: true, updated: 1790671818,
          qr_key: 'b'.repeat(32), ready: manual.enabled, reason: '' };
      } else if (channel === 'wechat') {
        manual.channels.manual_wechat = { label: '微信', qr_set: true, updated: 1790671818,
          qr_key: 'a'.repeat(32), ready: manual.enabled, reason: '' };
      } else {
        await route.fulfill({ status: 422, json: { detail: '只能上传微信或支付宝收款码' } });
        return;
      }
      await route.fulfill({ json: { ...payments, manual } });
    } else if (options.manual && path === '/api/admin/payments/config') {
      calls.push(path);
      const payload = route.request().postDataJSON() as {
        payment_manual_enabled?: boolean; payment_manual_clear?: string };
      if (payload.payment_manual_enabled !== undefined) {
        manual.enabled = payload.payment_manual_enabled;
        for (const key of ['manual_wechat', 'manual_alipay'] as const) {
          manual.channels[key] = { ...manual.channels[key], ready: manual.enabled
            && manual.channels[key].qr_set, reason: manual.enabled ? '' : '平台未启用人工收款' };
        }
      }
      if (payload.payment_manual_clear === 'wechat') {
        manual.channels.manual_wechat = { label: '微信', qr_set: false, updated: null,
          qr_key: '', ready: false, reason: '平台未启用人工收款' };
      }
      await route.fulfill({ json: { ...payments, manual } });
    } else if (path === '/api/admin/payments/manual/orders') {
      // The pending list always loads; it is simply empty without the fixture.
      calls.push(path);
      await route.fulfill({ json: { total: pending.length, items: pending } });
    } else if (/\/api\/admin\/payments\/manual\/orders\/[\w-]+\/(confirm|reject)$/.test(path)) {
      calls.push(path);
      const id = path.split('/')[6];
      const at = pending.findIndex((order) => order.id === id);
      const [decided] = pending.splice(at, 1);
      await route.fulfill({ json: { ...decided,
        state: path.endsWith('confirm') ? 'paid' : 'rejected' } });
    } else if (path === '/api/admin/payments') {
      calls.push(path);
      await route.fulfill({ json: { ...payments, manual } });
    } else if (path.startsWith('/api/payments/manual/qr/')) {
      await route.fulfill({ status: 200, body: 'png', contentType: 'image/png' });
    } else if (path.startsWith('/api/admin/')) {
      calls.push(path);
      if (unauthorized) await route.fulfill({ status: 401, json: { detail: '登录状态已失效' } });
      else if (fail.includes(path)) await route.fulfill({ status: 500, json: { detail: '内部错误' } });
      else await route.fulfill({ json: body[path] });
    } else if (path === '/api/auth/login') {
      token = 'admin-token';
      await route.fulfill({ json: { access_token: token, token_type: 'bearer', expires_in: 43200 } });
    } else if (path === '/api/account/me') {
      expect(route.request().headers()['authorization']).toBe(`Bearer ${token}`);
      await route.fulfill({ json: { ...account, role } });
    } else if (path === '/api/auth/logout') {
      await route.fulfill({ json: { logged_out: true } });
    } else if (path.startsWith('/api/')) {
      await route.fulfill({ status: 404 });
    } else if (path.startsWith('/assets/')) {
      const name = path.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else {
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    }
  });
  await page.goto('/admin');
  return calls;
}

test('an anonymous visitor gets a sign-in card without a registration path', async ({ page }) => {
  const calls = await openAdmin(page, { startSignedIn: false });
  await expect(page.getByRole('heading', { name: '管理员登录' })).toBeVisible();
  await expect(page.getByText('本页不提供注册入口')).toBeVisible();
  await expect(page.getByRole('button', { name: '注册', exact: true })).toHaveCount(0);
  await expect(page.getByRole('tab')).toHaveCount(0);
  // Nothing private is asked for before an admin has signed in.
  expect(calls).toEqual([]);
});

test('an admin signs in and reads the whole console', async ({ page }) => {
  await openAdmin(page, { startSignedIn: false });
  await page.getByLabel('账号').fill('ops');
  await page.getByLabel('密码').fill('operator-password');
  await page.getByRole('button', { name: '进入管理后台' }).click();

  await expect(page.getByRole('heading', { name: '系统状态与平台数据' })).toBeVisible();
  await expect(page.getByText('已登录：运维（ops · 管理员）')).toBeVisible();
  for (const title of ['运行状态', '模型服务配置', '账号与商户', '用户反馈', '平台数据统计',
    '订单与支付', '服务端任务']) {
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
  }
  // Status: platform version, the active photo source and one probe result.
  await expect(page.getByText('0.1.0')).toBeVisible();
  await expect(page.getByText('360图片')).toBeVisible();
  await expect(page.getByText('RTX 4090')).toBeVisible();
  await expect(page.getByRole('cell', { name: '火山引擎 SeedDream 5.0' })).toBeVisible();
  // Accounts keep their role and state, not their password material.
  const shops = section(page, '账号与商户');
  await expect(shops.getByRole('cell', { name: '管理员' })).toBeVisible();
  await expect(shops.getByRole('cell', { name: '顾客' })).toBeVisible();
  await expect(shops.getByRole('cell', { name: '已禁用' })).toBeVisible();
  // Usage aggregates money and content across every account.
  const usage = section(page, '平台数据统计');
  await expect(usage.getByText('¥87.65')).toBeVisible();
  await expect(usage.getByRole('cell', { name: '¥93.80' })).toBeVisible();
  await expect(usage.getByText('3 件 · 已发布 2 件')).toBeVisible();
  await expect(usage.getByRole('cell', { name: '建模扣费' })).toBeVisible();
  // Signed cents read the same way as the customer's own wallet ledger.
  await expect(usage.getByRole('cell', { name: '¥-23.45' })).toBeVisible();
  await expect(usage.getByRole('cell', { name: '¥63.00' })).toBeVisible();
  const payments = section(page, '订单与支付');
  await expect(payments.getByRole('cell', { name: '钱包充值' })).toBeVisible();
  await expect(payments.getByRole('cell', { name: '已支付' })).toBeVisible();
  // The task list says what it really is: no server-side history exists.
  const tasks = section(page, '服务端任务');
  await expect(tasks.getByText('等待确认姿势')).toBeVisible();
  await expect(tasks.getByText('几何生成·done、姿势编辑·running')).toBeVisible();
  await expect(tasks.getByRole('heading', { name: '脸部精修任务（0）' })).toBeVisible();
  await expect(tasks.getByText('没有历史任务记录')).toBeVisible();
  // The console is a real page: a refresh keeps the signed-in admin on it.
  await page.reload();
  await expect(page.getByRole('heading', { name: '系统状态与平台数据' })).toBeVisible();
});

test('the operator reads user feedback, anonymous reports included', async ({ page }) => {
  const calls = await openAdmin(page);
  const inbox = section(page, '用户反馈');
  await expect(inbox.locator('.admin-kpi', { hasText: '反馈总数' })).toContainText('2 条');
  await expect(inbox.getByRole('cell', { name: '问题反馈' })).toBeVisible();
  await expect(inbox.getByText('手机端提交按钮太小，点不动。')).toBeVisible();
  await expect(inbox.getByText('fan@example.com')).toBeVisible();
  // A report from a visitor without an account is still readable and labelled.
  await expect(inbox.getByRole('cell', { name: '匿名' })).toBeVisible();
  await expect(inbox.getByText('希望支持批量导入商品。')).toBeVisible();
  await expect(inbox.getByText('第 1 / 1 页 · 共 2 条')).toBeVisible();
  // The newest first page is what the console asks the server for.
  expect(calls).toContain('/api/admin/feedback');
  // Reading the inbox is all an operator can do: no button posts anywhere.
  await expect(inbox.getByRole('button', { name: '上一页' })).toBeDisabled();
  await expect(inbox.getByRole('button', { name: '下一页' })).toBeDisabled();
});

test('the operator uploads a collection code and switches manual collection on', async ({ page }) => {
  const calls = await openAdmin(page, { manual: true });
  const panel = section(page, '支付配置（可写）');
  const manualBlock = panel.locator('.admin-manual');
  await expect(manualBlock).toBeVisible();
  await expect(manualBlock.getByText('未启用')).toBeVisible();
  await expect(manualBlock.locator('.admin-manual-empty')).toHaveCount(2);

  await manualBlock.getByLabel('上传微信收款码').setInputFiles({
    name: 'wechat-code.png', mimeType: 'image/png', buffer: PNG,
  });
  // The upload carries the channel, and the picture is shown back for checking.
  expect(calls).toContain('/api/admin/payments/manual/qr');
  await expect(manualBlock.getByAltText('微信收款码')).toBeVisible();
  await expect(manualBlock.locator('.admin-manual-empty')).toHaveCount(1);

  // A picture alone offers nothing: the switch is what puts it in front of
  // payers. It is a controlled checkbox, so the state comes back from the save.
  await manualBlock.getByLabel('启用人工收款').click();
  expect(calls.filter((path) => path === '/api/admin/payments/config').length).toBeGreaterThan(0);
  await expect(manualBlock.getByText('已启用')).toBeVisible();
  await expect(panel.getByText('人工收款已启用', { exact: false })).toBeVisible();
});

test('a pending manual order is confirmed from the console, and exactly once', async ({ page }) => {
  const calls = await openAdmin(page, { manual: true });
  const inbox = section(page, '待确认人工支付订单');
  await expect(inbox.getByRole('cell', { name: 'alice' })).toBeVisible();
  await expect(inbox.getByText('¥50.00')).toBeVisible();
  await expect(inbox.getByText('微信收款码')).toBeVisible();
  await expect(inbox.getByText('钱包充值')).toBeVisible();
  await expect(inbox.getByText('支付确认中')).toBeVisible();

  await inbox.getByRole('button', { name: '确认到账' }).click();
  expect(calls.some((path) => path.endsWith('/confirm'))).toBe(true);
  await expect(inbox.getByText('已确认到账', { exact: false })).toBeVisible();
  // The order leaves the pending list, and nothing on this page posts a paid flag.
  await expect(inbox.getByText('没有待确认的人工支付订单')).toBeVisible();
});

test('rejecting a manual order asks first and credits nothing', async ({ page }) => {
  const calls = await openAdmin(page, { manual: true });
  page.on('dialog', (dialog) => void dialog.accept());
  const inbox = section(page, '待确认人工支付订单');
  await inbox.getByRole('button', { name: '拒绝' }).click();
  expect(calls.some((path) => path.endsWith('/reject'))).toBe(true);
  await expect(inbox.getByText('已拒绝', { exact: false })).toBeVisible();
  await expect(inbox.getByText('没有待确认的人工支付订单')).toBeVisible();
});

test('a signed-in customer is refused and no admin endpoint is called', async ({ page }) => {
  const calls = await openAdmin(page, { role: 'customer' });
  await expect(page.getByRole('heading', { name: '管理员后台仅限管理员账号访问' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '系统状态与平台数据' })).toHaveCount(0);
  expect(calls).toEqual([]);
});

test('an expired admin session drops back to the sign-in card', async ({ page }) => {
  await openAdmin(page, { unauthorized: true });
  await expect(page.getByRole('heading', { name: '管理员登录' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('itp.merchant.token'))).toBeNull();
});

test('the provider settings section is read-only and shows no secret', async ({ page }) => {
  await openAdmin(page);
  const panel = section(page, '模型服务配置');
  await expect(panel.getByText('凭据由服务器')).toBeVisible();
  // No field, no password box and no save button: credentials live in the .env.
  await expect(panel.locator('input')).toHaveCount(0);
  await expect(panel.locator('input[type="password"]')).toHaveCount(0);
  await expect(panel.getByRole('button')).toHaveCount(0);
  await expect(panel.getByText('腾讯云混元 AI3D')).toBeVisible();
  await expect(panel.getByText('ai3d.tencentcloudapi.com')).toBeVisible();
  await expect(panel.getByText('已配置').first()).toBeVisible();
  await expect(panel.getByText('未配置').first()).toBeVisible();
});

test('one failing endpoint leaves the other sections readable', async ({ page }) => {
  await openAdmin(page, { fail: ['/api/admin/usage'] });
  await expect(page.getByText('本节读取失败')).toBeVisible();
  await expect(page.getByRole('heading', { name: '运行状态' })).toBeVisible();
  await expect(page.getByText('0.1.0')).toBeVisible();
  await expect(section(page, '订单与支付').getByRole('cell', { name: '已支付' })).toBeVisible();
});

test('an admin configures the real payment channels without echoing a secret', async ({ page }) => {
  const sent: Record<string, unknown>[] = [];
  // A PEM the console must never render back after saving.
  const privateKey = '-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----';
  await openAdmin(page, {
    paymentConfig: (payload) => { sent.push(payload as Record<string, unknown>); return configuredPayments; },
  });

  const panel = section(page, '支付配置');
  await expect(panel.getByText('只写入服务器配置文件')).toBeVisible();
  await expect(panel.getByText('尚未填写商户参数').first()).toBeVisible();
  // The operator sees the exact callback addresses to register with each platform.
  await expect(panel.getByText(paymentCallbacks.wechat)).toBeVisible();

  // Only the touched fields are sent: everything else keeps its stored value.
  await panel.getByLabel('APP_ID').first().fill('2021000000000000');
  await panel.getByLabel('应用私钥').fill(privateKey);
  await panel.getByRole('button', { name: '保存并校验' }).click();

  await expect(panel.getByText('配置已保存到服务器')).toBeVisible();
  await expect(panel.locator('.admin-check.ok')).toContainText('凭据已通过官方接口校验');
  expect(sent).toEqual([{ alipay_app_id: '2021000000000000', alipay_private_key: privateKey }]);

  // The server answered with readiness only, and the page shows exactly that.
  await expect(panel.getByText('可用')).toBeVisible();
  await expect(page.getByText('MIIEvQIBADANBgkqhkiG9w0BAQEFAASC')).toHaveCount(0);
  await expect(panel.locator('input[value*="BEGIN PRIVATE KEY"]')).toHaveCount(0);
});

test('a wrong channel answer keeps the configuration and explains the next step', async ({ page }) => {
  await openAdmin(page, {
    paymentConfig: () => ({ ...configuredPayments, checks: [
      { channel: 'alipay', ok: false, message: '渠道校验未通过：支付签名校验失败' },
      { channel: 'wechat', ok: false, message: '尚未填写商户参数' },
    ] }),
  });
  const panel = section(page, '支付配置');
  await panel.getByLabel('APP_ID').first().fill('2021000000000000');
  await panel.getByRole('button', { name: '保存并校验' }).click();
  await expect(panel.getByRole('status')).toContainText('配置已保存到服务器');
  await expect(panel.locator('.admin-check.bad').first())
    .toContainText('渠道校验未通过：支付签名校验失败');
});

test('the operator switches the vision model from the console', async ({ page }) => {
  const sent: Record<string, unknown>[] = [];
  await openAdmin(page, { productAiConfig: (payload) => {
    sent.push(payload as Record<string, unknown>); return savedProductAi; } });

  const panel = section(page, 'AI 识图配置');
  const current = panel.locator('.admin-payment-channel strong');
  await expect(current).toHaveText('qwen-vl-max');
  await expect(panel.locator('.admin-payment-channel small').first())
    .toHaveText('沿用姿势编辑的密钥');

  await panel.getByLabel('视觉模型接口地址')
    .fill('https://ark.cn-beijing.volces.com/api/v3/chat/completions');
  await panel.getByLabel('模型名').fill('doubao-seed-2-1-lite-260915');
  await panel.getByLabel('API Key（选填）').fill('ark-secret-value');
  await panel.getByRole('button', { name: '保存并校验' }).click();

  // Only the fields the operator touched travel, and the key never comes back.
  expect(sent).toEqual([{
    product_ai_endpoint: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    product_ai_model: 'doubao-seed-2-1-lite-260915',
    product_ai_api_key: 'ark-secret-value',
  }]);
  await expect(panel.getByRole('status')).toContainText('自检通过');
  await expect(panel.locator('.admin-check.ok')).toContainText('已返回');
  await expect(page.getByText('ark-secret-value')).toHaveCount(0);
  await expect(current).toHaveText('doubao-seed-2-1-lite-260915');
});

test('a provider that refuses the credentials says so and keeps the settings', async ({ page }) => {
  await openAdmin(page, { productAiConfig: () => ({ ...savedProductAi,
    check: { ok: false, message: '图片识别服务暂时不可用，请稍后重试' } }) });
  const panel = section(page, 'AI 识图配置');
  await panel.getByLabel('模型名').fill('doubao-seed-2-1-lite-260915');
  await panel.getByRole('button', { name: '保存并校验' }).click();
  await expect(panel.getByRole('status')).toContainText('自检未通过');
  await expect(panel.locator('.admin-check.bad')).toContainText('稍后重试');
});
