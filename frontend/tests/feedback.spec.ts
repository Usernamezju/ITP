import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'c'.repeat(32), name: 'alice', display_name: '爱丽丝', contact: '13800000000',
  role: 'customer', created: 1 };

type Sent = { method: string; path: string; body: string };

/**
 * The customer shell with a mocked API: the same app the server hands to any
 * page address, plus a feedback route that records what was submitted.
 */
async function openStudio(page: Page, { signedIn = true, failure = '' } = {}) {
  const sent: Sent[] = [];
  if (signedIn) await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'customer-token'));
  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/')) {
      sent.push({ method: request.method(), path: pathname, body: request.postData() || '' });
    }
    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: false,
        tryon: false, tryon_model: '', tryon_providers: {}, faceverse: false,
        outfit_images: false, image_provider: '', provider: '', pose_provider: '',
        model: '3.1', pose_model: '' } });
    } else if (pathname === '/api/jobs') {
      await route.fulfill({ json: [] });
    } else if (pathname === '/api/account/me') {
      await route.fulfill({ json: account });
    } else if (pathname === '/api/feedback' && failure) {
      await route.fulfill({ status: 503, json: { detail: failure } });
    } else if (pathname === '/api/feedback') {
      await route.fulfill({ status: 201, json: { id: 'f'.repeat(32), created: 1790671818,
        kind: '功能建议', signed_in: signedIn } });
    } else if (pathname === '/api/pricing') {
      await route.fulfill({ json: { currency: 'CNY', model_price_cents: 2345, plans: [] } });
    } else if (pathname.startsWith('/api/')) {
      await route.fulfill({ status: 404, json: { detail: '未实现' } });
    } else if (pathname.startsWith('/assets/')) {
      const name = pathname.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else {
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    }
  });
  await page.goto('/');
  return sent;
}

const trigger = (page: Page) => page.getByRole('button', { name: '意见反馈' });
const dialog = (page: Page) => page.getByRole('dialog', { name: '意见反馈' });

test('the feedback entry sits beside the avatar and submits a typed report', async ({ page }) => {
  const sent = await openStudio(page);
  const button = trigger(page);
  await expect(button).toBeVisible();
  // Right of the top bar's own actions, immediately before the avatar button.
  const order = await page.locator('.topbar-right > *').evaluateAll(
    (nodes) => nodes.map((node) => node.className));
  expect(order.at(-2)).toContain('feedback-trigger');
  expect(order.at(-1)).toContain('account-avatar');
  await expect(page.locator('.feedback-trigger span')).toHaveText('反馈');

  if ((page.viewportSize()?.width ?? 1440) <= 760) {
    // A phone keeps the icon only, but a full 44px tap target.
    await expect(page.locator('.feedback-trigger span')).toBeHidden();
    expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  }

  await button.click();
  await expect(dialog(page)).toBeVisible();
  // A signed-in reporter is named, and their contact is prefilled but editable.
  await expect(dialog(page)).toContainText('将以账号 爱丽丝（alice）提交');
  await expect(page.getByLabel('联系方式')).toHaveValue('13800000000');

  await page.getByRole('radio', { name: '问题反馈' }).click();
  await page.getByLabel('反馈内容').fill('手机上提交按钮太小，点不动。');
  await page.getByLabel('联系方式').fill('fan@example.com');
  await page.getByRole('button', { name: '提交反馈' }).click();

  await expect(dialog(page)).toContainText('反馈已提交');
  const posted = sent.filter((call) => call.path === '/api/feedback' && call.method === 'POST');
  expect(posted).toHaveLength(1);
  expect(JSON.parse(posted[0].body)).toEqual({ kind: '问题反馈',
    body: '手机上提交按钮太小，点不动。', contact: 'fan@example.com', page: '/' });

  await page.getByRole('button', { name: '完成' }).click();
  await expect(dialog(page)).toBeHidden();
});

test('a report that is too short is caught before it is sent', async ({ page }) => {
  const sent = await openStudio(page);
  await trigger(page).click();
  await page.getByLabel('反馈内容').fill('没用');
  await expect(dialog(page)).toContainText('至少 5 个字');
  await page.getByRole('button', { name: '提交反馈' }).click();
  await expect(page.getByRole('alert')).toContainText('至少 5 个字');
  expect(sent.filter((call) => call.path === '/api/feedback')).toHaveLength(0);
  // The typed text is never thrown away by a refused submission.
  await expect(page.getByLabel('反馈内容')).toHaveValue('没用');
});

test('a visitor who is not signed in can still report a problem', async ({ page }) => {
  const sent = await openStudio(page, { signedIn: false });
  await trigger(page).click();
  await expect(dialog(page)).toContainText('当前未登录');
  await page.getByLabel('反馈内容').fill('首页在夜晚模式下看不清文字。');
  await page.getByRole('button', { name: '提交反馈' }).click();
  await expect(dialog(page)).toContainText('反馈已提交');
  expect(sent.filter((call) => call.path === '/api/feedback')).toHaveLength(1);
});

test('a failed submission explains itself and keeps the draft', async ({ page }) => {
  await openStudio(page, { failure: '反馈暂不可用，请稍后重试' });
  await trigger(page).click();
  await page.getByLabel('反馈内容').fill('提交后没有任何提示，不知道是否成功。');
  await page.getByRole('button', { name: '提交反馈' }).click();
  await expect(page.getByRole('alert')).toHaveText('反馈暂不可用，请稍后重试');
  await expect(page.getByLabel('反馈内容')).toHaveValue('提交后没有任何提示，不知道是否成功。');
  await expect(dialog(page)).toBeVisible();
  await page.getByRole('button', { name: '取消' }).click();
  await expect(dialog(page)).toBeHidden();
});

test('the fields carry their length limits and count what was typed', async ({ page }) => {
  const sent = await openStudio(page);
  await trigger(page).click();
  await expect(page.getByLabel('反馈内容')).toHaveAttribute('maxlength', '1000');
  await expect(page.getByLabel('联系方式')).toHaveAttribute('maxlength', '80');
  await page.getByLabel('反馈内容').fill('x'.repeat(1000));
  await expect(dialog(page)).toContainText('1000 / 1000');
  await page.getByRole('button', { name: '提交反馈' }).click();
  await expect(dialog(page)).toContainText('反馈已提交');
  const posted = sent.find((call) => call.path === '/api/feedback' && call.method === 'POST');
  expect(JSON.parse(posted?.body || '{}').body).toHaveLength(1000);
});
