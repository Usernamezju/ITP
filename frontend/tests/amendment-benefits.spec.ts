import { expect, test } from '@playwright/test';

test('points, signin and paid monthly benefits use server transactions', async ({ page }) => {
  const name = `benefit_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  await page.goto('/account');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('昵称 / 商家名称').fill('积分验收');
  await page.getByLabel('密码', { exact: true }).fill('test-password-123');
  await page.getByRole('button', { name: '注册并登录' }).click();
  await expect(page.getByRole('region', { name: '积分与权益' })).toContainText('200');
  await page.goto('/signin');
  await expect(page.getByRole('region', { name: '签到月历' })).toContainText('积分余额200');
  await page.getByRole('button', { name: '今日签到 · 10 积分' }).click();
  await expect(page.getByRole('status')).toContainText('签到成功，获得 10 积分');
  await expect(page.getByRole('button', { name: '今日签到 · 10 积分' })).toBeDisabled();
  await page.goto('/pricing');
  const customer = page.getByRole('region', { name: '顾客会员套餐' });
  const merchants = page.getByRole('region', { name: '商家会员套餐' });
  await expect(customer).toContainText('¥30.00');
  await expect(merchants.locator('.plan-card')).toHaveCount(4);
  await expect(merchants).toContainText('不限量');
  await expect(merchants).toContainText('500 次 / 周期');
  await customer.getByRole('button', { name: '购买顾客月会员' }).click();
  const payment = page.getByRole('region', { name: '支付订单', exact: true });
  await expect(payment).toContainText('¥30.00');
  await expect(payment).toContainText('订单编号');
  await payment.getByRole('button', { name: '模拟付款（仅开发测试）' }).click();
  await expect(payment).toContainText('支付已确认');
  await page.goto('/signin');
  const calendar = page.getByRole('region', { name: '签到月历' });
  await expect(calendar).toContainText('积分余额1210');
  await expect(calendar).toContainText('补签卡5 张');
  await expect(page.getByRole('button', { name: '今日签到 · 50 积分' })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('makeup requires a second confirmation and uses only server data', async ({ page }) => {
  const days = [{ day: '2026-10-01', state: 'missed', can_makeup: true },
    { day: '2026-10-02', state: 'today', can_makeup: false }];
  let supplements = 0;
  await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'makeup-test'));
  await page.route('**/api/account/me', (r) => r.fulfill({ json: { id: 'a'.repeat(32),
    role: 'customer', name: 'alice', display_name: 'Alice' } }));
  await page.route('**/api/account/points*', (r) => r.fulfill({ json: {
    balance_points: 1200 + supplements * 50, member: true, cards: 5 - supplements, streak: 0,
    attendance_count: supplements, attendance_required: 31, month: '2026-10', today: '2026-10-02',
    signin_points: 50, days: days.map((d) => supplements && d.day === '2026-10-01'
      ? { ...d, state: 'supplemented', can_makeup: false } : d),
  } }));
  await page.route('**/api/account/signin', async (r) => {
    expect(r.request().postDataJSON()).toEqual({ day: '2026-10-01' });
    supplements++;
    await r.fulfill({ json: { signed: true, points: 50 } });
  });
  await page.goto('/signin');
  await page.getByRole('button', { name: '2026-10-01 漏签，可补签' }).click();
  expect(supplements).toBe(0);
  const dialog = page.getByRole('dialog', { name: '确认补签' });
  await expect(dialog).toContainText('使用 1 张补签卡');
  await dialog.getByRole('button', { name: '取消补签' }).click();
  expect(supplements).toBe(0);
  await page.getByRole('button', { name: '2026-10-01 漏签，可补签' }).click();
  await dialog.getByRole('button', { name: '确认使用补签卡' }).click();
  await expect(page.getByRole('button', { name: '2026-10-01 已补签' })).toBeDisabled();
  expect(supplements).toBe(1);
});
