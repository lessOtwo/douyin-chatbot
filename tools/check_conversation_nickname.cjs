// Focused read/display check against the existing local simulated conversation.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
  .split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));

(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8088');
    await page.fill('#key', env.BUSINESS_API_KEY);
    await page.locator('#loginForm button').click();
    await page.locator('#login').waitFor({state:'hidden'});
    const snapshot = await (await page.request.get('http://127.0.0.1:8088/api/snapshot')).json();
    const conversation = snapshot.conversations.find(row => row.customer_nickname === '泰安月嫂找红姐');
    if (!conversation) throw Error('Simulated peer conversation was not found');
    await page.locator('[data-view="conversations"]').click();
    await page.locator(`[data-open="${conversation.id}"]`).click();
    const label = await page.locator('#conversationDetail .bubble .small').first().textContent();
    if (!label.includes('客户：泰安月嫂找红姐')) throw Error('Admin transcript lacks customer nickname');
    const lead = snapshot.leads.find(row => row.conversation_id === conversation.id);
    if (!lead) throw Error('Original lead candidate was not found');
    const response = await page.request.post(`http://127.0.0.1:8088/api/leads/${lead.id}/history-link`,
      {headers:{Origin:'http://127.0.0.1:8088'}});
    if (!response.ok()) throw Error(`History link request failed: ${response.status()}`);
    const link = await response.json();
    const html = await (await page.request.get(link.url)).text();
    if (!html.includes('客户：泰安月嫂找红姐')) throw Error('Shared transcript lacks customer nickname');
    if (errors.length) throw Error('Browser errors: ' + errors.join('; '));
    console.log(JSON.stringify({result:'passed',checks:['admin customer label','shared transcript label'],
      conversation:conversation.external_id}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error.message);process.exit(1)});
