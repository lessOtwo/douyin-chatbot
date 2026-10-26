// Focused browser check. All list responses are intercepted; no records are changed.
const {chromium} = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const many = (length, make) => Array.from({length}, (_, index) => make(index + 1));
const timestamp = 1792700000;
const accounts = many(45, i => ({id:`account-${i}`,merchant_id:`merchant-${i}`,
  douyin_nickname:`商家${i}`,industry:'教育教培',fastgpt_app_id:'app'}));
const conversations = many(45, i => ({id:`conv-${i}`,account_id:`account-${i}`,
  external_id:`会话${i}`,customer_id:`customer-${i}`,customer_nickname:`客户${i}`,
  mode:'bot',reply_rounds:0}));
const events = many(125, i => ({id:`event-${i}`,account_id:'account-1',
  conversation_id:'conv-1',event_id:`source-${i}`,message_id:`message-${i}`,
  text:`客户消息${i}`,direction:'customer',status:'blocked',revision:1,created_at:timestamp-i,
  error:'模拟异常'}));
const outbox = many(45, i => ({id:`outbox-${i}`,event_id:`event-${i}`,
  conversation_id:'conv-1',text:`回复${i}`,status:'mock_sent',created_at:timestamp-i,
  sent_at:timestamp-i,part_index:0}));
const leads = many(45, i => ({id:`lead-${i}`,account_id:'account-1',
  douyin_nickname:'商家1',store_name:'测试门店',conversation_id:'conv-1',
  external_id:'会话1',kind:'phone',value:`1380000${String(i).padStart(4,'0')}`,
  status:'candidate',created_at:timestamp-i,exported_at:null}));
const questions = many(45, i => ({id:`question-${i}`,account_id:'account-1',
  conversation_id:'conv-1',external_id:'会话1',customer_id:'customer-1',
  event_id:`event-${i}`,question:`问题${i}`,reason:'模拟',status:'open',created_at:timestamp-i}));
const industries = many(45, i => ({id:`industry-${i}`,draft_name:`行业${i}`,
  published_name:`行业${i}`,published_at:timestamp}));
const projects = many(45, i => ({id:`project-${i}`,industry_id:`industry-${i}`,
  draft_name:`项目${i}`,draft_question:'您好，想了解什么？',published_at:timestamp}));
const invitations = many(45, i => ({id:`invite-${i}`,merchant_id:null,status:'revoked',
  expires_at:timestamp+3600,created_at:timestamp-i}));
const merchants = many(45, i => ({id:`version-${i}`,merchant_id:`merchant-${i}`,
  version:1,status:'approved',profile:{fields:{'完整门店名称':`门店${i}`}},
  contact_name:'联系人',contact_phone:'13800000000',current:true}));

(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    const errors=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.route('**/api/snapshot', route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({send_mode:'mock',totals:{conversations:45,events:125,outbox:45,leads:45},
        conversations,events,outbox})}));
    await page.route('**/api/accounts', route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({accounts})}));
    const sources={conversations,outbox,errors:events,accounts,industries,
      projects:projects.map((item,i)=>({...item,industry_name:industries[i].draft_name})),invitations,merchants};
    const pageRequests=[];
    await page.route('**/api/admin/pages/**', route=>{
      const url=new URL(route.request().url()),segments=url.pathname.split('/');
      const kind=segments[4],number=Number(url.searchParams.get('page')||1);
      pageRequests.push({kind,page:number});
      if(kind==='history'){
        const items=events.filter(e=>e.conversation_id===segments[5]);
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
          conversation:conversations.find(c=>c.id===segments[5]),items:items.slice((number-1)*20,number*20).map(e=>({...e,replies:[]})),
          total:items.length,page:number,page_size:20})});
      }
      const filtered=kind==='projects'&&url.searchParams.get('search')?
        sources.projects.filter(p=>p.industry_name.includes(url.searchParams.get('search'))):sources[kind];
      if(!filtered)throw Error('unknown page source '+kind);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        items:filtered.slice((number-1)*20,number*20),total:filtered.length,page:number,page_size:20})});
    });
    await page.route('**/api/admin/options/industries', route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:industries})}));
    await page.route('**/api/admin/options/merchants', route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:merchants.map(m=>({merchant_id:m.merchant_id,store_name:m.profile.fields['完整门店名称']}))})}));
    const exportedSelections=[];
    await page.route('**/api/leads/export', route=>{
      exportedSelections.push(route.request().postDataJSON().ids);
      return route.fulfill({status:200,contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',body:'fixture'});
    });
    await page.route('**/api/unanswered?*', route=>{
      const status=new URL(route.request().url()).searchParams.get('status');
      const items=status==='resolved'?[]:questions,page=Number(new URL(route.request().url()).searchParams.get('page')||1);
      pageRequests.push({kind:'questions',page});
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:items.slice((page-1)*20,page*20),total:items.length,page,page_size:20})});
    });
    await page.route('**/api/leads?*', route=>{
      const query=new URL(route.request().url()).searchParams;
      const search=(query.get('search')||'').toLowerCase();
      const items=leads.filter(item=>[item.account_id,item.douyin_nickname,item.store_name]
        .some(value=>value.toLowerCase().includes(search)) && query.get('exported')!=='yes');
      const page=Number(query.get('page')||1);pageRequests.push({kind:'leads',page});
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:items.slice((page-1)*20,page*20),total:items.length,page,page_size:20})});
    });
    await page.goto('http://localhost:8088');
    await page.locator('#login').waitFor({state:'hidden'});
    const count = selector => page.locator(`${selector} tbody tr`).count();
    const jump = async (selector, number) => {
      await page.locator(`${selector} [data-page-form] input`).fill(String(number));
      await page.locator(`${selector} [data-page-form] button`).click();
      await page.waitForFunction(({selector,number})=>document.querySelector(`${selector} [data-pager]`)?.textContent.includes(`第 ${number} /`),{selector,number});
    };
    await page.locator('[data-view="outbox"]').click();
    if (await count('#outboxList')!==20) throw Error('outbox first page is not 20 rows');
    await jump('#outboxList',3);
    if (await count('#outboxList')!==5 || !(await page.locator('#outboxList').textContent()).includes('回复45'))
      throw Error('outbox jump to last page failed');
    await page.locator('#outboxList [data-page-step="-1"]').click();
    await page.waitForFunction(()=>document.querySelector('#outboxList [data-pager]')?.textContent.includes('第 2 /'));
    if (await count('#outboxList')!==20 || !(await page.locator('#outboxList').textContent()).includes('回复21'))
      throw Error('outbox previous page failed');
    await page.locator('#outboxList [data-page-step="1"]').click();
    await page.waitForFunction(()=>document.querySelector('#outboxList [data-pager]')?.textContent.includes('第 3 /'));
    if (await count('#outboxList')!==5) throw Error('outbox next page failed');
    if (await count('#eventErrors')!==20) throw Error('error list page size failed');
    await jump('#eventErrors',7);
    if (await count('#eventErrors')!==5 || !(await page.locator('#eventErrors').textContent()).includes('客户消息125'))
      throw Error('events beyond first 100 are not reachable');
    await page.locator('[data-view="conversations"]').click();
    await jump('#conversationList',3);
    if (await count('#conversationList')!==5) throw Error('conversation last page failed');
    await jump('#conversationList',1);
    await page.locator('[data-open="conv-1"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#conversationDetail .bubble:not(.reply)').length===20);
    if (await page.locator('#conversationDetail .bubble:not(.reply)').count()!==20) throw Error('history first page failed');
    await jump('#conversationDetail',7);
    if (await page.locator('#conversationDetail .bubble:not(.reply)').count()!==5)
      throw Error('history page beyond first 100 failed');
    await page.locator('[data-view="leads"]').click();
    await page.locator('#selectAllLeads').check();
    await page.locator('#leadList [data-page-step="1"]').click();
    await page.waitForFunction(()=>document.querySelector('#leadList [data-pager]')?.textContent.includes('第 2 /'));
    await page.locator('#selectAllLeads').check();
    if (!(await page.locator('#leadCount').textContent()).includes('已选 40 条'))
      throw Error('lead selections did not survive page change');
    await page.locator('#exportSelectedLeads').click();
    await page.waitForFunction(()=>document.querySelector('#leadCount')?.textContent.includes('已选 0 条'));
    if (exportedSelections.length!==1 || exportedSelections[0].length!==40)
      throw Error('cross-page export omitted selected rows');
    await jump('#leadList',3);
    if (await count('#leadList')!==5) throw Error('lead last page failed');
    await page.locator('[data-view="unanswered"]').click();
    await jump('#questionList',3);
    if (await count('#questionList')!==5) throw Error('question jump failed');
    await page.selectOption('#questionStatus','resolved');
    await page.waitForFunction(()=>document.querySelector('#questionList [data-pager="questions"]')?.textContent.includes('共 0 条'));
    if (!(await page.locator('#questionList [data-pager="questions"]').textContent()).includes('第 1 / 1 页'))
      throw Error('empty filter did not reset question pagination');
    await page.locator('[data-view="catalog"]').click();
    await jump('#industryList',3);
    await jump('#projectList',3);
    if (await count('#industryList')!==5 || await count('#projectList')!==5)
      throw Error('catalog pagination failed');
    await page.locator('[data-view="merchants"]').click();
    await jump('#invitationList',3);
    await jump('#merchantList',3);
    if (await count('#invitationList')!==5 || await count('#merchantList')!==5)
      throw Error('onboarding pagination failed');
    await page.locator('[data-view="accounts"]').click();
    await jump('#accountList',3);
    if (await count('#accountList')!==5) throw Error('account pagination failed');
    if (!pageRequests.some(item=>item.kind==='outbox'&&item.page===3) ||
        !pageRequests.some(item=>item.kind==='history'&&item.page===7) ||
        !pageRequests.some(item=>item.kind==='leads'&&item.page===2))
      throw Error('page navigation did not request the server page');
    if (errors.length) throw Error('browser errors: '+errors.join('; '));
    console.log(JSON.stringify({result:'passed',checks:['previous/next/jump','last partial page',
      '125-item list','history','cross-page lead selection','all admin lists'],writesIntercepted:true}));
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error.stack||error.message);process.exit(1)});
