import { expect, test } from '@playwright/test';

import { imageFromCanvas, triangleGlb } from './fixtures';

test('offline workspace accepts uploads, controls and local GLB preview', async ({ page, isMobile }) => {
  await page.route('**/api/capabilities', (route) => route.fulfill({ json: {
    geometry: false, pose: false, segmentation: false,
    provider: '', pose_provider: '', model: '3.1', pose_model: '',
  } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '从一张图，到一个世界' })).toBeVisible();
  await page.getByRole('link', { name: '虚拟试穿' }).click();
  await expect(page.getByRole('heading', { name: '虚拟试穿', exact: true })).toBeVisible();
  await expect(page.locator('.tryon-input input[type="file"]')).toHaveCount(12);
  await page.getByRole('link', { name: '人体建模' }).click();
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.getByText('请上传角色图片')).toBeVisible();
  await expect(page.getByText('人体建模服务暂不可用，请稍后再试')).toBeVisible();
  if (!isMobile) await expect(page.getByRole('heading', { name: '服务状态' })).toBeVisible();

  const image = await imageFromCanvas(page);
  await page.getByLabel('上传上传角色图片').setInputFiles({
    name: 'character.png', mimeType: 'image/png', buffer: image,
  });
  await expect(page.getByAltText('上传角色图片')).toBeVisible();
  await expect(page.getByText('请上传角色图片')).toHaveCount(0);
  await page.getByRole('button', { name: '放大查看上传角色图片' }).click();
  await expect(page.getByRole('dialog', { name: '预览上传角色图片' })).toBeVisible();
  await page.getByRole('button', { name: '关闭图片预览' }).click();
  await page.getByRole('button', { name: '自定义' }).click();
  await expect(page.getByText('请上传姿势参考图')).toBeVisible();
  await expect(page.getByLabel('上传上传姿势参考图')).toBeVisible();
  await page.getByLabel('上传上传姿势参考图').setInputFiles({
    name: 'pose.png', mimeType: 'image/png', buffer: image,
  });
  await expect(page.getByAltText('上传姿势参考图')).toBeVisible();
  await expect(page.getByRole('switch', { name: /自动绑骨/ })).toBeDisabled();

  await page.getByLabel('导入 GLB 模型').setInputFiles({
    name: 'triangle.glb', mimeType: 'model/gltf-binary', buffer: triangleGlb(),
  });
  await expect(page.getByText('1 三角面')).toBeVisible();
  await page.getByRole('link', { name: '任务记录' }).click();
  await expect(page.getByText('第一件作品，从这里开始')).toBeVisible();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await expect(page.getByRole('heading', { name: '工作台主题' })).toBeVisible();
  await expect(page.locator('#faceverse_endpoint')).toHaveCount(0);
  await expect(page.getByLabel('Secret Key', { exact: true })).toHaveCount(0);
});

test('appearance is browser-local and no provider credentials are requested', async ({ page }) => {
  let settingsRequests = 0;
  await page.route('**/api/settings', (route) => {
    settingsRequests++;
    return route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await expect(page.getByRole('heading', { name: '工作台主题' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '模型服务' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '登录账号', exact: true })).toBeVisible();
  await expect(page.locator('.theme-section input[type="password"]')).toHaveCount(0);
  await expect(page.locator('[id$="api_key"], [id$="endpoint"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '保存设置' })).toHaveCount(0);
  expect(settingsRequests).toBe(0);
});
