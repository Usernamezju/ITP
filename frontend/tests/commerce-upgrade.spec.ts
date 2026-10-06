import { expect, test, type Page } from '@playwright/test';

async function noOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

test('public pages, real purchase clicks and merchant analytics remain account scoped', async ({ page, request, context }, testInfo) => {
  for (const path of ['/', '/merchant']) {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(response.headers()['www-authenticate']).toBeUndefined();
  }
  expect((await request.get('/api/account/commerce')).status()).toBe(401);
  const name = `click-${testInfo.project.name}-${Date.now()}`;
  const registered = await request.post('/api/auth/register', { data: {
    name, display_name: '点击验收店铺', password: 'e2e-password-123', role: 'merchant',
  } });
  expect(registered.status()).toBe(201);
  const session = await request.post('/api/auth/login', { data: { name, password: 'e2e-password-123' } });
  const token = (await session.json()).access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const created = await request.post('/api/merchant/garments', { headers, multipart: {
    payload: JSON.stringify({ name: `验收商品-${name}`, category: '上装', status: 'published',
      price_cents: 12900, style: '通勤', season: '四季', purchase_url: 'https://shop.example/itp-product',
      fit_ranges: { shoulder_cm: [36, 45] } }),
  } });
  expect(created.status()).toBe(201);
  const garment = await created.json();
  const clickPath = `/api/garments/${garment.id}/clicks`;
  expect((await request.patch(`/api/merchant/garments/${garment.id}`, {
    headers, data: { purchase_url: 'javascript:alert(1)' },
  })).status()).toBe(422);
  await page.addInitScript((value) => localStorage.setItem('itp.merchant.token', value), token);
  await context.route('https://shop.example/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<html><head><title>商品验收页</title></head><body>商品页面</body></html>',
  }));
  await page.goto('/outfits');
  const card = page.locator('.outfit-card').filter({ hasText: `验收商品-${name}` });
  await expect(card).toContainText('¥129.00');
  const popupPromise = page.waitForEvent('popup');
  await card.getByRole('button', { name: `查看商品：验收商品-${name}` }).click();
  const popup = await popupPromise;
  await popup.waitForURL('https://shop.example/itp-product');
  expect(await popup.evaluate(() => window.opener === null)).toBe(true);
  await popup.close();
  const analytics = await request.get('/api/merchant/analytics', { headers });
  expect((await analytics.json()).summary).toEqual({ today: 1, month: 1, total: 1 });

  // A recording failure gives feedback and still opens the validated product URL.
  await page.route(`**${clickPath}`, (route) => route.fulfill({ status: 503, json: { detail: '统计暂不可用' } }));
  const failedPopupPromise = page.waitForEvent('popup');
  await card.getByRole('button', { name: `查看商品：验收商品-${name}` }).click();
  const failedPopup = await failedPopupPromise;
  await failedPopup.waitForURL('https://shop.example/itp-product');
  await expect(card.getByRole('status')).toContainText('仍可查看商品');
  await failedPopup.close();
  await noOverflow(page);
  await page.screenshot({ path: `/home/fjp/temp/itp-commerce-qa/${testInfo.project.name}-outfits.png`, fullPage: true });

  // Only bounded style/category counts survive locally and are sent on reranking.
  await card.getByRole('button', { name: `查看验收商品-${name}详情` }).click();
  await page.getByRole('button', { name: '关闭穿搭详情' }).click();
  const rerank = page.waitForRequest((req) => req.url().includes('/api/outfits/recommend') && req.method() === 'POST');
  await page.getByRole('button', { name: '查看全部套装' }).click();
  const preferences = (await rerank).postDataJSON().history_preferences;
  expect(preferences.styles.通勤).toBeGreaterThan(0);
  expect(Object.keys(preferences)).toEqual(expect.arrayContaining(['styles', 'categories']));
  expect(JSON.stringify(preferences)).not.toContain(garment.id);
  await page.getByRole('button', { name: '清除推荐偏好' }).click();

  await page.goto('/merchant');
  const overview = page.getByRole('region', { name: '商品点击数据概览' });
  await expect(overview.locator('.merchant-kpi').first()).toContainText('1');
  await overview.locator('summary').click();
  await expect(overview.getByRole('cell', { name: `验收商品-${name}` })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `/home/fjp/temp/itp-commerce-qa/${testInfo.project.name}-merchant.png`, fullPage: true });
  await page.goto('/account');
  await expect(page.getByText('当前余额（元）', { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `/home/fjp/temp/itp-commerce-qa/${testInfo.project.name}-wallet.png`, fullPage: true });
  await request.delete(`/api/merchant/garments/${garment.id}`, { headers });
});
