// Focused browser check for the merchant's single-choice contact direction.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const html = fs.readFileSync(path.join(__dirname, '../business-service/web/onboard.html'), 'utf8');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const page = await browser.newPage();
    await page.route('http://127.0.0.1:8088/onboard?token=direction-ui-check', route =>
      route.fulfill({ status: 200, contentType: 'text/html', body: html }));
    await page.route('http://127.0.0.1:8088/api/onboard/context?token=direction-ui-check', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        industries: [{ id: 'auto', name: '汽车服务' }],
        projects: [{ id: 'care', industry_id: 'auto', name: '日常保养', question: '保养什么？' }],
        prefill: { review_reason: '请确认资料', contact_name: '测试联系人', contact_phone: '13800000000',
          profile: { industry_id: 'auto', fields: { '门店抖音号': '123456789',
            '抖音昵称': '汽车门店', '完整门店名称': '测试汽车门店', '服务区域': '泰安市',
            '资料或服务': '预约时间' },
            projects: { main: [{ id: 'care', name: '日常保养' }], available: [], excluded: [] } } }
      }) }));
    await page.goto('http://127.0.0.1:8088/onboard?token=direction-ui-check');
    await page.waitForFunction(() => document.querySelector('#materials')?.value === '预约时间');
    const options = await page.locator('#materials option').evaluateAll(items =>
      items.map(item => item.value).filter(Boolean));
    if (JSON.stringify(options) !== JSON.stringify(['预约时间', '折扣']))
      throw Error('contact direction must have exactly two selectable values');
    await page.locator('#materials').selectOption('折扣');
    const value = await page.evaluate(() => payload().fields.materials_or_services);
    if (value !== '折扣') throw Error('selected contact direction was not submitted');
    console.log(JSON.stringify({ result: 'passed', checks: ['prefill', 'single choice', 'payload'] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
