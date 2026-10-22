// Own-app browser check; no Codex UI automation and no external transmissions.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname,'..');
const settings = Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/).filter(s=>s&&!s.startsWith('#')&&s.includes('=')).map(s=>[s.slice(0,s.indexOf('=')),s.slice(s.indexOf('=')+1)]));
(async()=>{
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.ADMIN_URL || 'http://127.0.0.1:18089');
  await page.fill('#key',settings.BUSINESS_API_KEY);
  await page.locator('#loginForm button').click();
  await page.locator('#login').waitFor({state:'hidden'});
  await page.fill('#simConversation','ui-check-'+Date.now());
  await page.fill('#simText','我想咨询老房翻新，大约90平');
  const acceptedRequest=page.waitForResponse(r=>r.url().includes('/api/simulate/')&&r.request().method()==='POST');
  await page.locator('#simButton').click();
  const accepted=await (await acceptedRequest).json();
  let completed=false;
  for(let attempt=0;attempt<100;attempt++){
    completed=await page.evaluate(async eventId=>{
      const snapshot=await fetch('/api/snapshot',{cache:'no-store'}).then(r=>r.json());
      return snapshot.outbox.some(o=>o.event_id===eventId && o.status==='mock_sent' && o.text.includes('整体翻新'));
    },accepted.id);
    if(completed)break;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!completed)throw Error('Fixture did not finish in time');
  const refreshed=page.waitForResponse(r=>r.url().endsWith('/api/snapshot') && r.request().method()==='GET');
  await page.locator('#refresh').click();
  const refreshedData=await (await refreshed).json();
  await page.waitForFunction(eventId=>data.outbox.some(o=>o.event_id===eventId&&o.status==='mock_sent'),accepted.id,{timeout:10000}).catch(async error=>{
    console.error(JSON.stringify({refreshedEvent:refreshedData.events.find(e=>e.id===accepted.id),ui:await page.evaluate(eventId=>({eventId,refreshGeneration,events:data.events.map(e=>({id:e.id,status:e.status})),outbox:data.outbox.map(o=>({event_id:o.event_id,status:o.status})),notice:document.querySelector('#notice').innerText}),accepted.id)}));
    throw error;
  });
  await page.locator('#recent').getByText('我想咨询老房翻新，大约90平',{exact:true}).first().waitFor();
  await page.screenshot({path:path.join(root,'artifacts/admin-preview.png'),fullPage:true});
  await page.locator('[data-view="conversations"]').click();
  await page.locator('[data-open="'+accepted.conversation_id+'"]').click();
  await page.waitForFunction(()=>document.querySelector('#conversationDetail').innerText.includes('整体翻新'));
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(root,'artifacts/admin-mobile-preview.png'),fullPage:true});
  if(errors.length)throw Error(JSON.stringify(errors));
  console.log(JSON.stringify({result:'passed',scope:'own admin login, simulation, conversation detail, desktop/mobile render',pageErrors:0}));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
