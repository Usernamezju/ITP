import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const TOKEN_KEY = 'itp.merchant.token';

/** The reference document the form is generated from, as the API serves it. */
const options = {
  categories: ['上装', '下装', '外套', '鞋履', '配饰'],
  styles: ['通勤', '休闲', '街头'],
  seasons: ['春', '夏', '秋', '冬', '四季'],
  silhouettes: ['修身', '标准', '宽松', 'oversize'],
  stretches: ['无弹', '微弹', '高弹'],
  length_types: ['短款', '常规', '长款'],
  statuses: ['draft', 'published'],
  measurements: [
    { key: 'shoulder_cm', label: '肩宽', max: 300 },
    { key: 'bust_cm', label: '胸围', max: 300 },
    { key: 'waist_cm', label: '腰围', max: 300 },
    { key: 'hip_cm', label: '臀围', max: 300 },
    { key: 'length_cm', label: '衣长', max: 300 },
    { key: 'hem_cm', label: '下摆', max: 300 },
  ],
  fit_ranges: [
    { key: 'height_cm', label: '适合身高', min: 120, max: 220 },
    { key: 'bust_cm', label: '适合胸围', min: 60, max: 160 },
    { key: 'waist_cm', label: '适合腰围', min: 45, max: 150 },
    { key: 'hip_cm', label: '适合臀围', min: 60, max: 170 },
    { key: 'shoulder_cm', label: '适合肩宽', min: 25, max: 70 },
  ],
  body_profile: [
    { key: 'height_cm', label: '身高', min: 100, max: 250 },
    { key: 'weight_kg', label: '体重', min: 20, max: 300 },
  ],
  limits: {
    name_max: 200, short_text_max: 80, description_max: 1000, tips_max: 3, tip_max: 120,
    price_max_cents: 10 ** 12, weight_gsm_min: 20, weight_gsm_max: 2000, image_max_mb: 10,
    images_max: 8, palette_max: 6, look_items_max: 12, look_name_max: 80, look_story_max: 1000,
  },
};

const profile = {
  merchant_id: 'a'.repeat(32), name: 'demo-shop', display_name: '示例商家',
  contact: 'demo@example.com', created: 1, quota: 200, garment_count: 2,
};

function metrics(overrides: Record<string, unknown> = {}) {
  return {
    category: '上装', name: '细罗纹半高领针织', sku: null, brand: null, price_cents: 26900,
    measurements: { shoulder_cm: 39, bust_cm: 100, waist_cm: null, hip_cm: null,
      length_cm: 62, hem_cm: null },
    fit_ranges: { height_cm: [158, 176], bust_cm: [86, 96], waist_cm: null, hip_cm: null,
      shoulder_cm: null },
    attributes: { silhouette: '标准', stretch: '微弹', weight_gsm: 260, color: '#c9d6bd',
      length_type: '常规' },
    style: '通勤', season: '四季', occasion: '通勤办公', description: '雾面针织',
    tips: null, status: 'published',
    ...overrides,
  };
}

function garment(id: string, overrides: Record<string, unknown> = {}) {
  const built = metrics(overrides);
  return {
    id, merchant_id: profile.merchant_id, status: built.status, created: 1, updated: 1,
    metrics: built, images: [],
  };
}

type Recorded = { method: string; path: string; body: string };

/**
 * Serves the built bundle with a mocked merchant API and records every call, so
 * a test can assert both what was sent and what was deliberately not sent.
 */
async function openConsole(page: Page, state: {
  signedIn?: boolean; goods?: unknown[]; looks?: unknown[];
} = {}) {
  const calls: Recorded[] = [];
  const goods = [...(state.goods ?? [garment('g1'), garment('g2', {
    name: '高腰直筒西裤', category: '下装', status: 'draft',
    fit_ranges: { height_cm: [162, 176], waist_cm: [66, 76], hip_cm: null, bust_cm: null,
      shoulder_cm: null },
  })])];
  const looks = [...(state.looks ?? [])];

  if (state.signedIn !== false) {
    await page.addInitScript(([key, value]) => {
      window.localStorage.setItem(key, value);
    }, [TOKEN_KEY, 'test-token'] as const);
  }

  await page.route('**/*', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const method = request.method();
    if (pathname.startsWith('/api/')) calls.push({ method, path: pathname, body: request.postData() || '' });

    if (pathname === '/api/capabilities') {
      await route.fulfill({ json: { geometry: false, pose: false, segmentation: false,
        tryon: false, tryon_model: '', tryon_providers: { seedream: false, flux: false,
          flux_klein: false, gpt_image: false }, faceverse: false, faceverse_model: '',
        outfit_images: true, image_provider: 'so', provider: '', pose_provider: '',
        model: '3.1', pose_model: '' } });
    } else if (pathname === '/api/jobs') {
      await route.fulfill({ json: [] });
    } else if (pathname === '/api/garment-options') {
      await route.fulfill({ json: options });
    } else if (pathname === '/api/merchant/me') {
      await route.fulfill({ json: { ...profile, garment_count: goods.length } });
    } else if (pathname === '/api/merchant/login' && method === 'POST') {
      await route.fulfill({ json: { access_token: 'test-token', token_type: 'bearer',
        expires_in: 43200 } });
    } else if (pathname === '/api/merchant/register' && method === 'POST') {
      await route.fulfill({ status: 201, json: { merchant_id: profile.merchant_id,
        name: profile.name, display_name: profile.display_name } });
    } else if (pathname === '/api/merchant/password' && method === 'POST') {
      await route.fulfill({ json: { changed: true, tokens_revoked: true } });
    } else if (pathname === '/api/merchant/garments' && method === 'GET') {
      await route.fulfill({ json: { total: goods.length, items: goods } });
    } else if (pathname === '/api/merchant/garments' && method === 'POST') {
      const created = garment('g-new', { name: '测试细针织衫', status: 'draft' });
      goods.unshift(created);
      await route.fulfill({ status: 201, json: created });
    } else if (/^\/api\/merchant\/garments\/[\w-]+$/.test(pathname) && method === 'DELETE') {
      const id = pathname.split('/').pop();
      const at = goods.findIndex((item) => (item as { id: string }).id === id);
      if (at >= 0) goods.splice(at, 1);
      await route.fulfill({ status: 204, body: '' });
    } else if (/^\/api\/merchant\/garments\/[\w-]+$/.test(pathname) && method === 'PATCH') {
      const id = pathname.split('/').pop();
      const patch = JSON.parse(request.postData() || '{}') as { status?: string };
      const at = goods.findIndex((item) => (item as { id: string }).id === id);
      if (at >= 0) goods[at] = { ...(goods[at] as object), status: patch.status };
      await route.fulfill({ json: goods[at] });
    } else if (pathname === '/api/merchant/looks' && method === 'GET') {
      await route.fulfill({ json: { total: looks.length, items: looks } });
    } else if (pathname === '/api/merchant/looks' && method === 'POST') {
      const draft = JSON.parse(request.postData() || '{}') as { items?: string[] };
      const created = {
        id: 'l1', merchant_id: profile.merchant_id, name: '柔雾通勤', story: '上装塞进裤腰',
        style: '通勤', season: '四季', occasion: '通勤办公', palette: ['#c9d6bd'],
        status: 'published', created: 1,
        items: goods.filter((item) => (draft.items || []).includes((item as { id: string }).id)),
      };
      looks.unshift(created);
      await route.fulfill({ status: 201, json: created });
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
  await page.getByRole('button', { name: '商家后台' }).click();
  return calls;
}

const writes = (calls: Recorded[], path: string) => calls.filter(
  (call) => call.path === path && call.method !== 'GET',
);

test('signing in opens the shop with its goods and their size ranges', async ({ page }) => {
  const calls = await openConsole(page, { signedIn: false });

  await expect(page.getByText('注册商家账号')).toBeVisible();
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByLabel('账号').fill('demo-shop');
  await page.getByLabel('密码').fill('demo-pass-123');
  await page.getByRole('button', { name: '登录', exact: true }).last().click();

  await expect(page.getByText('示例商家')).toBeVisible();
  await expect(page.getByText('@demo-shop · demo@example.com')).toBeVisible();
  await expect(page.getByText('商品 2/200')).toBeVisible();
  const row = page.locator('.merchant-goods > li').first();
  await expect(row).toContainText('细罗纹半高领针织');
  await expect(row).toContainText('适合身高 158–176 · 适合胸围 86–96');
  await expect(row.locator('.merchant-status')).toHaveText('已发布');
  expect(writes(calls, '/api/merchant/login')).toHaveLength(1);
});

test('an out-of-range fit range is refused before any request', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /新建商品/ }).first().click();
  await page.getByLabel('商品名称').fill('测试细针织衫');
  await page.getByLabel('适合胸围下限').fill('86');
  await page.getByLabel('适合胸围上限').fill('999');
  await page.getByRole('button', { name: '保存为草稿' }).click();

  await expect(page.getByText('适合胸围需在 60-160cm 之间')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('还有字段未填对');
  expect(writes(calls, '/api/merchant/garments')).toHaveLength(0);
});

test('a name is required, and an inverted range is caught', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /新建商品/ }).first().click();
  await page.getByRole('button', { name: '保存并发布' }).click();
  await expect(page.getByText('请填写商品名称')).toBeVisible();

  await page.getByLabel('商品名称').fill('测试细针织衫');
  await page.getByLabel('适合腰围下限').fill('80');
  await page.getByLabel('适合腰围上限').fill('70');
  await page.getByRole('button', { name: '保存并发布' }).click();
  await expect(page.getByText('适合腰围的下限不能大于上限')).toBeVisible();
  expect(writes(calls, '/api/merchant/garments')).toHaveLength(0);
});

test('a new garment is uploaded as multipart metrics and listed', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /新建商品/ }).first().click();
  await page.getByLabel('商品名称').fill('测试细针织衫');
  await page.getByLabel('胸围（cm）').fill('100');
  await page.getByLabel('适合胸围下限').fill('86');
  await page.getByLabel('适合胸围上限').fill('96');
  await page.getByLabel('价格（元）').fill('269');
  await page.getByRole('button', { name: '保存为草稿' }).click();

  await expect(page.locator('.merchant-goods > li').first()).toContainText('测试细针织衫');
  const sent = writes(calls, '/api/merchant/garments');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toContain('name="payload"');
  expect(sent[0].body).toContain('"name":"测试细针织衫"');
  expect(sent[0].body).toContain('"bust_cm":[86,96]');
  expect(sent[0].body).toContain('"bust_cm":100');
  expect(sent[0].body).toContain('"price_cents":26900');
  expect(sent[0].body).toContain('"status":"draft"');
});

test('a draft can be published straight from the list', async ({ page }) => {
  const calls = await openConsole(page);
  const draft = page.locator('.merchant-goods > li').nth(1);
  await expect(draft.locator('.merchant-status')).toHaveText('草稿');
  await draft.getByRole('button', { name: '发布' }).click();
  await expect(draft.locator('.merchant-status')).toHaveText('已发布');
  const patched = calls.filter(
    (call) => call.method === 'PATCH' && call.path === '/api/merchant/garments/g2');
  expect(patched).toHaveLength(1);
  expect(patched[0].body).toContain('"status":"published"');
});

test('deleting a garment asks first and then removes it', async ({ page }) => {
  const calls = await openConsole(page);
  page.on('dialog', (dialog) => void dialog.accept());
  const row = page.locator('.merchant-goods > li').first();
  await row.getByRole('button', { name: /删除/ }).click();
  await expect(page.locator('.merchant-goods > li')).toHaveCount(1);
  const deleted = calls.filter(
    (call) => call.method === 'DELETE' && call.path === '/api/merchant/garments/g1');
  expect(deleted).toHaveLength(1);
});

test('a look is composed from published members only', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: '套装', exact: true }).click();
  await page.getByRole('button', { name: /新建套装/ }).first().click();

  // Only the published garment is offered as a member.
  await expect(page.locator('.merchant-picker li')).toHaveCount(1);
  await expect(page.locator('.merchant-picker')).toContainText('细罗纹半高领针织');
  await expect(page.locator('.merchant-picker')).not.toContainText('高腰直筒西裤');

  await page.getByLabel('套装名称').fill('柔雾通勤');
  await page.getByRole('checkbox').first().check();
  await page.getByLabel('搭配故事').fill('上装塞进裤腰，比例更长');
  await page.getByRole('button', { name: '保存并发布' }).click();

  await expect(page.getByText('柔雾通勤')).toBeVisible();
  const sent = writes(calls, '/api/merchant/looks');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toContain('"name":"柔雾通勤"');
  expect(sent[0].body).toContain('"items":["g1"]');
  expect(sent[0].body).toContain('"status":"published"');
});

test('logging out clears the token and shows the sign-in form again', async ({ page }) => {
  await openConsole(page);
  await expect(page.getByText('示例商家')).toBeVisible();
  await page.getByRole('button', { name: /退出登录/ }).click();
  await expect(page.getByText('注册商家账号')).toBeVisible();
  const stored = await page.evaluate((key) => window.localStorage.getItem(key), TOKEN_KEY);
  expect(stored).toBeNull();
});

test('a mismatched confirmation never reaches the API', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /账号设置/ }).click();
  await page.getByLabel('当前密码').fill('demo-pass-123');
  await page.getByLabel('新密码', { exact: true }).fill('new-pass-1234');
  await page.getByLabel('确认新密码').fill('new-pass-9999');
  await page.getByRole('button', { name: '修改密码' }).click();

  await expect(page.locator('.merchant-account .field-error')).toHaveText('两次输入的新密码不一致');
  await expect(page.getByRole('alert')).toContainText('还有字段未填对');
  expect(writes(calls, '/api/merchant/password')).toHaveLength(0);
});

test('a short new password is refused with the same rule as registration', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /账号设置/ }).click();
  await page.getByLabel('当前密码').fill('demo-pass-123');
  await page.getByLabel('新密码', { exact: true }).fill('short');
  await page.getByLabel('确认新密码').fill('short');
  await page.getByRole('button', { name: '修改密码' }).click();

  await expect(page.locator('.merchant-account .field-error')).toHaveText('新密码至少 8 位');
  await expect(page.getByRole('alert')).toContainText('还有字段未填对');
  expect(writes(calls, '/api/merchant/password')).toHaveLength(0);
});

test('changing the password signs the shop out, because the token is revoked', async ({ page }) => {
  const calls = await openConsole(page);
  await page.getByRole('button', { name: /账号设置/ }).click();
  await page.getByLabel('当前密码').fill('demo-pass-123');
  await page.getByLabel('新密码', { exact: true }).fill('new-pass-1234');
  await page.getByLabel('确认新密码').fill('new-pass-1234');
  await page.getByRole('button', { name: '修改密码' }).click();

  await expect(page.getByText('密码已修改，旧登录状态已失效，请用新密码重新登录')).toBeVisible();
  await expect(page.getByText('注册商家账号')).toBeVisible();
  const stored = await page.evaluate((key) => window.localStorage.getItem(key), TOKEN_KEY);
  expect(stored).toBeNull();
  const sent = writes(calls, '/api/merchant/password');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toContain('"current_password":"demo-pass-123"');
  expect(sent[0].body).toContain('"new_password":"new-pass-1234"');
});
