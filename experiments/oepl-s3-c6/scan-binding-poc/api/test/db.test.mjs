import test from 'node:test';
import assert from 'node:assert/strict';
import {createStore} from '../src/db.mjs';
import {normalizeMac,normalizeBarcode,renderShelfSvg} from '../src/domain.mjs';

const tagA='1234567890ABCDEF', tagB='1234567890ABCDE0';
const water={sku:'SKU-01',barcode:'6901234567892',name:'Sample bottled water',price_cents:200};
const tea={sku:'SKU-02',barcode:'6901234567893',name:'Sample tea',price_cents:500};

test('scanner identifiers normalize, reject unknown and bad data',()=>{
  assert.equal(normalizeMac('OEPL:12:34:56:78:90:ab:cd:ef'),tagA);
  assert.equal(normalizeBarcode('00123456'),'00123456');
  assert.throws(()=>normalizeMac('00:00'),/Expected 16/);
  assert.throws(()=>normalizeMac('0000000000000000'),/Expected 16/);
});
test('tag/product binding is idempotent, simulated only, replacement needs consent',()=>{
  const db=createStore();
  try {
    assert.equal(db.registerTag(tagA).verified,0);
    db.upsertProduct(water);
    db.upsertProduct(tea);
    const first=db.bind(tagA,water.barcode);
    assert.equal(first.idempotent,false);
    assert.equal(db.listJobs().length,1);
    assert.equal(db.listJobs()[0].status,'SIMULATED');
    assert.equal(db.bind(tagA,water.barcode).idempotent,true);
    assert.equal(db.listJobs().length,1);
    assert.throws(()=>db.bind(tagA,tea.barcode),/Confirm replacement/);
    assert.equal(db.listBindings()[0].sku,water.sku);
    db.bind(tagA,tea.barcode,true);
    assert.equal(db.listBindings()[0].sku,tea.sku);
    assert.equal(db.listJobs().length,2);
  } finally { db.close(); }
});
test('price edit creates simulated jobs only for bound tag; failed bind rolls back',()=>{
  const db=createStore();
  try {
    db.registerTag(tagA);db.registerTag(tagB);db.upsertProduct(water);
    assert.throws(()=>db.bind(tagA,'01234567'),/Register product first/);
    assert.equal(db.listJobs().length,0);
    db.bind(tagA,water.barcode);
    const after={...water,price_cents:275};
    db.upsertProduct(after);
    assert.equal(db.listJobs().length,2);
    assert.equal(db.listJobs()[0].reason,'PRODUCT_CHANGE');
    assert.equal(db.getBoundProduct(tagA).price_cents,275);
    assert.equal(db.getBoundProduct(tagB),undefined);
  } finally { db.close(); }
});
test('SVG preview escapes user-controlled text and stays 296x128',()=>{
  const svg=renderShelfSvg({...water,name:'<script>alert(1)</script>'});
  assert.match(svg,/width="296" height="128"/);
  assert.doesNotMatch(svg,/<script>/);
  assert.match(svg,/&lt;script&gt;/);
});
