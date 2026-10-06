import type { Page } from '@playwright/test';

/** A 256×256 PNG drawn in the page, so uploads carry a real, decodable image. */
export async function imageFromCanvas(page: Page): Promise<Buffer> {
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

/** A minimal, valid GLB: one triangle, so the viewer has something real to parse. */
export function triangleGlb(): Buffer {
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
