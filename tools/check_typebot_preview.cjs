// RETIRED: Typebot was removed; this script is retained only as historical evidence.
// Targeted local preview and import checks; never publishes a flow or calls a model.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const settings=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/).filter(s=>s&&!s.startsWith('#')&&s.includes('=')).map(s=>[s.slice(0,s.indexOf('=')),s.slice(s.indexOf('=')+1)]));
const builderUrl=settings.TYPEBOT_BUILDER_URL||'http://localhost:8080';
const assert=(value,label)=>{if(!value)throw Error(label)};
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 let importedId, context, builder;
 try {
  context=await browser.newContext({viewport:{width:1440,height:1050}});
  const page=await context.newPage();
  await page.goto(process.env.ADMIN_URL||'http://localhost:8088');
  await page.fill('#key',settings.BUSINESS_API_KEY);
  await page.locator('#loginForm button').click();
  await page.locator('#login').waitFor({state:'hidden'});
  const next=context.waitForEvent('page');
  await page.locator('#openTypebot').click();
  builder=await next;
  await builder.waitForURL(url=>url.pathname==='/typebots'||/^\/w\/[^/]+\/typebots$/.test(url.pathname),{timeout:60000});
  await builder.getByRole('button',{name:/Settings & Members/}).waitFor({timeout:30000});
  await builder.goto(new URL('/typebots/create',builderUrl).href);
  await builder.getByRole('button',{name:/Start from a template/}).click();
  await builder.getByText('Lead Generation',{exact:true}).first().click();
  const iframe=builder.locator('iframe');
  await iframe.waitFor();
  const viewerUrl=await iframe.getAttribute('src');
  assert(new URL(viewerUrl).hostname!==new URL(builderUrl).hostname,'builder and viewer must use different hostnames');
  const frame=builder.frameLocator('iframe');
  await frame.getByText('Welcome to',{exact:false}).waitFor({timeout:60000});
  await frame.getByRole('button',{name:'Hi!',exact:true}).click();
  await frame.getByText("What's your name?",{exact:false}).waitFor({timeout:30000});
  assert(!(await builder.locator('body').innerText()).includes('must use a different hostname'),'hostname error must disappear');
  await builder.screenshot({path:path.join(root,'artifacts/typebot-preview.png'),fullPage:true,timeout:10000});
  console.log('Template iframe loaded and advanced to the name question.');
  await builder.goto(new URL('/typebots/create',builderUrl).href);
  await builder.locator('input[type="file"]').waitFor({state:'attached'});
  await builder.waitForFunction(()=>{
   const input=document.querySelector('input[type="file"]');
   return input&&Object.keys(input).some(key=>key.startsWith('__reactProps$')&&typeof input[key]?.onChange==='function');
  });
  const draft=JSON.parse(fs.readFileSync(path.join(root,'config/typebot-renovation.json'),'utf8'));
  draft.name='[临时导入检查] '+new Date().toISOString();
  const responsePromise=builder.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('import'),{timeout:60000});
  const chooserPromise=builder.waitForEvent('filechooser');
  await builder.locator('label').filter({hasText:'Import a file'}).click();
  const chooser=await chooserPromise;
  await chooser.setFiles({name:'renovation-import-check.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(draft),'utf8')});
  const response=await responsePromise;
  const body=await response.json();
  // Current upstream deployments can use either the REST or RPC transport.
  const imported=body.typebot||body.json?.typebot||body.result?.data?.json?.typebot;
  importedId=imported?.id;
  assert(response.ok()&&importedId,'renovation file must import successfully; HTTP '+response.status());
  await builder.waitForURL(new RegExp('/typebots/'+importedId+'/edit'),{timeout:60000});
  await builder.getByText('业务服务调用 FastGPT',{exact:true}).waitFor({timeout:30000});
  assert(imported.groups.length===draft.groups.length,'imported groups must be preserved');
  assert(imported.variables.some(v=>v.name==='internal_key'),'business context variables must be preserved');
  await builder.goto(new URL('/typebots',builderUrl).href);
  const removed=await context.request.delete(new URL('/api/v1/typebots/'+importedId,builderUrl).href,{headers:{Origin:new URL(builderUrl).origin}});
  assert(removed.ok(),'temporary imported draft must be removed');
  importedId=undefined;
  const record={result:'passed',date:new Date().toISOString(),builder:builderUrl,viewer:viewerUrl,checks:['different hostnames','template iframe actual welcome','template button advances conversation','renovation file imported through browser file input','imported editor and business variables','temporary unpublished draft removed'],full_tests_run:false,real_business_conversation_verified:false,production_deployed:false};
  fs.writeFileSync(path.join(root,'artifacts/typebot-preview-checks.json'),JSON.stringify(record,null,2)+'\n','utf8');
  console.log(JSON.stringify(record));
 } catch(error) {
  if(builder){
   console.error(JSON.stringify({path:new URL(builder.url()).pathname,text:(await builder.locator('body').innerText()).slice(0,2200)}));
   await builder.screenshot({path:path.join(root,'.logs/typebot-preview-failure.png'),fullPage:true,timeout:5000}).catch(()=>{});
  }
  throw error;
 } finally {
  if(importedId&&context){
   const removed=await context.request.delete(new URL('/api/v1/typebots/'+importedId,builderUrl).href,{headers:{Origin:new URL(builderUrl).origin}});
   if(!removed.ok())console.error('Temporary draft cleanup failed: '+importedId+' HTTP '+removed.status());
  }
  await browser.close();
 }
})().catch(e=>{console.error(e.message.replace(/([?&](?:token|ticket)=)[^&\s]+/g,'$1[REDACTED]'));process.exit(1)});
