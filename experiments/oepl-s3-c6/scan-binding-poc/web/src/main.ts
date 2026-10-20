import {bootstrapApplication} from '@angular/platform-browser';
import {Component, ElementRef, OnDestroy, OnInit, ViewChild} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {BrowserMultiFormatReader, IScannerControls} from '@zxing/browser';

type Product = {sku: string; barcode: string; name: string; price_cents: number};
type Binding = {mac: string; sku: string; barcode: string; name: string; price_cents: number; verified: number};
type Job = {id: string; mac: string; sku: string; status: string; reason: string; created_at: string};
type ApiError = Error & {code?: string};

@Component({
  selector: 'app-scan-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html'
})
export class ScanApp implements OnInit, OnDestroy {
  @ViewChild('video') video?: ElementRef<HTMLVideoElement>;
  tagInput = '';
  barcodeInput = '';
  productSku = '';
  productName = '';
  priceInput = '';
  selectedProduct?: Product;
  lastPreviewMac = '';
  bindings: Binding[] = [];
  jobs: Job[] = [];
  busy = false;
  message = '模拟模式：所有价格下发任务只保存在本地，不发送到 AP。';
  messageType: 'info'|'error'|'success' = 'info';
  scanning = false;
  scanTarget: 'tag'|'product' = 'tag';
  private reader = new BrowserMultiFormatReader();
  private controls?: IScannerControls;

  ngOnInit(): void { void this.refresh(); }
  ngOnDestroy(): void { this.stopScan(); }
  private notify(msg: string, kind: 'info'|'error'|'success' = 'info'): void {
    this.message = msg; this.messageType = kind;
  }
  private async api(path: string, options?: RequestInit): Promise<any> {
    const response = await fetch('/api' + path, {
      ...options,
      headers: {'Content-Type': 'application/json', ...(options?.headers ?? {})},
      cache: 'no-store'
    });
    const json = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(json.message || json.error || 'API 请求失败') as ApiError;
      err.code = json.error;
      throw err;
    }
    return json;
  }
  private error(err: unknown): void { this.notify(err instanceof Error ? err.message : '操作失败', 'error'); }
  async refresh(): Promise<void> {
    try {
      const [bindings, jobs] = await Promise.all([this.api('/bindings'), this.api('/jobs')]);
      this.bindings = bindings.bindings;
      this.jobs = jobs.jobs;
    } catch (err) { this.error(err); }
  }
  async registerTag(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      const result = await this.api('/tags', {method:'POST', body:JSON.stringify({code:this.tagInput})});
      this.tagInput = result.tag.mac;
      this.notify('价签已登记（尚未验证在线）。下一步扫描商品条码。', 'success');
      await this.refresh();
    } catch (err) { this.error(err); }
    finally { this.busy = false; }
  }
  async lookupProduct(): Promise<void> {
    if (!this.barcodeInput) return;
    try {
      const result = await this.api('/products/by-barcode/' + encodeURIComponent(this.barcodeInput));
      this.selectedProduct = result.product;
      this.productSku = result.product.sku;
      this.productName = result.product.name;
      this.priceInput = (result.product.price_cents / 100).toFixed(2);
      this.notify('已找到商品，可确认绑定。', 'success');
    } catch (err) {
      this.selectedProduct = undefined;
      if ((err as ApiError)?.code === 'product_unknown') {
        this.productName=''; this.productSku=''; this.priceInput='';
        this.notify('商品不存在，请录入 SKU、名称和价格。');
      } else this.error(err);
    }
  }
  async saveProduct(): Promise<void> {
    const p = this.priceInput.trim();
    if (!/^\d{1,6}(\.\d{1,2})?$/.test(p)) {
      this.notify('价格请输入最多两位小数的非负金额。', 'error'); return;
    }
    const [yuan,fen=''] = p.split('.');
    const price_cents = Number(yuan)*100 + Number(fen.padEnd(2,'0'));
    try {
      const result = await this.api('/products', {
        method:'POST', body:JSON.stringify({
          sku:this.productSku, barcode:this.barcodeInput,
          name:this.productName, price_cents
        })
      });
      this.selectedProduct=result.product;
      this.notify('商品已保存。若改价，系统仅生成模拟任务。', 'success');
      await this.refresh();
    } catch (err) { this.error(err); }
  }
  async bind(replace = false): Promise<void> {
    if (!this.tagInput || !this.selectedProduct) { this.notify('请先登记价签并识别商品。','error'); return; }
    if (this.busy) return;
    this.busy=true;
    try {
      const result=await this.api('/bindings',{
        method:'POST',body:JSON.stringify({mac:this.tagInput,barcode:this.selectedProduct.barcode,replace})
      });
      this.lastPreviewMac = result.binding.mac;
      this.notify(result.idempotent ? '已经绑定，无重复下发任务。' : '绑定已保存，仅创建 SIMULATED 任务，未发送至 AP。', 'success');
      await this.refresh();
    } catch (err) {
      if ((err as ApiError)?.code === 'tag_already_bound' && !replace) {
        if (window.confirm('此价签已绑定其他商品。确定换绑并创建模拟任务？')) {
          this.busy=false; await this.bind(true); return;
        }
      } else this.error(err);
    } finally { this.busy=false; }
  }
  async startScan(target:'tag'|'product'): Promise<void> {
    this.stopScan();
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.notify('手机摄像头扫码需要 HTTPS 安全地址。当前可手工输入或用 USB 扫码枪。','error');
      return;
    }
    this.scanTarget=target; this.scanning=true;
    await new Promise(resolve=>setTimeout(resolve,0));
    try {
      if (!this.video) throw new Error('摄像头视图尚未加载');
      this.controls=await this.reader.decodeFromVideoDevice(undefined,this.video.nativeElement,(result)=>{
        if (!result) return;
        const code=result.getText().trim();
        this.stopScan();
        if (target==='tag') { this.tagInput=code; void this.registerTag(); }
        else { this.barcodeInput=code; void this.lookupProduct(); }
      });
      if (!this.scanning) this.controls.stop();
    } catch(err) { this.stopScan(); this.error(err); }
  }
  stopScan(): void {
    this.controls?.stop();
    this.controls=undefined;
    this.scanning=false;
  }
  get previewUrl(): string { return '/api/preview/' + encodeURIComponent(this.lastPreviewMac) + '.svg'; }
}

bootstrapApplication(ScanApp).catch(error => console.error(error));
