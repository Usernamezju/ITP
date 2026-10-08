import { expect, test } from '@playwright/test';

test('private measurements restore on another device and deletion requires confirmation', async ({ page, browser }) => {
  const name = `cloud_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;
  await page.goto('/account');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('昵称 / 商家名称').fill('私有空间验收');
  await page.getByLabel('密码', { exact: true }).fill('storage-test-password');
  await page.getByRole('button', { name: '注册并登录' }).click();
  await expect(page.getByRole('region', { name: '账户私有空间' })).toContainText('0 条记录');
  await page.goto('/');
  await expect(page.getByRole('button', { name: '账户与设置' })).toBeVisible();
  await expect(page.getByRole('region', { name: '人体数据' })).not.toHaveAttribute('aria-busy', 'true');
  await page.getByRole('spinbutton', { name: '身高（cm）' }).fill('181');
  await page.getByRole('button', { name: '保存人体数据' }).click();
  await expect(page.getByText('已保存，可用于尺码推荐')).toBeVisible();
  const token = await page.evaluate(() => localStorage.getItem('itp.merchant.token'));
  const second = await browser.newContext();
  await second.addInitScript((value) => localStorage.setItem('itp.merchant.token', value!), token);
  const remote = await second.newPage();
  await remote.goto(new URL('/', page.url()).href);
  await expect(remote.getByRole('spinbutton', { name: '身高（cm）' })).toHaveValue('181');
  await remote.goto(new URL('/account', page.url()).href);
  const privatePanel = remote.getByRole('region', { name: '账户私有空间' });
  await expect(privatePanel).toContainText('1 条记录');
  await privatePanel.getByRole('button', { name: '删除私有数据', exact: true }).click();
  await expect(privatePanel.getByRole('dialog')).toBeVisible();
  await privatePanel.getByRole('button', { name: '取消', exact: true }).click();
  await expect(privatePanel).toContainText('1 条记录');
  await privatePanel.getByRole('button', { name: '删除私有数据', exact: true }).click();
  await privatePanel.getByRole('button', { name: '确认', exact: true }).click();
  await expect(privatePanel).toContainText('私有数据已删除');
  await expect(privatePanel).toContainText('0 条记录');
  expect(await remote.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await second.close();
});

test('a failed cloud write reports failure while retaining the local cache', async ({ page }) => {
  await page.goto('/account');
  const name = `fail_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('昵称 / 商家名称').fill('同步失败验收');
  await page.getByLabel('密码', { exact: true }).fill('storage-test-password');
  await page.getByRole('button', { name: '注册并登录' }).click();
  await expect(page.getByRole('region', { name: '账户私有空间' })).toBeVisible();
  await page.route('**/api/account/storage/records/**', (r) => r.fulfill({ status: 503, json: { detail: 'temporary outage' } }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '账户与设置' })).toBeVisible();
  await expect(page.getByRole('region', { name: '人体数据' })).not.toHaveAttribute('aria-busy', 'true');
  await page.getByRole('spinbutton', { name: '身高（cm）' }).fill('178');
  await page.getByRole('button', { name: '保存人体数据' }).click();
  await expect(page.getByRole('alert')).toContainText('云端同步失败');
  await page.reload();
  await expect(page.getByRole('spinbutton', { name: '身高（cm）' })).toHaveValue('178');
});

test('legacy browser records are uploaded only after explicit confirmation', async ({ page }) => {
  await page.goto('/account');
  const name = `old_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('昵称 / 商家名称').fill('旧数据迁移验收');
  await page.getByLabel('密码', { exact: true }).fill('storage-test-password');
  await page.getByRole('button', { name: '注册并登录' }).click();
  const panel = page.getByRole('region', { name: '账户私有空间' });
  await expect(panel).toContainText('0 条记录');
  await page.evaluate(async () => {
    const token = localStorage.getItem('itp.merchant.token')!;
    const namespace = `account:${JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub}`;
    const database = await new Promise<IDBDatabase>((resolve) => {
      const request = indexedDB.open('itp-customer-data', 1); request.onsuccess = () => resolve(request.result);
    });
    await new Promise<void>((resolve) => {
      const tx = database.transaction('records', 'readwrite'); const store = tx.objectStore('records');
      store.put({ key: `${namespace}/body-profile`, namespace, id: 'body-profile', value: { height_cm: 175 } });
      store.put({ key: 'anonymous/body-profile', namespace: 'anonymous', id: 'body-profile', value: { height_cm: 166 } });
      tx.oncomplete = () => resolve();
    });
    database.close();
  });
  await page.reload();
  await expect(panel).toContainText('0 条记录');
  await panel.getByRole('button', { name: '迁移 / 重试同步本机数据' }).click();
  await expect(panel).toContainText('0 条记录');
  await panel.getByRole('button', { name: '确认', exact: true }).click();
  await expect(panel).toContainText('已同步 1 条记录');
  await expect(panel).toContainText('1 条记录');
  await page.goto('/');
  await expect(page.getByRole('spinbutton', { name: '身高（cm）' })).toHaveValue('175');
});
