// Read-only browser check for the newly added operator and merchant pages.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('C:/Users/simai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const settings = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
  .split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8088');
    await page.fill('#key', settings.BUSINESS_API_KEY);
    await page.locator('#loginForm button').click();
    await page.locator('#login').waitFor({ state: 'hidden' });
    await page.locator('[data-view="catalog"]').click();
    await page.locator('#industryForm').waitFor({ state: 'visible' });
    await page.locator('#projectForm').waitFor({ state: 'visible' });
    const projects = await page.locator('#projectList').innerText();
    if (!projects.includes('中小学辅导') || !projects.includes('职业资格考证')) throw Error('education projects not shown');
    const projectHeaders = await page.locator('#projectList th').allTextContents();
    if (projectHeaders.join('|') !== '项目|所属行业|状态|操作') throw Error('project columns are not in the requested order');
    const firstProjectCells = await page.locator('#projectList tbody tr:first-child td').allTextContents();
    if (firstProjectCells.length !== 4 || firstProjectCells[0].trim() === firstProjectCells[1].trim()) throw Error('project and industry are not separate columns');
    if (!(await page.locator('[data-delete-industry]').count()) || !(await page.locator('[data-delete-project]').count())) throw Error('catalog delete actions missing');
    await page.locator('[data-view="merchants"]').click();
    await page.locator('#inviteForm').waitFor({ state: 'visible' });
    const inviteHours = await page.locator('#inviteHours option').evaluateAll(options => options.map(option => [option.value, option.textContent.trim()]));
    if (JSON.stringify(inviteHours) !== JSON.stringify([['6', '6 小时有效'], ['12', '12 小时有效'], ['24', '24 小时有效']])) throw Error('invitation expiry options are incorrect');
    if (await page.locator('#inviteHours').inputValue() !== '6') throw Error('invitation expiry does not default to 6 hours');
    if ((await page.locator('#invitationList').innerText()).includes('已撤销') && !(await page.locator('[data-delete-invite]').count())) throw Error('revoked invitation delete action missing');
    await page.evaluate(() => {
      onboarding.merchants.push({ id: 'review-ui-check', merchant_id: 'review-ui-check', version: 1,
        status: 'pending', profile: { fields: { '完整门店名称': '审核界面检查', '门店抖音号': '1615185357' }, projects: {} },
        contact_name: '检查', contact_phone: '13800000000' });
      showMerchant('review-ui-check');
    });
    await page.waitForFunction(() => document.querySelector('#reviewWorkflow')?.options.length > 1);
    if (await page.locator('#reviewWorkflow').inputValue() !== '') throw Error('review workflow must not be preselected');
    if (!(await page.locator('[data-review="review-ui-check"][data-decision="approved"]').isDisabled())) throw Error('approve action should require workflow selection');
    const workflowOptions = await page.locator('#reviewWorkflow option').allTextContents();
    if (!workflowOptions.some(value => value.includes('抖音私信留资工作流'))) throw Error('published workflow is missing');
    await page.locator('#reviewWorkflow').selectOption({ index: 1 });
    if (await page.locator('[data-review="review-ui-check"][data-decision="approved"]').isDisabled()) throw Error('approve action did not unlock after selecting workflow');
    if (!(await page.locator('#reviewReason').count()) || !(await page.locator('[data-decision="rejected"]').innerText()).includes('生成修改链接')) throw Error('return reason or retry link action missing');
    await page.evaluate(() => showReturnInvite({ contact_name: '测试联系人', contact_phone: '13800000000', review_reason: '请修改服务区域' }, 'test-token', 6));
    const returnMessage = await page.locator('#returnMessage').inputValue();
    if (!returnMessage.includes('请修改服务区域') || !returnMessage.includes('/onboard?token=test-token')) throw Error('return message is missing reason or link');
    await page.locator('[data-view="accounts"]').click();
    if ((await page.locator('#accountList').innerText()).includes('renovation-demo')) throw Error('preset demo account remains');
    if ((await page.locator('#accountList tbody tr').count()) && !(await page.locator('[data-delete-account]').count())) throw Error('account delete action missing');
    const labels = await page.evaluate(() => ({
      invitation: ['active', 'revoked', 'used'].map(value => stateLabel(value, invitationLabels)),
      merchant: ['pending', 'approved', 'rejected'].map(value => stateLabel(value, merchantLabels)),
      lead: ['phone', 'wechat'].map(value => leadKindLabels[value]),
      subtitle: document.querySelector('.sub').textContent.trim(),
      invitationText: document.querySelector('#invitationList').textContent,
    }));
    if (JSON.stringify(labels.invitation) !== JSON.stringify(['有效', '已撤销', '已使用'])) throw Error('invitation statuses are not Chinese');
    if (JSON.stringify(labels.merchant) !== JSON.stringify(['待审核', '审核通过', '已退回'])) throw Error('merchant statuses are not Chinese');
    if (JSON.stringify(labels.lead) !== JSON.stringify(['电话', '微信'])) throw Error('lead types are not Chinese');
    if (labels.subtitle !== '私信客服运营后台' || /\b(active|revoked|used)\b/.test(labels.invitationText)) throw Error('English UI text remains');
    for (const view of ['catalog', 'merchants', 'accounts']) {
      await page.locator(`[data-view="${view}"]`).click();
      const visible = await page.locator(`#${view}`).innerText();
      if (/\b(active|revoked|used|pending|approved|rejected|phone|wechat|CRM)\b/.test(visible)) {
        throw Error(`English label remains in ${view}`);
      }
    }
    await page.route('http://127.0.0.1:8088/onboard?token=retry-ui-check', route => route.fulfill({ status: 200, contentType: 'text/html', body: fs.readFileSync(path.join(root, 'business-service/web/onboard.html'), 'utf8') }));
    await page.route('http://127.0.0.1:8088/api/onboard/context?token=retry-ui-check', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ industries: [{ id: 'industry-1', name: '教育教培' }], projects: [{ id: 'project-1', industry_id: 'industry-1', name: '语言培训', question: '想学什么语言？' }], prefill: { review_reason: '请修改服务区域', contact_name: '测试联系人', contact_phone: '13800000000', profile: { industry_id: 'industry-1', fields: { '门店抖音号': '1615185357', '抖音昵称': '测试门店', '完整门店名称': '测试门店', '服务区域': '泰安市', '门店详细地址': '示例街道1号', '资料或服务': '提供课程表' }, projects: { main: [{ id: 'project-1', name: '语言培训' }], available: [], excluded: [] } } } }) }));
    await page.goto('http://127.0.0.1:8088/onboard?token=retry-ui-check');
    await page.waitForFunction(() => document.querySelector('#returnReason')?.textContent.includes('请修改服务区域'));
    if (await page.locator('#storeName').inputValue() !== '测试门店' || await page.locator('#address').inputValue() !== '示例街道1号' || !(await page.locator('input[data-project="project-1"][value="main"]').isChecked())) throw Error('returned form was not prefilled');
    const invalid = await page.goto('http://127.0.0.1:8088/onboard');
    if (invalid.status() !== 404) throw Error('invitation page is not token gated');
    if (errors.length) throw Error(errors.join('; '));
    console.log(JSON.stringify({ result: 'passed', checks: ['catalog, merchant and account pages', 'Chinese status labels', 'invitation gate', 'no page errors'] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
