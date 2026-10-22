// Targeted UI check against an isolated backend populated by check_business_flow.py.
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/).filter(s=>s&&!s.startsWith('#')&&s.includes('=')).map(s=>[s.slice(0,s.indexOf('=')),s.slice(s.indexOf('=')+1)]));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.ADMIN_URL||'http://localhost:18188');
  await page.fill('#key',env.BUSINESS_API_KEY);await page.locator('#loginForm button').click();await page.locator('#login').waitFor({state:'hidden'});
  await page.locator('[data-view="unanswered"]').click();
  await page.locator('#questionList').getByText('缺少当前商家的材料品牌资料',{exact:true}).waitFor();
  if(await page.locator('#questionList img').count())throw Error('customer message rendered as HTML');
  const row=page.locator('#questionList tr').filter({hasText:'unanswered'});
  await row.locator('[data-question-open]').click();
  await page.locator('#questionDetail').getByText(/unanswered <img/).waitFor();
  await page.screenshot({path:path.join(root,'artifacts/unanswered-preview.png'),fullPage:true});
  const id=await row.locator('[data-question-state]').getAttribute('data-question-state');
  await row.locator('[data-question-state]').click();
  await page.locator(`[data-question-state="${id}"]`).waitFor({state:'hidden'});
  await page.locator('#questionStatus').selectOption('resolved');
  const resolved=page.locator(`#questionList [data-question-state="${id}"]`);await resolved.waitFor();
  await resolved.click();await resolved.waitFor({state:'hidden'});
  await page.locator('#questionStatus').selectOption('open');
  await page.locator(`#questionList [data-question-state="${id}"]`).waitFor();
  await page.setViewportSize({width:390,height:844});
  if(!await page.locator('#questionList').evaluate(el=>el.scrollWidth>el.clientWidth))throw Error('mobile question table must scroll instead of squeezing columns');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile page overflows viewport');
  await page.screenshot({path:path.join(root,'artifacts/unanswered-mobile-preview.png'),fullPage:true});
  if(errors.length)throw Error('browser script errors: '+errors.join(';'));
  const result={result:'passed',scope:['question sidebar','original customer text escaped','detail view','resolve and reopen','status filter','desktop and mobile layouts'],full_tests_run:false};
  fs.writeFileSync(path.join(root,'artifacts/unanswered-ui-checks.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exit(1)});
