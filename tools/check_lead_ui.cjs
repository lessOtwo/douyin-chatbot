// Focused browser check; intercepts writes so existing local leads are untouched.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
  .split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
const leads = [
  {id:'lead-one',account_id:'12345678912',douyin_nickname:'泰安小帅聊雅思',store_name:'直达灯塔语言培训',
    conversation_id:'conv-one',external_id:'模拟会话一',kind:'phone',value:'13800000000',status:'candidate',created_at:1792660000,exported_at:null},
  {id:'lead-two',account_id:'22345678912',douyin_nickname:'济南装修小张',store_name:'济南家庭装修店',
    conversation_id:'conv-two',external_id:'模拟会话二',kind:'wechat',value:'wx123456',status:'candidate',created_at:1792660001,exported_at:1792660100},
];

(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({acceptDownloads:true});
    page.setDefaultTimeout(15000);
    const errors=[];
    const exports=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.route('**/api/leads?*', async route=>{
      const url=new URL(route.request().url());
      const search=(url.searchParams.get('search')||'').toLowerCase();
      const exported=url.searchParams.get('exported');
      const items=leads.filter(item=>[item.account_id,item.douyin_nickname,item.store_name]
        .some(value=>value.toLowerCase().includes(search)) &&
        (exported==='all'||(exported==='yes')===Boolean(item.exported_at)));
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items,total:items.length})});
    });
    await page.route('**/api/leads/export', async route=>{
      exports.push(route.request().postDataJSON().ids);
      await route.fulfill({status:200,headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition':'attachment; filename=leads.xlsx'},body:'fixture'});
    });
    await page.route('**/api/leads/*/history-link', async route=>{
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        url:'http://localhost:8088/lead-history?token='+'a'.repeat(64),expires_at:1793260000})});
    });
    await page.goto('http://localhost:8088');
    await page.fill('#key', env.BUSINESS_API_KEY);
    await page.locator('#loginForm button').click();
    await page.locator('#login').waitFor({state:'hidden'});
    await page.locator('[data-view="leads"]').click();
    await page.locator('#leadList tbody tr').first().waitFor();
    if (await page.locator('#leadList tbody tr').count() !== 2) throw Error('initial lead list should have two rows');
    if (!(await page.locator('#leadList').textContent()).includes('泰安小帅聊雅思')) throw Error('merchant nickname missing');
    await page.fill('#leadSearch','直达灯塔');
    await page.waitForFunction(()=>document.querySelectorAll('#leadList tbody tr').length===1);
    if (await page.locator('#leadList tbody tr').count() !== 1) throw Error('search did not narrow rows');
    await page.locator('#selectAllLeads').check();
    await Promise.all([page.waitForEvent('download'),page.locator('#exportSelectedLeads').click()]);
    if (JSON.stringify(exports[0]) !== JSON.stringify(['lead-one'])) throw Error('batch export did not use filtered selection');
    await page.fill('#leadSearch','');
    await page.selectOption('#leadExported','yes');
    await page.waitForFunction(()=>document.querySelectorAll('#leadList tbody tr').length===1 &&
      document.querySelector('#leadList tbody tr')?.textContent.includes('济南装修小张'));
    if (await page.locator('#leadList tbody tr').count() !== 1) throw Error('exported filter did not narrow rows');
    await Promise.all([page.waitForEvent('download'),page.locator('[data-lead-export="lead-two"]').click()]);
    if (JSON.stringify(exports[1]) !== JSON.stringify(['lead-two'])) throw Error('single-row export used wrong lead');
    await page.locator('[data-lead-history="lead-two"]').click();
    await page.locator('#leadHistoryDialog').waitFor({state:'visible'});
    if (!(await page.inputValue('#leadHistoryUrl')).includes('/lead-history?token=')) throw Error('history link not shown');
    if (errors.length) throw Error('browser errors: '+errors.join('; '));
    console.log(JSON.stringify({result:'passed',checks:['merchant column','search','exported filter',
      'filtered select-all export','single-row export','history-link dialog'],writesIntercepted:true}));
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error.message);process.exit(1)});
