// Focused browser check for the overview simulation dialog; all API calls are mocked.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const html = fs.readFileSync(path.resolve(__dirname, '../business-service/web/index.html'));
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');
const server = http.createServer((request, response) => {
  response.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
  response.end(html);
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    const errors=[], sent=[], events=[];
    let historyReads=0;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request=route.request(), url=new URL(request.url()), api=url.pathname;
      const json=value => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
      if (api==='/api/snapshot') return json({send_mode:'mock',totals:{conversations:1,events:events.length,leads:0,outbox:0},conversations:[],events:[],outbox:[]});
      if (api==='/api/accounts') return json({accounts:[{id:'101',merchant_id:'one',douyin_nickname:'测试商家',industry:'家政服务'}]});
      if (api.startsWith('/api/admin/options/')) return json({items:[]});
      if (api.startsWith('/api/admin/pages/history/')) {
        historyReads++;
        const items=[...events].reverse().map(event => ({...event,replies:event.id==='event-1'&&historyReads>=2?
          [{id:'reply-1',text:'店里工作人员会和您确认 😊',status:'mock_sent',part_index:0}]:[]}));
        return json({conversation:{id:'internal-chat',account_id:'101',external_id:'模拟会话一',
          customer_id:'模拟客户一',customer_nickname:'测试客户'},items,total:items.length,page:1,page_size:20});
      }
      if (api.startsWith('/api/admin/pages/')) return json({items:[],total:0,page:1,page_size:20});
      if (api.startsWith('/api/admin/images/')) return route.fulfill({status:200,contentType:'image/png',body:png});
      if (api.startsWith('/api/simulate/')) {
        const body=request.postDataJSON();
        sent.push(body);
        events.push({id:`event-${sent.length}`,account_id:'101',conversation_id:'internal-chat',
          text:body.text||'[图片]',direction:'customer',created_at:1792660000+sent.length,
          status:'processed',has_image:body.image?1:0});
        return json({id:`event-${sent.length}`,status:'pending',conversation_id:'internal-chat',duplicate:false});
      }
      return json({items:[],total:0,page:1,page_size:20});
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('#login').waitFor({state:'hidden'});
    await page.fill('#simNickname','测试客户');
    await page.fill('#simText','你好');
    await page.locator('#simButton').click();
    await page.locator('#simulateConversationDialog').waitFor({state:'visible'});
    await page.locator('#simulateConversationHistory .bubble').first().waitFor();
    if (!(await page.locator('#simulateConversationHistory').textContent()).includes('客户：测试客户')) throw Error('nickname missing in dialog');
    await page.waitForFunction(() => document.querySelector('#simulateConversationHistory')?.textContent.includes('店里工作人员会和您确认 😊'), null, {timeout:7000});
    if (historyReads<2) throw Error('five-second polling did not load the reply');

    await page.fill('#simConversationReplyText','看看图片');
    await page.locator('[data-sim-emoji="😊"]').click();
    await page.locator('#simConversationImage').setInputFiles({name:'sample.png',mimeType:'image/png',buffer:png});
    await page.locator('#simImagePreview img').waitFor();
    await page.locator('#sendSimConversationReply').click();
    await page.waitForFunction(() => document.querySelectorAll('#simulateConversationHistory .bubble').length>=3);
    if (sent.length!==2 || sent[1].conversation_id!=='模拟会话一' || sent[1].customer_id!=='模拟客户一') throw Error('reply did not continue same conversation');
    if (!sent[1].text.includes('😊') || !sent[1].image.startsWith('data:image/png;base64,')) throw Error('emoji or image was not sent');
    await page.locator('#simulateConversationHistory img.chat-image').waitFor();
    await page.locator('#simConversationImage').setInputFiles({name:'sample.png',mimeType:'image/png',buffer:png});
    await page.locator('#sendSimConversationReply').click();
    await page.waitForFunction(() => document.querySelectorAll('#simulateConversationHistory img.chat-image').length===2);
    if (sent.length!==3 || sent[2].text!=='' || !sent[2].image.startsWith('data:image/png;base64,')) throw Error('image-only reply was not sent');
    await page.locator('#closeSimConversation').click();
    const readsAfterClose=historyReads;
    await page.waitForTimeout(5300);
    if (historyReads!==readsAfterClose) throw Error('polling continued after dialog closed');
    if (errors.length) throw Error('browser error: '+errors.join(' | '));
    console.log('simulation modal: same conversation, five-second refresh, emoji, image and close cleanup passed');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode=1; server.close(); });
