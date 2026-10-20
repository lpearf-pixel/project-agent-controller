import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import {AppError, normalizeMac, normalizeBarcode, validateProduct} from './domain.mjs';

export function createStore(filename = ':memory:') {
  const db = new DatabaseSync(filename);
  db.exec('PRAGMA foreign_keys=ON');
  db.exec('PRAGMA busy_timeout=3000');
  if (filename !== ':memory:') db.exec('PRAGMA journal_mode=WAL');
  db.exec([
    'CREATE TABLE IF NOT EXISTS tags (mac TEXT PRIMARY KEY, verified INTEGER NOT NULL DEFAULT 0, registered_at TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, barcode TEXT NOT NULL UNIQUE, name TEXT NOT NULL, price_cents INTEGER NOT NULL, updated_at TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS bindings (mac TEXT PRIMARY KEY REFERENCES tags(mac), sku TEXT NOT NULL REFERENCES products(sku), bound_at TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, mac TEXT NOT NULL REFERENCES tags(mac), sku TEXT NOT NULL REFERENCES products(sku), reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status = \'SIMULATED\'), created_at TEXT NOT NULL)'
  ].join(';') + ';');

  const stamp = () => new Date().toISOString();
  const tx = (fn) => {
    db.exec('BEGIN IMMEDIATE');
    try { const value = fn(); db.exec('COMMIT'); return value; }
    catch (error) { db.exec('ROLLBACK'); throw error; }
  };
  const insertJob = (mac, sku, reason) => {
    db.prepare('INSERT INTO jobs (id,mac,sku,reason,status,created_at) VALUES (?,?,?,?,?,?)')
      .run(randomUUID(), mac, sku, reason, 'SIMULATED', stamp());
  };
  const getProductByBarcode = barcode => db.prepare('SELECT * FROM products WHERE barcode=?').get(normalizeBarcode(barcode));
  const getProductBySku = sku => db.prepare('SELECT * FROM products WHERE sku=?').get(sku);
  const listBindings = () => db.prepare(
    'SELECT b.mac,b.sku,b.bound_at,p.barcode,p.name,p.price_cents,t.verified FROM bindings b ' +
    'JOIN products p ON p.sku=b.sku JOIN tags t ON t.mac=b.mac ORDER BY b.bound_at DESC'
  ).all();

  return {
    registerTag(raw) {
      const mac = normalizeMac(raw);
      db.prepare('INSERT OR IGNORE INTO tags (mac,verified,registered_at) VALUES (?,0,?)').run(mac, stamp());
      return db.prepare('SELECT * FROM tags WHERE mac=?').get(mac);
    },
    listTags() { return db.prepare('SELECT * FROM tags ORDER BY registered_at DESC').all(); },
    upsertProduct(raw) {
      const p = validateProduct(raw);
      return tx(() => {
        const old = getProductBySku(p.sku);
        const barcodeOwner = db.prepare('SELECT sku FROM products WHERE barcode=?').get(p.barcode);
        if (barcodeOwner && barcodeOwner.sku !== p.sku) throw new AppError(409,'barcode_in_use','Barcode already belongs to a different SKU');
        db.prepare('INSERT INTO products(sku,barcode,name,price_cents,updated_at) VALUES(?,?,?,?,?) ' +
          'ON CONFLICT(sku) DO UPDATE SET barcode=excluded.barcode,name=excluded.name,price_cents=excluded.price_cents,updated_at=excluded.updated_at')
          .run(p.sku, p.barcode, p.name, p.price_cents, stamp());
        if (old && (old.price_cents !== p.price_cents || old.name !== p.name)) {
          for (const b of db.prepare('SELECT mac FROM bindings WHERE sku=?').all(p.sku)) insertJob(b.mac, p.sku, 'PRODUCT_CHANGE');
        }
        return getProductBySku(p.sku);
      });
    },
    getProductByBarcode,
    listProducts() { return db.prepare('SELECT * FROM products ORDER BY sku LIMIT 5000').all(); },
    bind(rawMac, rawBarcode, replace = false) {
      const mac = normalizeMac(rawMac);
      const barcode = normalizeBarcode(rawBarcode);
      if (typeof replace !== 'boolean') throw new AppError(400, 'invalid_replace', 'replace must be boolean');
      return tx(() => {
        const tag = db.prepare('SELECT mac FROM tags WHERE mac=?').get(mac);
        if (!tag) throw new AppError(404, 'tag_unknown', 'Register tag first');
        const product = getProductByBarcode(barcode);
        if (!product) throw new AppError(404, 'product_unknown', 'Register product first');
        const old = db.prepare('SELECT sku FROM bindings WHERE mac=?').get(mac);
        if (old && old.sku === product.sku) return {binding: listBindings().find(x=>x.mac===mac), idempotent: true};
        if (old && !replace) throw new AppError(409, 'tag_already_bound', 'Confirm replacement explicitly');
        db.prepare('INSERT INTO bindings(mac,sku,bound_at) VALUES (?,?,?) ON CONFLICT(mac) DO UPDATE SET sku=excluded.sku,bound_at=excluded.bound_at')
          .run(mac, product.sku, stamp());
        insertJob(mac, product.sku, 'BIND');
        return {binding: listBindings().find(x=>x.mac===mac), idempotent: false};
      });
    },
    listBindings,
    getBoundProduct(rawMac) {
      const mac = normalizeMac(rawMac);
      return db.prepare('SELECT p.* FROM bindings b JOIN products p ON p.sku=b.sku WHERE b.mac=?').get(mac);
    },
    listJobs() { return db.prepare('SELECT * FROM jobs ORDER BY rowid DESC LIMIT 200').all(); },
    close() { db.close(); }
  };
}
