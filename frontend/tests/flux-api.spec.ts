import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

import { imageFromCanvas } from './fixtures';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));

for (const [provider, model, label] of [
  ['flux', 'flux-2-pro', 'FLUX.2 Pro'],
  ['flux_max', 'flux-2-max', 'FLUX.2 Max'],
] as const) {
  for (const available of [false, true]) {
    test(`${label} is server-managed and ${available ? 'usable' : 'unavailable'} without client keys`, async ({ page }) => {
      const settingsRequests: string[] = [];
      let submitted: Record<string, unknown> | undefined;
      // The temporary server copy of a picture this browser already holds.
      const uploaded = { id: 'd'.repeat(32), url: '', kind: 'image', size: 100 };
      await page.route('**/*', async (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        if (pathname === '/') {
          await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
        } else if (pathname.startsWith('/assets/')) {
          const name = pathname.slice('/assets/'.length);
          if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
          await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
            contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
        } else if (pathname === '/api/settings') {
          settingsRequests.push(request.method());
          await route.fulfill({ status: 404 });
        } else if (pathname === '/api/capabilities') {
          await route.fulfill({ json: {
            geometry: false, pose: false, segmentation: false, tryon: false, tryon_model: '',
            tryon_providers: { seedream: false, flux: provider === 'flux' && available,
              flux_max: provider === 'flux_max' && available, flux_klein: false,
              flux_klein_9b: false, gpt_image: false },
            faceverse: false, faceverse_model: '', outfit_images: false, image_provider: 'so',
            provider: '', pose_provider: '', model: '3.1', pose_model: '',
          } });
        } else if (pathname === '/api/tryons' && request.method() === 'POST') {
          submitted = request.postDataJSON();
          await route.fulfill({ json: { id: 'b'.repeat(32), name: '虚拟试穿', state: 'ready',
            model, provider, results: {}, active_view: null } });
        } else if (pathname === '/api/assets' && request.method() === 'POST') {
          // The one temporary copy of a picture kept in this browser.
          await route.fulfill({ status: 201, json: uploaded });
        } else if (pathname === '/api/jobs' || pathname === '/api/tryons') {
          await route.fulfill({ json: [] });
        } else {
          await route.fulfill({ status: 404 });
        }
      });
      await page.goto('/');
      const image = await imageFromCanvas(page);
      await expect(page.getByRole('button', { name: '设置', exact: true })).toHaveCount(0);
      await page.getByRole('button', { name: '虚拟试穿', exact: true }).click();
      const picker = page.locator('select:visible').first();
      await expect(picker.locator('option', { hasText: 'FLUX.2 Pro' })).toHaveCount(1);
      await expect(picker.locator('option', { hasText: 'FLUX.2 Max' })).toHaveCount(1);
      await picker.selectOption(provider);
      await expect(page.getByRole('button', { name: '打开服务设置' })).toHaveCount(0);
      await expect(page.locator('input[type="password"]')).toHaveCount(0);
      // Both pictures are chosen here and kept locally; only the run itself
      // sends a temporary copy.
      const upload = page.getByLabel('上传正面');
      await upload.first().setInputFiles({ name: 'person.png', mimeType: 'image/png', buffer: image });
      await upload.last().setInputFiles({ name: 'garment.png', mimeType: 'image/png', buffer: image });
      await expect(page.getByAltText('正面')).toHaveCount(2);
      const generate = page.getByRole('button', { name: '生成六视图试穿' });
      if (!available) {
        await expect(generate).toBeDisabled();
        await expect(page.getByText('当前生图服务暂不可用，请选择其他可用模型或稍后再试')).toBeVisible();
      } else {
        await expect(generate).toBeEnabled();
        await generate.click();
        await expect.poll(() => submitted?.provider).toBe(provider);
        expect(submitted?.person).toEqual({ front: uploaded.id });
        expect(submitted?.garment).toEqual({ front: uploaded.id });
        expect(Object.keys(submitted!)).not.toEqual(expect.arrayContaining(['api_key', 'endpoint', 'token']));
      }
      expect(settingsRequests).toEqual([]);
    });
  }
}
