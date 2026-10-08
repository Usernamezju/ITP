import { expect, test } from '@playwright/test';

test('unverified mainland phone registration and binding work on both screens', async ({ page }) => {
  const name = `phone_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  await page.goto('/account');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('昵称 / 商家名称').fill('手机号验收');
  await page.getByLabel('密码', { exact: true }).fill('test-password-123');
  await expect(page.getByText('短信服务未配置，填写后标记为“未验证”。', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '注册并登录' }).click();
  const binding = page.getByRole('region', { name: '手机号绑定' });
  await expect(binding).toContainText('尚未绑定手机号');
  const phone = `138${String(Math.floor(Math.random() * 1e8)).padStart(8, '0')}`;
  await binding.getByLabel('绑定手机号').fill(phone);
  await binding.getByRole('button', { name: '保存手机号' }).click();
  await expect(binding).toContainText(`${phone} · 未验证`);
  await page.reload();
  await expect(binding).toContainText(`${phone} · 未验证`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
