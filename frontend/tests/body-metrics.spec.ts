import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));

/**
 * Serves the built bundle offline and fails any request the panel must not make:
 * the measurements belong to the browser, so no body-profile call may leave it.
 */
async function openWorkspace(page: Page) {
  const profileCalls: string[] = [];
  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: true,
        tryon: false, tryon_model: '', tryon_providers: { seedream: false, flux: false,
          flux_klein: false, gpt_image: false }, faceverse: false, faceverse_model: '',
        outfit_images: true, image_provider: 'so', provider: '', pose_provider: '',
        model: '3.1', pose_model: '' } });
    } else if (pathname.startsWith('/api/body-profile')) {
      profileCalls.push(request.method());
      await route.fulfill({ status: 500, json: { detail: '人体数据不应离开浏览器' } });
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
  // The panel is inert until this browser's own store has been read back;
  // typing before that would be overwritten by the stored values.
  const panel = page.getByRole('region', { name: '人体数据' });
  await expect(panel).toBeVisible();
  await expect(panel).not.toHaveAttribute('aria-busy', 'true');
  return profileCalls;
}

test('body measurements are optional, saved locally and read back after reload', async ({ page }) => {
  const profileCalls = await openWorkspace(page);
  const panel = page.getByRole('region', { name: '人体数据' });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('选填');
  await expect(panel).toContainText('仅存本机');
  await expect(page.getByLabel('身高（cm）')).toHaveValue('');
  await expect(page.getByRole('button', { name: /保存人体数据/ })).toBeDisabled();

  await page.getByLabel('身高（cm）').fill('170');
  await page.getByLabel('胸围（cm）').fill('88.5');
  await page.getByRole('button', { name: /保存人体数据/ }).click();
  await expect(page.getByText('已保存在本机，可用于尺码推荐')).toBeVisible();

  await page.reload();
  const saved = page.getByRole('region', { name: '人体数据' });
  await expect(saved).toContainText('2/6');
  await expect(page.getByLabel('身高（cm）')).toHaveValue('170');
  await expect(page.getByLabel('胸围（cm）')).toHaveValue('88.5');
  // Untouched fields stay empty instead of taking a guessed value.
  await expect(page.getByLabel('腰围（cm）')).toHaveValue('');
  expect(profileCalls).toEqual([]);
});

test('out-of-range measurements are refused before anything is stored', async ({ page }) => {
  await openWorkspace(page);
  await page.getByLabel('身高（cm）').fill('170');
  await page.getByRole('button', { name: /保存人体数据/ }).click();
  await expect(page.getByText('已保存在本机，可用于尺码推荐')).toBeVisible();
  await page.getByLabel('身高（cm）').fill('90');
  await page.getByRole('button', { name: /保存人体数据/ }).click();
  await expect(page.getByRole('alert')).toContainText('身高应在 120–220cm 之间');
  // The refused value never reaches the browser store either.
  await page.reload();
  await expect(page.getByLabel('身高（cm）')).toHaveValue('170');
});
