import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

import { imageFromCanvas } from './fixtures';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const caps = {
  geometry: false, pose: false, segmentation: false, tryon: false, tryon_model: '',
  tryon_providers: { seedream: false, flux: false, flux_max: false, flux_klein: true,
    flux_klein_9b: true, gpt_image: false },
  faceverse: false, faceverse_model: '', outfit_images: false, image_provider: 'so',
  provider: '', pose_provider: '', model: '3.1', pose_model: '',
};

async function serveBundle(page: Page) {
  await page.route('**/*', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') {
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    } else if (pathname.startsWith('/assets/')) {
      const name = pathname.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else if (pathname === '/api/capabilities') {
      await route.fulfill({ json: caps });
    } else if (pathname === '/api/assets' && route.request().method() === 'POST') {
      await route.fulfill({ status: 201, json: { id: 'd'.repeat(32), url: '', kind: 'image', size: 100 } });
    } else if (pathname === '/api/jobs' || pathname === '/api/tryons') {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ status: 404 });
    }
  });
}

test('Klein selection uses independent health and submits the 9B provider', async ({ page }) => {
  await serveBundle(page);
  let resolveHealth!: () => void;
  const pendingHealth = new Promise<void>((resolve) => { resolveHealth = resolve; });
  await page.route('**/api/tryon-providers/flux-klein/health', (route) =>
    route.fulfill({ json: { ready: true, model: 'flux.2-klein-4b' } }));
  await page.route('**/api/tryon-providers/flux-klein-9b/health', async (route) => {
    await pendingHealth;
    await route.fulfill({ json: { ready: true, model: 'flux.2-klein-9b' } });
  });
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/tryons', async (route) => {
    if (route.request().method() === 'POST') {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { id: 'b'.repeat(32), name: '虚拟试穿', state: 'ready',
        model: 'flux.2-klein-9b', provider: 'flux_klein_9b', results: {}, active_view: null } });
    } else {
      await route.fulfill({ json: [] });
    }
  });
  await page.goto('/');
  await page.getByRole('button', { name: '虚拟试穿', exact: true }).click();
  const image = await imageFromCanvas(page);
  const upload = page.getByLabel('上传正面');
  await upload.first().setInputFiles({ name: 'person.png', mimeType: 'image/png', buffer: image });
  await upload.last().setInputFiles({ name: 'garment.png', mimeType: 'image/png', buffer: image });
  await expect(page.getByAltText('正面')).toHaveCount(2);
  const picker = page.locator('select:visible').first();
  const generate = page.getByRole('button', { name: '生成六视图试穿' });
  await picker.selectOption('flux_klein');
  await expect(generate).toBeEnabled();
  await picker.selectOption('flux_klein_9b');
  await expect(generate).toBeDisabled();
  resolveHealth();
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect.poll(() => submitted?.provider).toBe('flux_klein_9b');
  expect(submitted?.person).toEqual({ front: 'd'.repeat(32) });
  expect(submitted?.garment).toEqual({ front: 'd'.repeat(32) });
});

test('9B unavailable service never asks the customer for an endpoint or token', async ({ page }) => {
  await serveBundle(page);
  const requested: string[] = [];
  await page.route('**/api/settings', (route) => {
    requested.push(route.request().method());
    return route.fulfill({ status: 404 });
  });
  await page.route('**/api/tryon-providers/flux-klein-9b/health', (route) =>
    route.fulfill({ json: { ready: false, model: 'flux.2-klein-9b' } }));
  await page.goto('/');
  await page.getByRole('button', { name: '虚拟试穿', exact: true }).click();
  await page.locator('select:visible').first().selectOption('flux_klein_9b');
  await expect(page.getByRole('button', { name: '生成六视图试穿' })).toBeDisabled();
  await expect(page.getByText('当前生图服务暂不可用，请选择其他可用模型或稍后再试')).toBeVisible();
  await expect(page.locator('#flux_klein_9b_endpoint')).toHaveCount(0);
  await expect(page.locator('#flux_klein_9b_api_key')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '打开服务设置' })).toHaveCount(0);
  expect(requested).toEqual([]);
});
