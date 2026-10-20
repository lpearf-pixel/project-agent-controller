import test from 'node:test';
import assert from 'node:assert/strict';
import {createStore} from '../src/db.mjs';
import {createApp} from '../src/server.mjs';

test('HTTP flow: register, catalog, bind, idempotency, SVG preview, simulated job',async()=>{
  const store=createStore();
  const server=createApp(store).listen(0,'127.0.0.1');
  await new Promise(resolve=>server.once('listening',resolve));
  const root='http://127.0.0.1:'+server.address().port;
  const call=async(path,method='GET',body)=> {
    const res=await fetch(root+'/api'+path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    return {status:res.status,body:res.headers.get('content-type')?.includes('json')?await res.json():await res.text()};
  };
  try {
    const health=await call('/health');
    assert.equal(health.body.ap_writes_enabled,false);
    const tag=await call('/tags','POST',{code:'1234567890ABCDEF'});
    assert.equal(tag.status,201);
    assert.equal(tag.body.tag.verified,0);
    const notfound=await call('/products/by-barcode/6901234567892');
    assert.equal(notfound.status,404);
    const product=await call('/products','POST',{sku:'SKU-01',barcode:'6901234567892',name:'Sample water',price_cents:200});
    assert.equal(product.status,200);
    const bind=await call('/bindings','POST',{mac:'1234567890ABCDEF',barcode:'6901234567892'});
    assert.equal(bind.body.idempotent,false);
    const again=await call('/bindings','POST',{mac:'1234567890ABCDEF',barcode:'6901234567892'});
    assert.equal(again.body.idempotent,true);
    const jobs=await call('/jobs');
    assert.equal(jobs.body.jobs.length,1);
    assert.equal(jobs.body.jobs[0].status,'SIMULATED');
    const preview=await fetch(root+'/api/preview/1234567890ABCDEF.svg');
    assert.equal(preview.status,200);
    assert.match(await preview.text(),/width="296" height="128"/);
    assert.equal((await call('/tags','POST',{code:'invalid'})).status,400);
  } finally {
    await new Promise((resolve,reject)=>server.close(err=>err?reject(err):resolve()));
    store.close();
  }
});
