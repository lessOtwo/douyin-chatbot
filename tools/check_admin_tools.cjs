// Targeted checks against our sidebar and real upstream login sessions.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const settings=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/).filter(s=>s&&!s.startsWith('#')&&s.includes('=')).map(s=>[s.slice(0,s.indexOf('=')),s.slice(s.indexOf('=')+1)]));
const base=process.env.ADMIN_URL||'http://localhost:8088';
const assert=(value,label)=>{if(!value)throw Error(label)};
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1050}});
    const anonymous=await context.request.post(base+'/admin-tools/fastgpt');
    assert(anonymous.status()===401,'anonymous tool entry must require business login');
    const page=await context.newPage();
    await page.goto(base);
    await page.fill('#key',settings.BUSINESS_API_KEY);
    await page.locator('#loginForm button').click();
    await page.locator('#login').waitFor({state:'hidden'});
    const positions=await page.evaluate(()=>['[data-view="accounts"]','#openFastGPT'].map(selector=>document.querySelector(selector).getBoundingClientRect().y));
    assert(positions[0]<positions[1],'tool entries must sit under existing sidebar tabs');
    assert(!(await page.content()).includes(settings.FASTGPT_ROOT_PASSWORD),'upstream password must not be present in HTML');
    await page.screenshot({path:path.join(root,'artifacts/admin-tools-preview.png'),fullPage:true});
    const csrf=await context.request.post(base+'/admin-tools/fastgpt',{headers:{Origin:'https://wrong.invalid'},maxRedirects:0});
    assert(csrf.status()===403,'cross-origin sidebar POST must be rejected');
    const fastPageEvent=context.waitForEvent('page');
    await page.locator('#openFastGPT').click();
    const fastPage=await fastPageEvent;
    await fastPage.waitForURL(/\/dashboard\/agent/,{timeout:60000});
    const fastSession=await context.request.get(new URL('/api/support/user/account/tokenLogin',fastPage.url()).href,{headers:{'fastgpt-web-request':'1'}});
    const fastBody=await fastSession.json();
    if(!(fastBody.code===200&&fastBody.data.username==='root'))console.error(JSON.stringify({code:fastBody.code,message:fastBody.message,dataKeys:Object.keys(fastBody.data||{}),username:fastBody.data?.username,userName:fastBody.data?.user?.username,url:new URL(fastPage.url()).pathname}));
    assert(fastBody.code===200&&fastBody.data.username==='root','real FastGPT cookie session must belong to root');
    assert(await page.locator('#openChatwoot').count()===0,'removed Chatwoot sidebar entry');
    // Exercise the cross-host Caddy handoff contract without deploying domains.
    const handoff=await context.request.post(base+'/admin-tools/fastgpt',{headers:{Host:'admin.example.test',Origin:'http://admin.example.test'},maxRedirects:0});
    assert(handoff.status()===200,'cross-host entry must issue a handoff form');
    const match=(await handoff.text()).match(/name=ticket value="([a-f0-9-]+)"/);
    assert(match,'handoff form must contain an opaque ticket');
    const bridgeHeaders={Host:new URL(settings.FASTGPT_PUBLIC_URL).host};
    const accepted=await context.request.post(base+'/admin-entry/fastgpt',{headers:bridgeHeaders,form:{ticket:match[1]},maxRedirects:0});
    assert(accepted.status()===303,'valid cross-host handoff must establish upstream session');
    const replay=await context.request.post(base+'/admin-entry/fastgpt',{headers:bridgeHeaders,form:{ticket:match[1]},maxRedirects:0});
    assert(replay.status()===401,'handoff ticket must not be replayable');
    const cookies=await context.cookies();
    assert(cookies.some(c=>c.name==='fastgpt_token'&&c.httpOnly),'FastGPT login cookie must be HttpOnly');
    const record={result:'passed',scope:['sidebar placement','authenticated entry','same-origin restriction','real FastGPT auto-login','cross-host handoff contract','single-use ticket'],base,production_deployed:false,full_tests_run:false};
    fs.writeFileSync(path.join(root,'artifacts/admin-tools-checks.json'),JSON.stringify(record,null,2)+'\n','utf8');
    console.log(JSON.stringify(record));
  }finally{await browser.close()}
})().catch(error=>{console.error(error.message.replace(/([?&](?:token|ticket)=)[^&\s]+/g,'$1[REDACTED]'));process.exit(1)});
