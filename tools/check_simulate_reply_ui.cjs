// Targeted browser check. Intercepts the submit request to avoid changing local conversations.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
  .split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://localhost:8088');
    await page.fill('#key', env.BUSINESS_API_KEY);
    await page.locator('#loginForm button').click();
    await page.locator('#login').waitFor({state: 'hidden'});
    await page.locator('#openFastGPT').getByText('模型与工作流').waitFor();
    const leadLabels = await page.evaluate(() => [
      leadLabel({kind: 'phone', value: '13700000000'}),
      leadLabel({kind: 'wechat', value: '13700000000'}),
    ]);
    if (leadLabels.join('|') !== '电话|微信') {
      throw Error('lead channel labels are incorrect: ' + leadLabels.join('|'));
    }
    await page.locator('[data-view="conversations"]').click();
    const row = page.locator('#conversationList tr').filter({hasText: '模拟会话一'});
    await row.locator('[data-open]').click();
    await page.locator('#conversationDetail [data-simulate-reply]').click();
    await page.locator('#simulateReplyDialog').waitFor({state: 'visible'});
    const conversation = await page.evaluate(() => data.conversations.find(item => item.id === selected));
    const target = await page.locator('#simulateReplyTarget').textContent();
    if (!target.includes('模拟会话一')) throw Error('wrong conversation target');
    let intercepted;
    await page.route('**/api/simulate/**', async route => {
      intercepted = {url: route.request().url(), body: route.request().postDataJSON()};
      await route.fulfill({status: 200, contentType: 'application/json', body: '{}'});
    });
    await page.fill('#simulateReplyText', '测试同一会话的模拟用户回复');
    await page.locator('#sendSimulateReply').click();
    await page.locator('#simulateReplyDialog').waitFor({state: 'hidden'});
    if (!intercepted || !intercepted.url.endsWith('/api/simulate/' + encodeURIComponent(conversation.account_id)) ||
        intercepted.body.conversation_id !== conversation.external_id ||
        intercepted.body.customer_id !== conversation.customer_id ||
        intercepted.body.text !== '测试同一会话的模拟用户回复' ||
        intercepted.body.direction !== 'customer' ||
        !intercepted.body.event_id || !intercepted.body.message_id) {
      throw Error('simulation request did not preserve current account, conversation and customer');
    }
    if (errors.length) throw Error('browser errors: ' + errors.join('; '));
    console.log(JSON.stringify({result: 'passed', checks: ['Chinese sidebar label',
      'phone and WeChat lead labels',
      'conversation reply dialog', 'targeted existing conversation request'], submitted: false}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error.message); process.exit(1)});
