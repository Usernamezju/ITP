import { expect, test } from '@playwright/test';

async function imageFromCanvas(page: import('@playwright/test').Page): Promise<Buffer> {
  const encoded = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    const context = canvas.getContext('2d')!;
    context.fillStyle = '#b96139';
    context.fillRect(0, 0, 256, 256);
    context.fillStyle = '#f0db9e';
    context.fillRect(90, 30, 80, 190);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  return Buffer.from(encoded, 'base64');
}

function triangleGlb(): Buffer {
  const positions = Buffer.alloc(36);
  [-1, 0, 0, 1, 0, 0, 0, 1, 0].forEach((value, index) => positions.writeFloatLE(value, index * 4));
  const indices = Buffer.alloc(8);
  [0, 1, 2].forEach((value, index) => indices.writeUInt16LE(value, index * 2));
  const binary = Buffer.concat([positions, indices]);
  const document = {
    asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    buffers: [{ byteLength: binary.length }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 },
      { buffer: 0, byteOffset: 36, byteLength: 6 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3',
      min: [-1, 0, 0], max: [1, 1, 0] },
    { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' }],
  };
  const json = Buffer.from(JSON.stringify(document));
  const jsonChunk = Buffer.alloc(Math.ceil(json.length / 4) * 4, 0x20);
  json.copy(jsonChunk);
  const header = Buffer.alloc(12);
  header.write('glTF'); header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonChunk.length + 8 + binary.length, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonChunk.length, 0); jsonHeader.write('JSON', 4);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binary.length, 0); binHeader.write('BIN\0', 4);
  return Buffer.concat([header, jsonHeader, jsonChunk, binHeader, binary]);
}

test('offline workspace accepts uploads, controls and local GLB preview', async ({ page, isMobile }) => {
  await page.route('**/api/capabilities', (route) => route.fulfill({ json: {
    geometry: false, pose: false, segmentation: false,
    provider: '', pose_provider: '', model: '3.1', pose_model: '',
  } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '从一张图，到一个世界' })).toBeVisible();
  await page.getByRole('button', { name: '虚拟试穿' }).click();
  await expect(page.getByRole('heading', { name: '虚拟试穿', exact: true })).toBeVisible();
  await expect(page.locator('.tryon-input input[type="file"]')).toHaveCount(12);
  await page.getByRole('button', { name: '人体建模' }).click();
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
  await page.getByRole('button', { name: '任务记录' }).click();
  await expect(page.getByText('第一件作品，从这里开始')).toBeVisible();
  await page.getByRole('button', { name: '外观', exact: true }).click();
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
  await page.getByRole('button', { name: '外观', exact: true }).click();
  await expect(page.getByRole('heading', { name: '工作台主题' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '模型服务' })).toHaveCount(0);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '保存设置' })).toHaveCount(0);
  expect(settingsRequests).toBe(0);
});
