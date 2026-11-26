const assert = require('node:assert/strict');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const config = JSON.parse(fs.readFileSync(0, 'utf8'));
const profile = fs.mkdtempSync('/tmp/edcs-entry-ui-');
const chrome = spawn(
  '/usr/bin/google-chrome',
  [
    '--headless=new',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
);
const sockets = [];

async function waitFor(callback, label) {
  for (let i = 0; i < 120; i++) {
    try {
      if (await callback()) return;
    } catch {}
    await delay(75);
  }
  throw new Error(`Timed out: ${label}`);
}

async function connect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  sockets.push(socket);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  let next = 0;
  const pending = new Map();
  socket.addEventListener('message', (event) => {
    const result = JSON.parse(event.data);
    const item = pending.get(result.id);
    if (!item) return;
    clearTimeout(item.timer);
    pending.delete(result.id);
    if (result.error) item.reject(new Error(JSON.stringify(result.error)));
    else item.resolve(result.result);
  });
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(method));
      }, 5000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  const evaluate = async (expression) => {
    const result = await call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails)
      throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call('Page.enable');
  return { call, evaluate };
}

async function key(client, key, code, keyCode) {
  const values = { key, code, windowsVirtualKeyCode: keyCode };
  await client.call('Input.dispatchKeyEvent', { type: 'keyDown', ...values });
  await client.call('Input.dispatchKeyEvent', { type: 'keyUp', ...values });
}

async function main() {
  const portFile = `${profile}/DevToolsActivePort`;
  await waitFor(() => fs.existsSync(portFile), 'Chrome start');
  const port = fs.readFileSync(portFile, 'utf8').split('\n')[0];
  const targets = async () =>
    (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const parentTarget = (await targets()).find((item) => item.type === 'page');
  const parent = await connect(parentTarget);
  await parent.call('Network.setCookie', { ...config.cookie, url: config.base });
  await parent.call('Emulation.setDeviceMetricsOverride', {
    width: 1366,
    height: 768,
    deviceScaleFactor: 1,
    mobile: false,
  });
  const statement = `${config.base}/debit-credit/?member=${config.member}&payment_type=${config.paymentType}`;
  await parent.call('Page.navigate', { url: statement });
  await waitFor(
    () => parent.evaluate("!!document.querySelector('[data-add-entry-link]')"),
    'statement',
  );
  assert.equal(
    await parent.evaluate("document.querySelector('[data-add-entry-link]').pathname"),
    '/debit-credit/new/',
  );

  const openPopup = async () => {
    await waitFor(
      () =>
        parent.evaluate(
          "document.readyState === 'complete' && !!document.querySelector('[data-entry-shortcut]')",
        ),
      'shortcut ready',
    );
    await key(parent, 'F2', 'F2', 113);
    let target;
    await waitFor(async () => {
      target = (await targets()).find(
        (item) =>
          item.type === 'page' &&
          item.id !== parentTarget.id &&
          item.url.includes('entry_popup=1'),
      );
      return target;
    }, 'F2 popup');
    const popup = await connect(target);
    await popup.call('Emulation.setDeviceMetricsOverride', {
      width: 1100,
      height: 760,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await waitFor(
      () =>
        popup.evaluate(
          "document.querySelector('[data-entry-tab]')?.getAttribute('aria-selected') === 'true'",
        ),
      'entry tabs',
    );
    return { popup, target };
  };

  let { popup, target } = await openPopup();
  assert.equal(
    await popup.evaluate("document.querySelectorAll('[role=tab]').length"),
    4,
  );
  assert.equal(
    await popup.evaluate(
      "document.querySelectorAll('[role=tabpanel]:not([hidden])').length",
    ),
    1,
  );
  assert.equal(
    await popup.evaluate(
      "getComputedStyle(document.querySelector('#sidebar')).display",
    ),
    'none',
  );
  assert.equal(
    await popup.evaluate("document.querySelector('#id_member').value"),
    String(config.member),
  );
  for (const id of ['member-balance', 'child-balance', 'details', 'account']) {
    await popup.evaluate(`document.querySelector('#entry-tab-${id}').click()`);
    assert.equal(
      await popup.evaluate(
        "document.querySelectorAll('[role=tabpanel]:not([hidden])').length",
      ),
      1,
    );
  }
  await popup.evaluate("document.querySelector('#entry-tab-account').focus()");
  await key(popup, 'ArrowRight', 'ArrowRight', 39);
  assert.equal(
    await popup.evaluate('document.activeElement.id'),
    'entry-tab-member-balance',
  );
  // Required fields on hidden tabs must be revealed before native validation.
  await popup.evaluate(`(() => {
    const member=document.querySelector('#id_member'); member.value=''; member.dispatchEvent(new Event('change',{bubbles:true}));
    document.querySelector('#entry-tab-details').click();
    document.querySelector('#id_credit').value='1000';
    document.querySelector('[data-entry-actions] button').click();
  })()`);
  assert.equal(
    await popup.evaluate("document.querySelector('[role=tab][aria-selected=true]').id"),
    'entry-tab-account',
  );
  assert.equal(await popup.evaluate('document.activeElement.id'), 'id_member_combo');
  await popup.evaluate(`(() => {
    const member=document.querySelector('#id_member'); member.value='${config.member}'; member.dispatchEvent(new Event('change',{bubbles:true}));
    document.querySelector('#entry-tab-details').click();
    document.querySelector('#id_reference').value='BROWSER-TABBED-ENTRY';
    document.querySelector('#id_description').value='Browser verification of existing entry posting.';
  })()`);
  for (const [width, height] of [
    [1100, 760],
    [900, 600],
    [390, 844],
  ]) {
    await popup.call('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false,
    });
    assert.equal(
      await popup.evaluate('document.documentElement.scrollHeight <= innerHeight + 1'),
      true,
      `page scroll at ${width}x${height}`,
    );
    assert.equal(
      await popup.evaluate(
        "document.querySelector('[data-entry-actions]').getBoundingClientRect().bottom <= innerHeight",
      ),
      true,
    );
  }
  await popup.call('Emulation.setDeviceMetricsOverride', {
    width: 1100,
    height: 760,
    deviceScaleFactor: 1,
    mobile: false,
  });
  assert.equal(
    await popup.evaluate(
      "(() => { const panel=document.querySelector('[data-entry-panels]'); return panel.scrollHeight <= panel.clientHeight + 1; })()",
    ),
    true,
    'entry details fit without scrolling on desktop',
  );
  await popup.evaluate(
    "document.querySelector('#entry-tab-child-balance').click(); document.querySelector('#entry-tab-details').click()",
  );
  assert.equal(
    await popup.evaluate("document.querySelector('#id_credit').value"),
    '1000',
  );
  const shot = await popup.call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(
    '/tmp/debit-credit-entry-tabs.png',
    Buffer.from(shot.data, 'base64'),
  );
  await popup.evaluate("document.querySelector('[data-entry-actions] button').click()");
  await waitFor(
    () =>
      parent.evaluate(
        "location.pathname === '/debit-credit/' && document.body.textContent.includes('BROWSER-TABBED-ENTRY')",
      ),
    'saved credit redirect to statement',
  );
  await waitFor(
    async () => !(await targets()).some((item) => item.id === target.id),
    'saved popup closed',
  );

  ({ popup, target } = await openPopup());
  await popup.evaluate(`(() => {
    const account=document.querySelector('#id_payment_type');
    account.value='child-type:${config.childType}';
    account.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await waitFor(
    () =>
      popup.evaluate(
        `!!document.querySelector('#id_child_deposit option[value="${config.childAccount}"]')`,
      ),
    'child account loaded',
  );
  await popup.evaluate(`(() => {
    const child=document.querySelector('#id_child_deposit');
    child.value='${config.childAccount}'; child.dispatchEvent(new Event('change',{bubbles:true}));
    document.querySelector('#entry-tab-details').click();
    document.querySelector('#id_credit').value='200';
    document.querySelector('#id_reference').value='BROWSER-TABBED-CHILD';
    document.querySelector('[data-entry-actions] button').click();
  })()`);
  await waitFor(
    () => parent.evaluate("document.body.textContent.includes('BROWSER-TABBED-CHILD')"),
    'child credit on existing child statement',
  );
  await waitFor(
    async () => !(await targets()).some((item) => item.id === target.id),
    'child popup closed',
  );
  await parent.call('Page.navigate', { url: statement });

  ({ popup, target } = await openPopup());
  await key(popup, 'Escape', 'Escape', 27).catch(() => {});
  await waitFor(
    async () => !(await targets()).some((item) => item.id === target.id),
    'Escape closes popup',
  );
  assert.equal(await parent.evaluate('location.pathname'), '/debit-credit/');

  // The original Add entry link continues to open the ordinary full page.
  await parent.evaluate("document.querySelector('[data-add-entry-link]').click()");
  await waitFor(
    () =>
      parent.evaluate(
        "location.pathname === '/debit-credit/new/' && document.querySelector('[role=tab]')?.getAttribute('aria-selected') === 'true'",
      ),
    'full Add entry page',
  );
  assert.equal(
    await parent.evaluate(
      "document.body.classList.contains('transaction-entry-popup')",
    ),
    false,
  );
  assert.equal(
    await parent.evaluate('document.documentElement.scrollHeight <= innerHeight + 1'),
    true,
  );
  await key(parent, 'Escape', 'Escape', 27);
  await waitFor(
    () => parent.evaluate("location.pathname === '/debit-credit/'"),
    'Escape leaves full entry page',
  );
  console.log(
    'Browser checks passed: four tabs, keyboard navigation, hidden-field validation, retained values, viewport fit, F2 popup, original Add entry link, Escape and posting.',
  );
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    for (const socket of sockets) socket.close();
    chrome.kill('SIGTERM');
    await delay(150);
    fs.rmSync(profile, { recursive: true, force: true });
  });
