import express from 'express';
import {mkdirSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {createStore} from './db.mjs';
import {AppError, normalizeBarcode, renderShelfSvg} from './domain.mjs';

export function createApp(store) {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({limit: '32kb'}));
  const wrap = fn => (req,res,next) => { try { Promise.resolve(fn(req,res)).catch(next); } catch(e) { next(e); } };
  app.get('/api/health', (_req,res) => res.json({ok:true,mode:'SIMULATED',ap_writes_enabled:false}));
  app.get('/api/tags', (_req,res) => res.json({tags:store.listTags()}));
  app.post('/api/tags', wrap((req,res) => res.status(201).json({tag:store.registerTag(req.body?.code)})));
  app.get('/api/products', (_req,res) => res.json({products:store.listProducts()}));
  app.get('/api/products/by-barcode/:barcode', wrap((req,res) => {
    const p=store.getProductByBarcode(req.params.barcode);
    if (!p) throw new AppError(404,'product_unknown','Product not found');
    res.json({product:p});
  }));
  app.post('/api/products', wrap((req,res) => res.json({product:store.upsertProduct(req.body)})));
  app.get('/api/bindings', (_req,res) => res.json({bindings:store.listBindings()}));
  app.post('/api/bindings', wrap((req,res) => {
    res.json(store.bind(req.body?.mac,req.body?.barcode,req.body?.replace ?? false));
  }));
  app.get('/api/jobs', (_req,res) => res.json({jobs:store.listJobs(),mode:'SIMULATED'}));
  app.get('/api/preview/:mac.svg', wrap((req,res) => {
    const p=store.getBoundProduct(req.params.mac);
    if (!p) throw new AppError(404,'not_bound','Tag is not bound');
    res.set('Cache-Control','no-store').type('image/svg+xml').send(renderShelfSvg(p));
  }));
  app.use((error,req,res,_next) => {
    if (error instanceof AppError) return res.status(error.status).json({error:error.code,message:error.message});
    if (error?.code === 'SQLITE_CONSTRAINT_UNIQUE') return res.status(409).json({error:'product_conflict'});
    if (error?.type === 'entity.parse.failed') return res.status(400).json({error:'invalid_json'});
    console.error('API error', error?.message ?? 'unknown');
    return res.status(500).json({error:'internal_error'});
  });
  return app;
}

if (process.argv[1] && import.meta.url === new URL('file://' + resolve(process.argv[1])).href) {
  const host=process.env.HOST || '127.0.0.1';
  if (!['127.0.0.1','::1','localhost'].includes(host)) {
    console.error('Refusing non-loopback bind: use an authenticated HTTPS reverse proxy on the same host.');
    process.exit(1);
  }
  const filename=resolve(process.env.DB_FILE || './data/scan-binding.sqlite');
  mkdirSync(dirname(filename),{recursive:true,mode:0o700});
  const store=createStore(filename);
  const port=Number(process.env.PORT || 3788);
  createApp(store).listen(port,host,()=>console.log('Scan bind simulation API listening on '+host+':'+port));
}
