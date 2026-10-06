import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'c'.repeat(32), name: 'alice', display_name: 'Alice', contact: '', role: 'customer', created: 1 };

async function openAccount(page: Page, signedIn = false) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const user = { ...account };
  if (signedIn) await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'customer-token'));
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.startsWith('/api/') && method !== 'GET') writes.push({ path, body: route.request().postDataJSON() });
    if (path === '/') {
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    } else if (path.startsWith('/assets/')) {
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
    } else if (path === '/api/pricing') {
      await route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [
        { id: 'customer_annual', name: '个性化推荐年会员', audience: 'customer', price_cents: 4567,
          period_months: 12, purchasable: true, entitlements: {} }] } });
    } else if (path === '/api/account/password') {
      await route.fulfill({ json: { changed: true, tokens_revoked: true } });
    } else {
      await route.fulfill({ status: 404 });
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
  await expect(page.getByText('人体建模：¥23.45 / 次')).toBeVisible();
  await expect(page.getByText('个性化推荐年会员：¥45.67 / 12 个月')).toBeVisible();
  expect(writes.find((item) => item.path === '/api/auth/register')?.body.role).toBe('customer');
  await page.reload();
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await expect(page.getByRole('menu', { name: '账号菜单' })).toBeVisible();
  await page.getByRole('menuitem', { name: '账号设置' }).click();
  await expect(page.getByLabel('昵称 / 商家名称')).toHaveValue('Alice');
  await expect(page.getByRole('button', { name: '设置', exact: true })).toHaveCount(0);
  await expect(page.locator('[id$="api_key"], [id$="endpoint"]')).toHaveCount(0);
});

test('profile and logout use verified user endpoints and clear the shared session', async ({ page }) => {
  const writes = await openAccount(page, true);
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await page.getByRole('menuitem', { name: '账号设置' }).click();
  await page.getByLabel('昵称 / 商家名称').fill('新昵称');
  await page.getByRole('button', { name: '保存个人资料' }).click();
  await expect(page.getByText('个人资料已保存')).toBeVisible();
  expect(writes.find((item) => item.path === '/api/account/me')?.body.display_name).toBe('新昵称');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录账号' })).toBeVisible();
  expect(writes.some((item) => item.path === '/api/auth/logout')).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem('itp.merchant.token'))).toBeNull();
});

test('customer cannot open the merchant console', async ({ page }) => {
  await openAccount(page, true);
  await expect(page.getByRole('button', { name: '打开账号菜单' })).toBeEnabled();
  await page.getByRole('button', { name: '商家后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '商家后台仅限商家账号访问' })).toBeVisible();
  await expect(page.getByRole('button', { name: '新建商品' })).toHaveCount(0);
});

test('password mismatch is local and successful change requires re-login', async ({ page }) => {
  const writes = await openAccount(page, true);
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await page.getByRole('menuitem', { name: '账号设置' }).click();
  await page.getByLabel('当前密码').fill('original-password');
  await page.getByLabel('新密码', { exact: true }).fill('second-password');
  await page.getByLabel('确认新密码').fill('different-password');
  await page.getByRole('button', { name: '修改密码', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('两次输入的新密码不一致');
  expect(writes.some((item) => item.path === '/api/account/password')).toBe(false);
  await page.getByLabel('确认新密码').fill('second-password');
  await page.getByRole('button', { name: '修改密码', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录账号' })).toBeVisible();
  expect(writes.find((item) => item.path === '/api/account/password')?.body).toEqual({
    current_password: 'original-password', new_password: 'second-password',
  });
});
