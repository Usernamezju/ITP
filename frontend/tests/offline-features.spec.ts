import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

import { imageFromCanvas, triangleGlb } from './fixtures';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));

async function openStudio(page: Page, jobs: unknown[] = []) {
  if (jobs.length) await page.addInitScript(() => localStorage.setItem('itp.merchant.token', 'history-token'));
  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: false,
        provider: '', pose_provider: '', model: '3.1', pose_model: '' } });
    } else if (pathname === '/api/account/me') {
      await route.fulfill({ json: { id: 'history-user', name: 'history', display_name: 'History', role: 'customer', created: 1, contact: '' } });
    } else if (pathname === '/api/jobs') {
      await route.fulfill({ json: jobs });
    } else if (pathname === '/api/settings') {
      await route.fulfill({ json: { tencent_endpoint: '', tencent_region: '', tencent_model: '3.1',
        tencent_secret_id_set: false, tencent_secret_key_set: false, pose_endpoint: '',
        pose_model: 'qwen-image-edit-plus-2025-12-15', pose_api_key_set: false } });
    } else if (/^\/api\/assets\/[a-f0-9]{32}\/file$/.test(pathname)) {
      // Results are downloaded into this browser before the server drops them.
      await route.fulfill({ body: triangleGlb(), contentType: 'model/gltf-binary' });
    } else if (/^\/api\/assets\/[a-f0-9]{32}$/.test(pathname)) {
      await route.fulfill({ json: { id: pathname.split('/').pop(), url: '', kind: 'model',
        size: 100, format: 'GLB' } });
    } else if (/^\/api\/jobs\/[a-f0-9]{32}\/acknowledge$/.test(pathname)) {
      await route.fulfill({ status: 204, body: '' });
    } else if (pathname.startsWith('/assets/')) {
      const name = pathname.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) { await route.fulfill({ status: 404 }); return; }
      await route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    } else if (pathname.startsWith('/api/')) {
      await route.fulfill({ status: 404 });
    } else {
      // Every page URL gets the app itself, exactly like the real server.
      await route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
    }
  });
  await page.goto('/');
}

test('generate checklist and uploaded image preview', async ({ page }) => {
  await openStudio(page);
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.getByText('请上传角色图片')).toBeVisible();
  // The picture is read here, in this browser; nothing is uploaded yet.
  await page.getByLabel('上传上传角色图片').setInputFiles({
    name: 'character.png', mimeType: 'image/png', buffer: await imageFromCanvas(page),
  });
  await expect(page.getByText('请上传角色图片')).toHaveCount(0);
  await page.getByRole('button', { name: '放大查看上传角色图片' }).click();
  await expect(page.getByRole('dialog', { name: '预览上传角色图片' })).toBeVisible();
  await page.getByRole('button', { name: '关闭图片预览' }).click();
  await expect(page.getByRole('dialog', { name: '预览上传角色图片' })).toHaveCount(0);
  await page.getByRole('button', { name: '自定义' }).click();
  await expect(page.getByText('请上传姿势参考图')).toBeVisible();
  await page.getByLabel('几何目标面数').selectOption('1500000');
  await expect(page.getByLabel('几何目标面数')).toHaveValue('1500000');
});

test('color and contrast themes persist after reload', async ({ page }) => {
  await openStudio(page);
  await page.getByRole('link', { name: '外观', exact: true }).click();
  await page.getByRole('radio', { name: /科技风/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'tech');
  await page.reload();
  await page.getByRole('link', { name: '外观', exact: true }).click();
  await expect(page.getByRole('radio', { name: /科技风/ })).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('radio', { name: /少女粉/ }).click();
  await page.getByRole('radio', { name: '高对比度' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'pink');
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  await page.reload();
  await page.getByRole('link', { name: '外观', exact: true }).click();
  await expect(page.getByRole('radio', { name: /少女粉/ })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByRole('radio', { name: '高对比度' })).toHaveAttribute('aria-checked', 'true');
});

test('old provider error displays a useful explanation', async ({ page }) => {
  await openStudio(page, [{
    id: 'b'.repeat(32), name: '失败任务', state: 'failed', created: 1790671818,
    error: '腾讯云错误 ResourceUnavailable.NotExist；RequestId=request-123',
    request: { front: 'a'.repeat(32), pose_mode: 'original', topology: false,
      texture: false, rig: false, export_fbx: false }, pose_asset: null,
    steps: [{ name: 'geometry', status: 'failed' }], artifacts: [],
  }]);
  await page.getByRole('link', { name: '任务记录' }).click();
  await page.getByRole('button', { name: /失败任务/ }).click();
  await expect(page.getByText(/服务未开通或计费状态异常/)).toBeVisible();
});

test('a failed rig step keeps generated models visible as partial success', async ({ page }) => {
  await openStudio(page, [{
    id: 'c'.repeat(32), name: '已生成角色', state: 'failed', created: 1790671818,
    error: '腾讯云错误 InvalidParameter.InvalidParameter：请求参数不被接受；请核对图片、模型版本和生成选项；RequestId=rig-request',
    request: { front: 'a'.repeat(32), pose_mode: 'original', topology: false,
      texture: true, rig: true, export_fbx: true, face_count: 1500000 }, pose_asset: null,
    steps: [{ name: 'geometry', status: 'done' }, { name: 'texture', status: 'done' },
      { name: 'rig', status: 'failed' }],
    artifacts: [{ asset_id: 'd'.repeat(32), stage: 'geometry', format: 'GLB', index: 0 },
      { asset_id: 'e'.repeat(32), stage: 'texture', format: 'GLB', index: 0 }],
  }]);
  await page.getByRole('link', { name: '任务记录' }).click();
  await expect(page.getByRole('button', { name: /已生成角色/ }).getByText('部分完成')).toBeVisible();
  await page.getByRole('button', { name: /已生成角色/ }).click();
  await expect(page.getByLabel('资产生成参数')).toContainText('1,500,000');
  await expect(page.getByLabel('资产生成参数')).toContainText('自动绑骨开启');
  await expect(page.getByLabel('资产生成参数')).toContainText('混元生3D Pro · 版本未记录');
  await expect(page.getByText('已完成几何生成、PBR 纹理，自动绑骨未完成。已有产物仍可预览、下载。')).toBeVisible();
  await expect(page.getByText(/绑骨接口未接受输入模型/)).toBeVisible();
  // The file now comes out of this browser, not from a server route.
  const download = page.getByRole('link', { name: '下载PBR 纹理GLB' });
  await expect(download).toHaveAttribute('download', '已生成角色-texture.glb');
  await expect(download).toHaveAttribute('href', /^blob:/);
});

test('asset page shows saved generation model and processing options', async ({ page }) => {
  await openStudio(page, [{
    id: 'f'.repeat(32), name: '新资产', state: 'succeeded', created: 1790671818, error: null,
    request: { front: 'a'.repeat(32), pose_mode: 'a-pose', topology: true,
      texture: false, rig: false, export_fbx: false, face_count: 500000 },
    models: { geometry: '3.1', pose: 'qwen-image-edit-plus-2025-12-15' },
    pose_asset: null, steps: [], artifacts: [],
  }]);
  await page.getByRole('link', { name: '任务记录' }).click();
  await page.getByRole('button', { name: /新资产/ }).click();
  const details = page.getByLabel('资产生成参数');
  await expect(details).toContainText('500,000');
  await expect(details).toContainText('A-Pose');
  await expect(details).toContainText('智能拓扑开启');
  await expect(details).toContainText('PBR 纹理关闭');
  await expect(details).toContainText('自动绑骨关闭');
  await expect(details).toContainText('混元生3D Pro · 3.1');
  await expect(details).toContainText('qwen-image-edit-plus-2025-12-15');
});
