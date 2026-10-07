import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'c'.repeat(32), name: 'alice', display_name: 'Alice', contact: '', role: 'customer', created: 1 };

/** Every customer address is served by the built app, like the real server. */
async function openPage(page: Page, { signedIn = false } = {}) {
  if (signedIn) await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'customer-token'));
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: false,
        tryon_providers: {}, faceverse: false, model: '3.1' } });
    } else if (path === '/api/jobs') {
      await route.fulfill({ json: [] });
    } else if (path === '/api/account/me') {
      await route.fulfill({ json: account });
    } else if (path === '/api/pricing') {
      await route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [] } });
    } else if (path === '/api/account/commerce') {
      await route.fulfill({ json: { balance_cents: 0, entitlements: {}, subscriptions: [], upload_usage: null } });
    } else if (path === '/api/account/ledger' || path === '/api/account/orders') {
      await route.fulfill({ json: { items: [] } });
    } else if (path === '/api/payments/methods') {
      await route.fulfill({ json: { methods: [] } });
    } else if (path === '/api/auth/logout') {
      await route.fulfill({ json: { logged_out: true } });
    } else if (path === '/api/garment-options') {
      // The shop console loads its option lists before it shows either form.
      await route.fulfill({ json: { categories: [], styles: [], seasons: [], silhouettes: [],
        stretches: [], length_types: [], statuses: [], measurements: [] } });
    } else if (path.startsWith('/api/')) {
      await route.fulfill({ status: 404 });
    } else if (path.startsWith('/assets/')) {
      const name = path.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else {
      // The server hands the app itself to any customer, merchant or admin URL.
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    }
  });
}

test('every customer address opens directly and survives a refresh', async ({ page }) => {
  await openPage(page);
  const routes: [string, string, boolean][] = [
    ['/', '从一张图，到一个世界', true],
    ['/tryon', '虚拟试穿', true],
    ['/outfits', '穿搭推荐', true],
    ['/history', '任务记录', true],
    ['/appearance', '工作台主题', true],
    ['/account', '登录账号', true],
    // The shop console shows its sign-in card, which is not a heading.
    ['/merchant', '注册商家账号', false],
  ];
  for (const [path, label, isHeading] of routes) {
    await page.goto(path);
    const canonicalPath = path === '/appearance' ? '/account' : path;
    await expect(page).toHaveURL(new RegExp(`${canonicalPath.replace('/', '\\/')}$`));
    const found = isHeading ? page.getByRole('heading', { name: label }) : page.getByText(label);
    await expect(found.first()).toBeVisible();
    await page.reload();
    await expect(found.first()).toBeVisible();
  }
});

for (const signedIn of [false, true]) {
  test(`settings and account avatar share one page (${signedIn ? 'signed in' : 'anonymous'})`, async ({ page }, testInfo) => {
    await openPage(page, { signedIn });
    await page.goto('/');
    const settings = page.getByRole('link', { name: '设置', exact: true });
    await expect(settings).toHaveAttribute('href', '/account');
    await settings.click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(settings).toHaveClass(/selected/);
    await expect(page.getByRole('heading', { name: signedIn ? '个人资料' : '登录账号', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '工作台主题' })).toBeVisible();
    await page.getByRole('link', { name: '人体建模' }).click();
    await page.getByRole('button', { name: signedIn ? '账户与设置' : '登录或注册' }).click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByRole('menu', { name: '账号菜单' })).toHaveCount(0);
    await page.getByRole('link', { name: '主题设置', exact: true }).click();
    await expect(page.getByRole('radio', { name: /日间模式/ })).toBeInViewport();
    await page.getByRole('radio', { name: /夜间模式/ }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('unified-settings-night.png'), fullPage: true });
    await page.reload();
    await expect(page.getByRole('radio', { name: /夜间模式/ })).toHaveAttribute('aria-checked', 'true');
    await page.goto('/appearance');
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByRole('heading', { name: signedIn ? '个人资料' : '登录账号', exact: true })).toBeVisible();
    await page.getByRole('radio', { name: /日间模式/ }).click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath('unified-settings.png'), fullPage: true });
  });
}

test('an address with no page returns to the workbench', async ({ page }) => {
  await openPage(page);
  await page.goto('/nope/deeper');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: '从一张图，到一个世界' })).toBeVisible();
});

test('the rail exposes real links and marks the current page', async ({ page }) => {
  await openPage(page);
  await page.goto('/');
  const tryon = page.getByRole('link', { name: '虚拟试穿' });
  await expect(tryon).toHaveAttribute('href', '/tryon');
  await expect(page.getByRole('link', { name: '人体建模' })).toHaveClass(/selected/);
  await tryon.click();
  await expect(page).toHaveURL(/\/tryon$/);
  await expect(tryon).toHaveClass(/selected/);
  await expect(page.getByRole('link', { name: '人体建模' })).not.toHaveClass(/selected/);
});

test('walking to another page and back keeps the workbench input', async ({ page }) => {
  await openPage(page);
  await page.goto('/');
  await page.getByLabel('资产名称').fill('留住这份灵感');
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await expect(page.getByRole('heading', { name: '工作台主题' })).toBeVisible();
  await page.getByRole('link', { name: '人体建模' }).click();
  await expect(page.getByLabel('资产名称')).toHaveValue('留住这份灵感');
});

test('a customer who opens the merchant console is offered the upgrade', async ({ page }) => {
  await openPage(page, { signedIn: true });
  await page.goto('/merchant');
  await expect(page.getByRole('heading', { name: '注册成为商家' })).toBeVisible();
  // No shop functions are reachable before the account really has the role.
  await expect(page.getByRole('button', { name: '新建商品' })).toHaveCount(0);
});
