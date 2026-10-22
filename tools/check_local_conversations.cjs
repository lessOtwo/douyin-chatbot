// Browser check against the isolated backend populated by check_business_flow.py.
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/).filter(s=>s&&!s.startsWith('#')&&s.includes('=')).map(s=>[s.slice(0,s.indexOf('=')),s.slice(s.indexOf('=')+1)]));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://localhost:18188');
  await page.fill('#key',env.BUSINESS_API_KEY);
  await page.locator('#loginForm button').click();
  await page.locator('#login').waitFor({state:'hidden'});
  await page.locator('[data-view="conversations"]').click();
  const row=page.locator('#conversationList tr').filter({hasText:'customer-dialogue'});
  await row.getByText('机器人已暂停').waitFor();
  await row.locator('[data-open]').click();
  await page.locator('#conversationDetail .bubble:not(.reply)').filter({hasText:'老房翻新 13800000000'}).waitFor();
  await page.locator('#conversationDetail .bubble:not(.reply)').filter({hasText:'paused question'}).waitFor();
  await page.locator('#conversationDetail .bubble.reply').filter({hasText:'fixture reply: 新问题'}).waitFor();
  page.once('dialog',dialog=>dialog.accept());
  await row.locator('[data-control]').click();
  await row.locator('[data-mode="paused"]').waitFor();
  await row.locator('[data-control]').click();
  await row.locator('[data-mode="bot"]').waitFor();
  await row.locator('[data-open]').click();
  await page.screenshot({path:path.join(root,'artifacts/local-conversations-preview.png'),fullPage:true});
  if(errors.length)throw Error('browser script errors: '+errors.join(';'));
  const record={result:'passed',scope:['local customer and bot messages','held message visible','pause and resume in business admin','no Chatwoot page'],full_tests_run:false};
  fs.writeFileSync(path.join(root,'artifacts/local-conversations-ui-checks.json'),JSON.stringify(record,null,2)+'\n');
  console.log(JSON.stringify(record));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exit(1)});
