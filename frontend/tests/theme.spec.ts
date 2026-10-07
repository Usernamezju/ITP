import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const account = { id: 'a'.repeat(32), name: 'alice', display_name: '爱丽丝', contact: '',
  role: 'customer', created: 1 };

const garment = {
  id: 'g1', merchant_id: 'a'.repeat(32), status: 'published', created: 1, updated: 1,
  metrics: { category: '上装', name: '细罗纹半高领针织', sku: null, brand: null, price_cents: 26900,
    purchase_url: 'https://shop.example/item', measurements: {}, fit_ranges: {}, attributes: {},
    style: '通勤', season: '四季', occasion: '通勤办公', description: '', tips: null, status: 'published' },
  images: [], clicks: { today: 3, month: 9, total: 21 },
};

/** The operator console renders every value it is given; these are its shapes. */
function adminBody(path: string): object {
  if (path === '/api/admin/status') {
    return { version: '0.1.0', services: { geometry: false, pose: false, segmentation: false,
      outfit_images: '', faceverse: { configured: false, reachable: false, model: '', status: null,
        cuda: null, gpu: null }, tryon: {}, flux_klein: { ready: false, model: '' },
      flux_klein_9b: { ready: false, model: '' } }, payments: [] };
  }
  if (path === '/api/admin/usage') {
    return { model_charges: {}, wallets: { count: 0, total_balance_cents: 0 }, recent_ledger: [],
      garments: { total: 0 }, looks: { total: 0 }, orders: {} };
  }
  if (path === '/api/admin/settings' || path === '/api/admin/product-ai') {
    return { settings: {} };
  }
  if (path === '/api/admin/payments') {
    return { settings: { alipay: {}, wechat: { platform_key_ids: [] } },
      status: { notify_origin: '', channels: [], callbacks: {} } };
  }
  if (path === '/api/admin/jobs') {
    return { jobs: [], tryons: [], face_refinements: [], note: '' };
  }
  return { total: 0, items: [] };
}

/** Every surface the two modes must repaint, with the API reduced to mocks. */
async function open(page: Page, path: string, theme: 'day' | 'night', role = 'customer') {
  await page.addInitScript(([value]) => {
    localStorage.setItem('itp.merchant.token', 'theme-token');
    localStorage.setItem('itp-color-theme', value);
  }, [theme] as const);
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    const path_ = url.pathname;
    const ok = (body: unknown) => route.fulfill({ json: body as object });
    if (path_ === '/api/capabilities') return ok({ geometry: false, pose: false, segmentation: false,
      tryon: false, tryon_model: '', tryon_providers: {}, faceverse: false, outfit_images: false,
      image_provider: '', provider: '', pose_provider: '', model: '3.1', pose_model: '' });
    if (path_ === '/api/jobs') return ok([]);
    if (path_ === '/api/account/me') return ok({ ...account, role });
    if (path_ === '/api/pricing') return ok({ currency: 'CNY', model_price_cents: 2345, plans: [] });
    if (path_ === '/api/garment-options') return ok({ categories: [], styles: [], seasons: [],
      silhouettes: [], stretches: [], length_types: [], statuses: [], measurements: [], fit_ranges: [],
      body_profile: [], limits: {} });
    if (path_ === '/api/merchant/me') return ok({ merchant_id: account.id, name: 'demo-shop',
      display_name: '示例商家', contact: '', created: 1, quota: 5, garment_count: 1 });
    if (path_ === '/api/merchant/garments') return ok({ total: 1, items: [garment] });
    if (path_ === '/api/merchant/looks') return ok({ total: 0, items: [] });
    if (path_ === '/api/merchant/analytics') return ok({ summary: { today: 3, month: 9, total: 21 },
      rank: url.searchParams.get('rank'), timezone: 'Asia/Shanghai', total: 1, items: [garment], trend: [] });
    if (path_.startsWith('/api/admin/')) return ok(adminBody(path_));
    if (path_.startsWith('/api/')) return route.fulfill({ status: 404, json: { detail: '未实现' } });
    if (path_.startsWith('/assets/')) {
      const name = path_.slice('/assets/'.length);
      if (!/^[\w.-]+$/.test(name)) return route.fulfill({ status: 404 });
      return route.fulfill({ body: await readFile(`${dist}/assets/${name}`),
        contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript' });
    }
    return route.fulfill({ body: await readFile(`${dist}/index.html`), contentType: 'text/html' });
  });
  await page.goto(path);
  await page.waitForTimeout(150);
}

/** One computed colour, after the element the page renders is really there. */
async function style(page: Page, selector: string, property: string): Promise<string> {
  const target = page.locator(selector).first();
  await expect(target).toBeVisible();
  return target.evaluate((element, prop) => getComputedStyle(element).getPropertyValue(prop), property);
}

const WHITE = 'rgb(255, 255, 255)';
const BLACK = 'rgb(0, 0, 0)';
const RED_DAY = 'rgb(200, 40, 28)';   // #C8281C
const RED_NIGHT = 'rgb(255, 83, 70)'; // #FF5346

test('day mode is pure white with black text and red actions', async ({ page }) => {
  await open(page, '/', 'day');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'day');
  expect(await style(page, 'body', 'background-color')).toBe(WHITE);
  expect(await style(page, 'body', 'color')).toBe(BLACK);
  expect(await style(page, '.rail', 'background-color')).toBe(WHITE);
  // An action button is filled red, and the page title stays black.
  expect(await style(page, '.generate-button', 'background-color')).toBe(RED_DAY);
  expect(await style(page, '.generate-button', 'color')).toBe(WHITE);
  expect(await style(page, '.page-title h1', 'color')).toBe(BLACK);
});

test('night mode is pure black with white text and outlined red actions', async ({ page }) => {
  await open(page, '/', 'night');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
  expect(await style(page, 'body', 'background-color')).toBe(BLACK);
  expect(await style(page, 'body', 'color')).toBe(WHITE);
  expect(await style(page, '.rail', 'background-color')).toBe(BLACK);
  // Red text, red border, near-black fill.
  expect(await style(page, '.generate-button', 'color')).toBe(RED_NIGHT);
  expect(await style(page, '.generate-button', 'border-top-color')).toBe(RED_NIGHT);
  expect(await style(page, '.generate-button', 'background-color')).toBe('rgb(11, 11, 11)');
  expect(await style(page, '.page-title h1', 'color')).toBe(WHITE);
});

test('a phone drawer, cards and inputs stay readable at night', async ({ page }) => {
  await open(page, '/', 'night');
  await page.getByRole('button', { name: '意见反馈' }).click();
  expect(await style(page, '.feedback-dialog', 'background-color')).toBe('rgb(11, 11, 11)');
  expect(await style(page, '.feedback-dialog', 'color')).toBe(WHITE);
  // An input keeps a visible border instead of a white field on a white card.
  expect(await style(page, '.feedback-dialog textarea', 'background-color')).toBe('rgb(11, 11, 11)');
  expect(await style(page, '.feedback-dialog textarea', 'color')).toBe(WHITE);
  await page.getByRole('button', { name: '关闭反馈' }).click();
  // The 3D viewport keeps its own dark canvas in both modes.
  expect(await style(page, '.viewport', 'background-color')).toBe('rgb(26, 26, 26)');
});

test('the shop console and the operator console follow the same switch', async ({ page }) => {
  await open(page, '/merchant', 'night', 'merchant');
  expect(await style(page, '.merchant-bar', 'background-color')).toBe('rgb(11, 11, 11)');
  expect(await style(page, '.merchant-kpi', 'color')).toBe(WHITE);
  expect(await style(page, '.merchant-good-text strong', 'color')).toBe(WHITE);

  await open(page, '/admin', 'night', 'admin');
  expect(await style(page, '.admin-shell', 'background-color')).toBe(BLACK);
  expect(await style(page, '.admin-topbar', 'background-color')).toBe(BLACK);
  expect(await style(page, '.admin-section', 'background-color')).toBe('rgb(11, 11, 11)');
  expect(await style(page, '.admin-section-title', 'color')).toBe(WHITE);

  // The same shop console in the day mode is white with black text.
  await open(page, '/merchant', 'day', 'merchant');
  expect(await style(page, '.merchant-bar', 'background-color')).toBe(WHITE);
  expect(await style(page, '.merchant-kpi', 'color')).toBe(BLACK);
});
