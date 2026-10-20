export class AppError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function normalizeMac(value) {
  if (typeof value !== 'string') throw new AppError(400, 'invalid_mac', 'Tag code must be a string');
  const mac = value.trim().replace(/^OEPL\s*:/i, '').replace(/[:\s-]/g, '').toUpperCase();
  if (!/^[0-9A-F]{16}$/.test(mac) || /^0{16}$/.test(mac)) {
    throw new AppError(400, 'invalid_mac', 'Expected 16 hexadecimal MAC characters');
  }
  return mac;
}

export function normalizeBarcode(value) {
  if (typeof value !== 'string') throw new AppError(400, 'invalid_barcode', 'Barcode must be a string');
  const code = value.trim();
  if (!/^[a-zA-Z0-9._-]{4,48}$/.test(code)) {
    throw new AppError(400, 'invalid_barcode', 'Barcode must have 4-48 safe characters');
  }
  return code;
}

export function normalizeSku(value) {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9._-]{1,48}$/.test(value.trim())) {
    throw new AppError(400, 'invalid_sku', 'SKU must have 1-48 safe characters');
  }
  return value.trim();
}

export function validateProduct(p) {
  const sku = normalizeSku(p?.sku);
  const barcode = normalizeBarcode(p?.barcode);
  const name = typeof p?.name === 'string' ? p.name.trim() : '';
  if (!name || name.length > 100) throw new AppError(400, 'invalid_name', 'Name must have 1-100 characters');
  if (!Number.isSafeInteger(p?.price_cents) || p.price_cents < 0 || p.price_cents > 99999999) {
    throw new AppError(400, 'invalid_price', 'Price must be nonnegative integer cents');
  }
  return {sku, barcode, name, price_cents: p.price_cents};
}

export function escapeXml(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

export function renderShelfSvg(product) {
  const title = escapeXml(product.name.slice(0, 27));
  const price = (product.price_cents / 100).toFixed(2);
  const sku = escapeXml(product.sku);
  return '<svg xmlns="http://www.w3.org/2000/svg" width="296" height="128" viewBox="0 0 296 128">' +
    '<rect width="296" height="128" fill="white"/>' +
    '<rect x="1" y="1" width="294" height="126" fill="none" stroke="black" stroke-width="2"/>' +
    '<text x="12" y="26" font-family="sans-serif" font-size="17" fill="black">' + title + '</text>' +
    '<text x="12" y="83" font-family="sans-serif" font-weight="bold" font-size="42" fill="black">¥' + price + '</text>' +
    '<text x="12" y="115" font-family="sans-serif" font-size="12" fill="black">SKU: ' + sku + '</text>' +
    '</svg>';
}
