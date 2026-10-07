import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { imageFromCanvas } from './fixtures';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'c'.repeat(32), name: 'alice', display_name: 'Alice', contact: '',
  role: 'customer', created: 1, avatar_key: null as string | null };
const uploadedKey = 'd'.repeat(32);
// A real 1x1 PNG: the stored picture is replaced by the default when it fails to load.
const avatarPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');

async function openAccount(page: Page, signedIn = false) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const user: typeof account = { ...account };
  if (signedIn) await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'customer-token'));
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    // Multipart uploads have no JSON body, so they are captured by their handler.
    if (path.startsWith('/api/') && method !== 'GET' && path !== '/api/account/avatar') {
      writes.push({ path, body: route.request().postDataJSON() });
    }
    if (path.startsWith('/assets/')) {
      const name = path.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else if (path === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: false,
        tryon_providers: {}, faceverse: false, model: '3.1' } });
    } else if (path === '/api/jobs') {
      await route.fulfill({ json: [] });
    } else if (path === '/api/auth/register') {
      await route.fulfill({ status: 201, json: user });
    } else if (path === '/api/auth/login') {
      await route.fulfill({ json: { access_token: 'customer-token', token_type: 'bearer', expires_in: 43200 } });
    } else if (path === '/api/account/me') {
      expect(route.request().headers()['authorization']).toBe('Bearer customer-token');
      if (method === 'PATCH') Object.assign(user, route.request().postDataJSON());
      await route.fulfill({ json: user });
    } else if (path === '/api/auth/logout') {
      expect(route.request().headers()['authorization']).toBe('Bearer customer-token');
      await route.fulfill({ json: { logged_out: true } });
    } else if (path === '/api/account/commerce') {
      await route.fulfill({ json: { balance_cents: 8765, entitlements: {}, subscriptions: [], upload_usage: null } });
    } else if (path === '/api/account/ledger') {
      await route.fulfill({ json: { items: [] } });
    } else if (path === '/api/account/orders') {
      await route.fulfill({ json: { items: [] } });
    } else if (path === '/api/payments/methods') {
      await route.fulfill({ json: { methods: [] } });
    } else if (path === '/api/pricing') {
      await route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [
        { id: 'customer_annual', name: '个性化推荐年会员', audience: 'customer', price_cents: 4567,
          period_months: 12, purchasable: true, entitlements: {} }] } });
    } else if (path === '/api/account/merchant') {
      expect(route.request().headers()['authorization']).toBe('Bearer customer-token');
      Object.assign(user, route.request().postDataJSON(), { role: 'merchant' });
      await route.fulfill({ json: user });
    } else if (path === '/api/account/avatar') {
      expect(route.request().headers()['authorization']).toBe('Bearer customer-token');
      if (method === 'DELETE') user.avatar_key = null;
      else {
        // The picture itself must have travelled as a file part.
        expect(route.request().postDataBuffer()?.toString('latin1')).toContain('filename="avatar.png"');
        user.avatar_key = uploadedKey;
      }
      await route.fulfill({ json: user });
    } else if (path.startsWith('/api/avatars/')) {
      await route.fulfill({ body: avatarPng, contentType: 'image/png' });
    } else if (path === '/api/account/password') {
      await route.fulfill({ json: { changed: true, tokens_revoked: true } });
    } else if (path.startsWith('/api/')) {
      await route.fulfill({ status: 404 });
    } else {
      // Every page URL gets the app itself, exactly like the real server.
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    }
  });
  await page.goto('/');
  return writes;
}

test('avatar opens unified registration and restores the session on reload', async ({ page }) => {
  const writes = await openAccount(page);
  await page.getByRole('button', { name: '登录或注册' }).click();
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill('alice');
  await page.getByLabel('昵称 / 商家名称').fill('Alice');
  await page.getByLabel('密码', { exact: true }).fill('original-password');
  await page.getByLabel('账号身份').selectOption('customer');
  await page.getByRole('button', { name: '注册并登录' }).click();
  await expect(page.getByRole('heading', { name: '个人资料' })).toBeVisible();
  await expect(page.getByText('¥87.65', { exact: true })).toBeVisible();
  // The price now lives inside the collapsed 会员与使用权益 section.
  await page.getByText('会员与使用权益').click();
  await expect(page.getByText('人体建模：¥23.45 / 次', { exact: true })).toBeVisible();
  await expect(page.getByText('个性化推荐年会员：¥45.67 / 12 个月')).toBeVisible();
  expect(writes.find((item) => item.path === '/api/auth/register')?.body.role).toBe('customer');
  await page.reload();
  await page.getByRole('button', { name: '账户与设置' }).click();
  await expect(page.getByLabel('昵称 / 商家名称')).toHaveValue('Alice');
  await expect(page.getByRole('button', { name: '设置', exact: true })).toHaveCount(0);
  await expect(page.locator('[id$="api_key"], [id$="endpoint"]')).toHaveCount(0);
});

test('profile and logout use verified user endpoints and clear the shared session', async ({ page }) => {
  const writes = await openAccount(page, true);
  await page.getByRole('button', { name: '账户与设置' }).click();
  await page.getByLabel('昵称 / 商家名称').fill('新昵称');
  await page.getByRole('button', { name: '保存个人资料' }).click();
  await expect(page.getByText('个人资料已保存')).toBeVisible();
  expect(writes.find((item) => item.path === '/api/account/me')?.body.display_name).toBe('新昵称');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录账号' })).toBeVisible();
  expect(writes.some((item) => item.path === '/api/auth/logout')).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem('itp.merchant.token'))).toBeNull();
});


test('password mismatch is local and successful change requires re-login', async ({ page }) => {
  const writes = await openAccount(page, true);
  await page.getByRole('button', { name: '账户与设置' }).click();
  await page.getByRole('button', { name: '修改密码', exact: true }).click();
  await page.getByLabel('当前密码').fill('original-password');
  await page.getByLabel('新密码', { exact: true }).fill('second-password');
  await page.getByLabel('确认新密码').fill('different-password');
  await page.getByRole('button', { name: '确认修改密码', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('两次输入的新密码不一致');
  expect(writes.some((item) => item.path === '/api/account/password')).toBe(false);
  await page.getByLabel('确认新密码').fill('second-password');
  await page.getByRole('button', { name: '确认修改密码', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录账号' })).toBeVisible();
  expect(writes.find((item) => item.path === '/api/account/password')?.body).toEqual({
    current_password: 'original-password', new_password: 'second-password',
  });
});

test('account overview uses live pricing and keeps the reference layout usable on both screens', async ({ page }, testInfo) => {
  await openAccount(page, true);
  await page.getByRole('button', { name: '账户与设置' }).click();
  await expect(page.getByRole('heading', { name: '账户概览', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '当前余额', exact: true })).toContainText('¥87.65');
  await expect(page.getByRole('region', { name: '会员状态', exact: true })).toContainText('未开通');
  await expect(page.getByRole('region', { name: '调用价格', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '我的订单', exact: true })).toContainText('暂无订单');
  await expect(page.getByRole('region', { name: '最近资金流水', exact: true })).toContainText('暂无交易');
  await expect(page.getByRole('button', { name: '创建充值订单' })).toBeDisabled();
  await expect(page.getByLabel('当前密码')).toHaveCount(0);
  const balanceIcon = (await page.locator('.wallet-balance .account-metric-icon').boundingBox())!;
  const balanceCopy = (await page.locator('.wallet-balance .account-metric-copy').boundingBox())!;
  expect(balanceIcon.x + balanceIcon.width).toBeLessThan(balanceCopy.x);
  const bounds = await Promise.all(['.account-profile-card', '.payment-recharge-card', '.payment-orders', '.commerce-ledger']
    .map((selector) => page.locator(selector).boundingBox()));
  const [profile, recharge, orders, ledger] = bounds.map((box) => box!);
  if (testInfo.project.name === 'desktop') {
    expect(Math.abs(profile.y - recharge.y)).toBeLessThan(2);
    expect(profile.x + profile.width).toBeLessThan(recharge.x);
    expect(Math.abs(orders.y - ledger.y)).toBeLessThan(2);
    expect(orders.x + orders.width).toBeLessThan(ledger.x);
  } else {
    expect(profile.y + profile.height).toBeLessThan(recharge.y);
    expect(orders.y + orders.height).toBeLessThan(ledger.y);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.getByRole('button', { name: '立即充值', exact: true }).click();
  await expect(page.getByLabel('充值金额（元）')).toBeFocused();
  await page.getByRole('button', { name: '查看会员详情' }).click();
  await expect(page.getByText('个性化推荐权益：未开通')).toBeVisible();
  await page.locator('#account-member-details').evaluate((element) => { (element as HTMLDetailsElement).open = false; });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath('account-overview.png'), fullPage: true });
});

test('membership overview distinguishes current rights from expired and future subscriptions', async ({ page }) => {
  await openAccount(page, true);
  const now = Math.floor(Date.now() / 1000);
  let current = false;
  await page.route('**/api/account/commerce', (route) => route.fulfill({ json: {
    balance_cents: 8765, entitlements: {}, upload_usage: null,
    subscriptions: [
      { id: 'expired', plan_id: 'customer_annual', starts: now - 86400, ends: now - 3600 },
      { id: 'future', plan_id: 'customer_annual', starts: now + 86400, ends: now + 172800 },
      ...(current ? [{ id: 'current', plan_id: 'customer_annual', starts: now - 3600, ends: now + 3600 }] : []),
    ],
  } }));
  await page.goto('/account');
  const membership = page.getByRole('region', { name: '会员状态', exact: true });
  await expect(membership).toContainText('未开通');
  await page.getByRole('button', { name: '查看会员详情' }).click();
  await expect(page.locator('#account-member-details')).toContainText('生效');
  current = true;
  await page.getByRole('button', { name: '刷新钱包与流水' }).click();
  await expect(membership).toContainText('已开通');
});

test('an account uploads its own avatar and can return to the default', async ({ page }) => {
  await openAccount(page, true);
  await page.getByRole('button', { name: '账户与设置' }).click();
  const profileAvatar = page.locator('.account-profile-card img.account-avatar');
  const topAvatar = page.locator('button.account-avatar img.account-avatar-image');
  // The built-in default ships with the app: it is either a file or, when Vite
  // inlines it, an SVG data URI. What matters is that no upload URL is used.
  const source = async (locator: typeof profileAvatar) => (await locator.getAttribute('src')) || '';
  for (const avatar of [profileAvatar, topAvatar]) {
    expect(await source(avatar)).not.toContain('/api/avatars/');
    expect(await source(avatar)).toMatch(/default-avatar|^data:image\/svg\+xml/);
  }

  await page.getByLabel('上传头像图片').setInputFiles({
    name: 'avatar.png', mimeType: 'image/png', buffer: await imageFromCanvas(page) });
  await expect(profileAvatar).toHaveAttribute('src', `/api/avatars/${uploadedKey}`);
  await expect(topAvatar).toHaveAttribute('src', `/api/avatars/${uploadedKey}`);
  await expect(page.getByText('头像已更新')).toBeVisible();
  // Replacing the picture means a new URL, so no cache can show the old one.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);

  await page.getByRole('button', { name: '恢复默认头像' }).click();
  await expect(page.getByText('已恢复默认头像')).toBeVisible();
  for (const avatar of [profileAvatar, topAvatar]) {
    expect(await source(avatar)).not.toContain('/api/avatars/');
  }
  await expect(page.getByRole('button', { name: '恢复默认头像' })).toHaveCount(0);
});
test('a customer registers as a merchant from the console and keeps the account', async ({ page }) => {
  const writes = await openAccount(page, true);
  await expect(page.getByRole('button', { name: '账户与设置' })).toBeEnabled();
  await page.getByRole('link', { name: '商家后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '注册成为商家' })).toBeVisible();
  await expect(page.getByRole('button', { name: '新建商品' })).toHaveCount(0);
  // The name already on the account is the starting point, not a new signup.
  await expect(page.getByLabel('商家名称')).toHaveValue('Alice');
  await page.getByLabel('手机号').fill('13800000000');
  await page.getByRole('button', { name: '注册成为商家' }).click();

  const upgrade = writes.find((item) => item.path === '/api/account/merchant');
  expect(upgrade?.body).toEqual({ display_name: 'Alice', contact: '13800000000' });
  // The same account now carries the merchant role, so the upgrade card is gone.
  await expect(page.getByRole('heading', { name: '注册成为商家' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '商家后台' })).toBeVisible();
});
