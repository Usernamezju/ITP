import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXcAAAAASUVORK5CYII=', 'base64');

const analysis = {
  available: true,
  method: '从三维模型包围盒与轮廓切片估算',
  labels: { build: '修长', volume: '轻量', legs: '长腿型', pose: 'T-Pose' },
  metrics: [
    { label: '身高 / 肩宽', value: '4.32', hint: '肩宽较窄，收肩或落肩结构更协调' },
    { label: '腰宽 / 肩宽', value: '0.74', hint: '腰线清晰，可用高腰强化比例' },
  ],
  profile: [0.08, 0.21, 0.44, 0.51, 0.47, 0.39, 0.42, 0.5, 0.46, 0.3,
    0.27, 0.26, 0.25, 0.23, 0.22, 0.2, 0.14, 0.1, 0.05, 0.02],
  notes: ['轮廓按身体高度等分为 20 段。'],
  tags: ['slim', 'light', 'long-leg'],
};

const outfits = [
  {
    id: 'soft-tailoring', name: '柔雾通勤', tagline: '收肩落肩，把利落留在轮廓里',
    story: '用垂坠面料和干净配色处理日常通勤。', style: '通勤', season: '四季', occasion: '通勤办公',
    palette: ['#c9d6bd', '#4a5b52', '#b9a892', '#8d7a63'],
    items: [
      { category: '上装', name: '落肩针织开衫', color: '#c9d6bd', note: '肩线内收，弱化肩宽' },
      { category: '下装', name: '直筒西裤', color: '#4a5b52', note: '垂坠面料保持纵向线条' },
    ],
    tips: ['把上衣下摆收进裤腰，抬高腰线。'],
    avoid: '避免过厚的肩部结构。',
    reason: '模型肩宽较窄、腿身比偏长，收肩结构与高腰下装能保持整体比例。',
    score: 0.91, matched: ['slim', 'long-leg'],
  },
  {
    id: 'street-layers', name: '街头层次', tagline: '宽松轮廓，把体量藏在层次里',
    story: '落肩卫衣与工装长裤组成日常街头层次。', style: '街头', season: '秋', occasion: '日常出行',
    palette: ['#5c6b58', '#2f3a34', '#c2b49a', '#8a6f4e'],
    items: [{ category: '外套', name: '落肩连帽卫衣', color: '#5c6b58', note: '落肩削弱肩部棱角' }],
    tips: ['保持上下同色系，避免横向切割。'],
    avoid: '避免紧身下装，会放大上半身体量。',
    reason: '模型体量偏轻，宽松层次能补足轮廓。',
    score: 0.78, matched: ['light'],
  },
  {
    id: 'resort-linen', name: '海岸亚麻', tagline: '轻薄面料，留住夏天的风',
    story: '亚麻衬衫与阔腿短裤，适合高温度假。', style: '度假', season: '夏', occasion: '旅行度假',
    palette: ['#e8ddc8', '#7f9a92', '#d9c3a5', '#4f6f68'],
    items: [{ category: '上装', name: '亚麻短袖衬衫', color: '#e8ddc8', note: '浅色反射热量' }],
    tips: ['选择天然纤维，保持透气。'],
    avoid: '避免厚重深色叠穿。',
    reason: '通用体型推荐，夏季优先轻薄与浅色。',
    score: 0.72, matched: [],
  },
];

const filters = {
  styles: [{ id: '通勤', count: 3 }, { id: '街头', count: 2 }, { id: '度假', count: 1 }],
  seasons: [{ id: '四季', count: 4 }, { id: '夏', count: 2 }],
  occasions: [{ id: '通勤办公', count: 3 }, { id: '日常出行', count: 2 }],
};

const sha = (letter: string) => letter.repeat(40);
const imagePayload = {
  outfit_id: 'soft-tailoring', query: '通勤 针织开衫 西裤', provider: 'so',
  provider_label: '360 图片', cached: true, error: null,
  images: [
    { id: sha('a'), url: `/api/outfit-images/${sha('a')}.jpg`, original_url: 'https://example.com/full.jpg',
      source_url: 'https://example.com/page', site: 'example.com', title: '初秋打造OL通勤风格',
      width: 632, height: 495, creator: null, license: null },
    { id: sha('b'), url: `/api/outfit-images/${sha('b')}.jpg`, original_url: 'https://example.com/full2.jpg',
      source_url: 'https://example.com/page2', site: 'example.com', title: '春季职场穿搭',
      width: 640, height: 800, creator: null, license: null },
  ],
};

/** Serves the built bundle offline, records outfits requests and can fail image search. */
async function openOutfits(page: Page, body: unknown, jobs: unknown[] = [], images: unknown = imagePayload) {
  const requests: string[] = [];
  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: true,
        tryon: false, tryon_model: '', tryon_providers: { seedream: false, flux: false, flux_max: false,
          flux_klein: false, flux_klein_9b: false, gpt_image: false },
        faceverse: false, faceverse_model: '',
        outfit_images: true, image_provider: 'so', provider: '', pose_provider: '',
        model: '3.1', pose_model: '' } });
    } else if (pathname === '/api/jobs') {
      await route.fulfill({ json: jobs });
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
  await page.route('**/api/outfits?*', async (route) => {
    requests.push(new URL(route.request().url()).search);
    await route.fulfill({ json: body });
  });
  // Registered last, so the more specific paths win over the two routes above.
  await page.route('**/api/outfits/*/images*', async (route) => {
    requests.push(new URL(route.request().url()).search);
    await route.fulfill({ json: images });
  });
  await page.route('**/api/outfit-images/*', async (route) => {
    await route.fulfill({ body: pixel, contentType: 'image/png' });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '穿搭推荐' }).click();
  return requests;
}

const analysisPayload = (available: boolean) => ({
  source: available ? 'model' : 'default',
  analysis: { ...analysis, available, labels: available ? analysis.labels : {},
    metrics: available ? analysis.metrics : [], profile: available ? analysis.profile : null,
    notes: available ? analysis.notes : ['尚未生成三维模型，已按通用体型推荐。'],
    tags: available ? analysis.tags : [],
    body: { height_cm: { value: 170, source: 'input' },
      bust_cm: { value: 88, source: 'input' },
      hip_cm: { value: 92, source: 'estimated' },
      waist_cm: { value: null, source: 'missing' } } },
  filters,
  recommendations: outfits.map((outfit, index) => index === 0 ? { ...outfit, fit } : outfit),
});

const fit = {
  score: 0.88, fit_score: 0.92, preference_score: 0.7, confidence: 0.75,
  dimensions: [
    { key: 'bust_cm', label: '胸围', weight: 0.3, score: 1, state: 'fit',
      body_value: 88, body_source: 'input', range: [88, 100],
      detail: '你的胸围 88cm 落在该款适配区间 88–100cm 内', delta_cm: 0 },
    { key: 'shoulder_cm', label: '肩宽', weight: 0.25, score: 0.4, state: 'tight',
      body_value: 38, body_source: 'estimated', range: [36, 37],
      detail: '肩宽超出适配区间 1cm，落肩结构可缓解', delta_cm: 1 },
  ],
  reasons: ['胸围与肩宽是决定合身度的主要维度，胸围完全落在区间内。'],
  warnings: ['肩宽偏紧 1cm，建议确认落肩幅度'],
  suggestions: [],
};

test('outfit page shows measured analysis, photos and ranked looks', async ({ page }) => {
  await openOutfits(page, analysisPayload(true));
  await expect(page.getByRole('heading', { name: '穿搭推荐', exact: true })).toBeVisible();
  await expect(page.getByText('三维模型分析')).toBeVisible();
  await expect(page.getByText('从三维模型包围盒与轮廓切片估算')).toBeVisible();
  await expect(page.getByText('身高 / 肩宽')).toBeVisible();
  await expect(page.getByText('4.32')).toBeVisible();
  await expect(page.locator('.outfits-labels')).toContainText('修长');
  await expect(page.getByRole('img', { name: '三维模型轮廓切片' })).toBeVisible();
  await expect(page.locator('.outfit-card')).toHaveCount(3);
  const first = page.locator('.outfit-card').first();
  await expect(first.locator('.outfit-score')).toHaveText('91');
  // Pictures are fetched only once a card scrolls near the viewport, and on a
  // phone the analysis panel sits above the grid, so scroll before asserting.
  await page.locator('.outfits-grid').scrollIntoViewIfNeeded();
  await expect(first.getByRole('img', { name: '初秋打造OL通勤风格' })).toBeVisible({ timeout: 15000 });
  await first.getByRole('button', { name: '换一张柔雾通勤的参考图' }).click();
  await expect(first.getByRole('img', { name: '春季职场穿搭' })).toBeVisible({ timeout: 15000 });
});

test('outfit detail dialog shows photos, source links, items and tips', async ({ page }) => {
  await openOutfits(page, analysisPayload(true));
  await page.getByRole('button', { name: '查看柔雾通勤详情' }).click();
  const dialog = page.getByRole('dialog', { name: '柔雾通勤 穿搭详情' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('收肩落肩，把利落留在轮廓里');
  await expect(dialog.getByRole('img', { name: '初秋打造OL通勤风格' })).toBeVisible({ timeout: 15000 });
  await expect(dialog.getByRole('link', { name: '来源' })).toHaveAttribute('href', 'https://example.com/page');
  await expect(dialog.getByRole('link', { name: '原图' })).toHaveAttribute('href', 'https://example.com/full.jpg');
  await expect(dialog).toContainText('图片由360 图片检索，版权归原作者所有');
  await expect(dialog).toContainText('落肩针织开衫');
  await expect(dialog).toContainText('肩线内收，弱化肩宽');
  await expect(dialog).toContainText('把上衣下摆收进裤腰，抬高腰线。');
  await expect(dialog).toContainText('避免过厚的肩部结构。');
  await expect(dialog).toContainText('匹配度 91');
  await page.getByRole('button', { name: '关闭穿搭详情' }).click();
  await expect(dialog).toHaveCount(0);
});

test('a failed image search falls back to the drawn stand-in and can be retried', async ({ page }) => {
  const requests = await openOutfits(page, analysisPayload(true), [], {
    outfit_id: 'soft-tailoring', query: '通勤 针织开衫 西裤', provider: 'so',
    provider_label: '360 图片', cached: false, error: '图片检索超时，请稍后重试',
    images: [],
  });
  const first = page.locator('.outfit-card').first();
  await page.locator('.outfits-grid').scrollIntoViewIfNeeded();
  await expect(first.getByRole('img', { name: '柔雾通勤 穿搭示意' })).toBeVisible({ timeout: 15000 });
  await expect(first.getByRole('button', { name: '换一张柔雾通勤的参考图' })).toHaveCount(0);
  // The retry must bypass the server's short negative cache.
  await first.getByRole('button', { name: '重新获取柔雾通勤的参考图' }).click();
  await expect.poll(() => requests.some((query) => query.includes('refresh=true'))).toBe(true);
  await page.getByRole('button', { name: '查看柔雾通勤详情' }).click();
  const dialog = page.getByRole('dialog', { name: '柔雾通勤 穿搭详情' });
  await expect(dialog.getByRole('img', { name: '柔雾通勤 穿搭示意' })).toBeVisible({ timeout: 15000 });
  await expect(dialog).toContainText('图片检索超时，请稍后重试');
  await expect(dialog.getByRole('button', { name: '重新检索' })).toBeVisible();
});

test('collected measurements and per-dimension fit are shown', async ({ page }) => {
  await openOutfits(page, analysisPayload(true));
  const body = page.locator('.outfits-body');
  await expect(body).toContainText('人体数据');
  await expect(body).toContainText('身高');
  await expect(body).toContainText('170cm');
  await expect(body).toContainText('已填');
  // Estimated values are marked as such, so nobody reads them as measured.
  await expect(body.locator('.outfits-body-chip.estimated')).toContainText('臀围');
  await expect(body.locator('.outfits-body-chip.estimated')).toContainText('估算');

  await page.getByRole('button', { name: '查看柔雾通勤详情' }).click();
  const dialog = page.getByRole('dialog', { name: '柔雾通勤 穿搭详情' });
  const block = dialog.locator('.outfit-fit');
  await expect(block).toContainText('尺码匹配');
  await expect(block).toContainText('胸围');
  await expect(block).toContainText('合身');
  await expect(block).toContainText('肩宽');
  await expect(block).toContainText('偏紧');
  await expect(block).toContainText('你的胸围 88cm 落在该款适配区间 88–100cm 内');
  await expect(block).toContainText('肩宽偏紧 1cm，建议确认落肩幅度');
  await expect(block).toContainText('匹配可信度 75%');
});

test('without a model the page still recommends and points at modeling', async ({ page }) => {
  await openOutfits(page, analysisPayload(false));
  await expect(page.locator('.outfits-stage-heading')).toContainText('通用体型推荐');
  await expect(page.getByRole('heading', { name: '尚未生成三维模型' })).toBeVisible();
  await expect(page.locator('.outfit-card')).toHaveCount(3);
  // Every look scores the same without a model, so no meaningless match badge is shown.
  await expect(page.locator('.outfit-score')).toHaveCount(0);
  await page.getByRole('button', { name: /前往人体建模/ }).click();
  await expect(page.getByRole('heading', { name: '从一张图，到一个世界' })).toBeVisible();
});

test('filtering and the model source reach the outfits endpoint', async ({ page }) => {
  const job = {
    id: 'a'.repeat(32), name: '比例测试', state: 'succeeded', created: 1790671818, error: null,
    request: { front: 'b'.repeat(32), pose_mode: 't-pose', topology: false, texture: true,
      rig: false, export_fbx: false },
    pose_asset: null, steps: [], artifacts: [{ asset_id: 'c'.repeat(32), stage: 'geometry', format: 'GLB', index: 0 }],
  };
  const requests = await openOutfits(page, analysisPayload(true), [job]);
  await expect.poll(() => requests.some((query) => query.includes(`job_id=${job.id}`))).toBe(true);
  await page.locator('.outfits-filter').filter({ hasText: '风格' })
    .getByRole('button', { name: /通勤/ }).click();
  await expect.poll(() => requests.some((query) => query.includes('style='))).toBe(true);
  await page.getByRole('button', { name: '查看全部套装' }).click();
  await expect.poll(() => requests.some((query) => query.includes('limit=24'))).toBe(true);
});
