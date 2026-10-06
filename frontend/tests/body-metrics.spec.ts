import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));

const emptyProfile = {
  height_cm: null, weight_kg: null, shoulder_cm: null,
  bust_cm: null, waist_cm: null, hip_cm: null, job_id: null,
};

/** Serves the built bundle offline and records every body-profile write. */
async function openWorkspace(page: Page, profile: unknown = emptyProfile) {
  const writes: Record<string, unknown>[] = [];
  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: true,
        tryon: false, tryon_model: '', tryon_providers: { seedream: false, flux: false,
          flux_klein: false, gpt_image: false }, faceverse: false, faceverse_model: '',
        outfit_images: true, image_provider: 'so', provider: '', pose_provider: '',
        model: '3.1', pose_model: '' } });
    } else if (pathname === '/api/jobs') {
      await route.fulfill({ json: [] });
    } else if (pathname === '/api/body-profile') {
      if (request.method() === 'PUT') {
        writes.push(request.postDataJSON() as Record<string, unknown>);
        await route.fulfill({ json: { ...(request.postDataJSON() as object) } });
      } else {
        await route.fulfill({ json: profile });
      }
    } else if (pathname === '/') {
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    } else if (pathname.startsWith('/assets/')) {
      const name = pathname.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else {
      await route.fulfill({ status: 404 });
    }
  });
  await page.goto('/');
  return writes;
}

test('body measurements are optional, prefilled and saved as numbers', async ({ page }) => {
  const writes = await openWorkspace(page);
  const panel = page.getByRole('region', { name: '人体数据' });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('选填');
  await expect(page.getByLabel('身高（cm）')).toHaveValue('');
  await expect(page.getByRole('button', { name: /保存人体数据/ })).toBeDisabled();

  await page.getByLabel('身高（cm）').fill('170');
  await page.getByLabel('胸围（cm）').fill('88.5');
  await page.getByRole('button', { name: /保存人体数据/ }).click();
  await expect(page.getByText('已保存，可用于尺码推荐')).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0].height_cm).toBe(170);
  expect(writes[0].bust_cm).toBe(88.5);
  // Untouched fields are cleared explicitly so an old value cannot linger.
  expect(writes[0].waist_cm).toBeNull();
});

test('out-of-range measurements are refused before any request', async ({ page }) => {
  const writes = await openWorkspace(page, { ...emptyProfile, height_cm: 170 });
  await expect(page.getByLabel('身高（cm）')).toHaveValue('170');
  await page.getByLabel('身高（cm）').fill('90');
  await page.getByRole('button', { name: /保存人体数据/ }).click();
  await expect(page.getByRole('alert')).toContainText('身高应在 120–220cm 之间');
  expect(writes).toHaveLength(0);
});

test('a saved profile comes back filled in', async ({ page }) => {
  await openWorkspace(page, { height_cm: 168, weight_kg: 55.5, shoulder_cm: 38,
    bust_cm: 86, waist_cm: 68, hip_cm: 92, job_id: null });
  await expect(page.getByLabel('身高（cm）')).toHaveValue('168');
  await expect(page.getByLabel('肩宽（cm）')).toHaveValue('38');
  await expect(page.getByLabel('体重（kg）')).toHaveValue('55.5');
  await expect(page.getByRole('region', { name: '人体数据' })).toContainText('6/6');
});
