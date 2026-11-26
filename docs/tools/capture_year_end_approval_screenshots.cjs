#!/usr/bin/env node
// Photograph current Django-rendered pages. No page is posted by this browser.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const root = path.resolve(__dirname, '../..');
const html = '/tmp/edcs-year-end-guide-html';
const out = path.join(root, 'docs/year_end_approval_assets');
const profile = fs.mkdtempSync('/tmp/edcs-guide-chrome-');
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  const file = pathname.startsWith('/static/')
    ? path.join(root, pathname.slice(1))
    : path.join(html, path.basename(pathname));
  const mime = {
    '.html': 'text/html',
    '.css': 'text/css',
    '.js': 'text/javascript',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
  };
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404);
    response.end();
    return;
  }
  response.writeHead(200, {
    'Content-Type': mime[path.extname(file)] || 'application/octet-stream',
  });
  fs.createReadStream(file).pipe(response);
});

async function main() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
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
      '--hide-scrollbars',
      '--remote-debugging-port=0',
      `--user-data-dir=${profile}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  );
  let socket;
  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    for (let i = 0; !fs.existsSync(portFile) && i < 200; i++) await delay(50);
    if (!fs.existsSync(portFile)) throw new Error('Chrome did not start');
    const port = fs.readFileSync(portFile, 'utf8').split('\n')[0];
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    socket = new WebSocket(
      targets.find((target) => target.type === 'page').webSocketDebuggerUrl,
    );
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    let id = 0;
    const pending = new Map();
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !pending.has(message.id)) return;
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    });
    const call = (method, params = {}) =>
      new Promise((resolve, reject) => {
        const key = ++id;
        pending.set(key, { resolve, reject });
        socket.send(JSON.stringify({ id: key, method, params }));
      });
    await call('Page.enable');
    await call('Emulation.setDeviceMetricsOverride', {
      width: 1500,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const evaluate = async (expression) => {
      const response = await call('Runtime.evaluate', {
        expression,
        returnByValue: true,
        awaitPromise: true,
      });
      if (response.exceptionDetails)
        throw new Error(JSON.stringify(response.exceptionDetails));
      return response.result.value;
    };
    const captures = [
      ['01-user-permissions', '01-permissions', 'permissions'],
      ['02-hierarchy-configuration', '02-hierarchy-module', 'top', 700],
      ['02-hierarchy-configuration', '03-approval-levels', 'levels'],
      ['03-hierarchy-list', '04-saved-hierarchy', 'main'],
      ['04-quarterly-interest', '05-quarterly-interest', 'top', 1050],
      ['05-member-year-end-review', '06-member-year-end-review', 'review'],
      ['06-account-approval-pending', '07-request-pending', 'top', 1050],
      ['06-account-approval-pending', '08-administrator-decision', 'decision'],
      ['07-ordinary-approver-access', '09-ordinary-approver-scope', 'main'],
      ['08-next-approval-level', '10-second-level', 'progress'],
      ['09-account-approval-complete', '11-request-approved', 'top', 1050],
      ['10-member-posted', '12-member-posted', 'review'],
      ['11-child-year-end-review', '13-child-year-end-review', 'main'],
      ['12-child-approval-pending', '14-child-request', 'top', 1050],
      ['13-batch-year-end-review', '15-batch-review', 'top', 1060],
      ['13-batch-year-end-review', '16-batch-approval-form', 'batchForm'],
      ['14-approved-batch-history', '17-batch-history', 'main'],
      ['15-current-year-preview', '18-current-year', 'top', 1030],
      ['16-audit-logs', '19-audit-trail', 'top', 1000],
    ];
    for (const [source, name, mode, height] of captures) {
      await call('Page.navigate', { url: `${base}/${source}.html` });
      for (let i = 0; i < 100; i++) {
        if (
          await evaluate(
            'document.readyState === "complete" && !!document.querySelector("#main-content")',
          )
        )
          break;
        await delay(50);
      }
      await evaluate('document.fonts.ready.then(() => true)');
      await delay(200);
      const clip = await evaluate(`(() => {
        const main = document.querySelector('#main-content');
        const bounds = element => { const r = element.getBoundingClientRect(); return {x:r.x+scrollX, y:r.y+scrollY, width:r.width, height:r.height}; };
        const mode = ${JSON.stringify(mode)};
        let r = bounds(main);
        if (mode === 'permissions') {
          const row = [...document.querySelectorAll('.permission-table tr')].find(node => node.textContent.includes('Quarterly Interest'));
          r.y = bounds(row).y - 110; r.height = 470;
        } else if (mode === 'levels') {
          const levels = [...document.querySelectorAll('[data-approval-levels] > [data-approval-level]')];
          r = bounds(levels[0]); const last = bounds(levels[1]); r.height = last.y + last.height - r.y;
        }
        else if (mode === 'review') r = bounds(document.querySelector('#main-content > section.card.mt-6'));
        else if (mode === 'progress') r = bounds([...main.querySelectorAll('section')].find(node => node.querySelector('h2')?.textContent.includes('Approval progress')));
        else if (mode === 'decision') r = bounds([...main.querySelectorAll('section')].find(node => node.querySelector('h2')?.textContent.includes('Record a decision')));
        else if (mode === 'batchForm') r = bounds(main.querySelector('form[method="post"]'));
        if (mode === 'top') r.height = Math.min(r.height, ${height || 1000});
        r.y = Math.max(0, r.y); r.height = Math.min(r.height, 1800); r.scale = 1;
        return r;
      })()`);
      const shot = await call('Page.captureScreenshot', {
        format: 'png',
        clip,
        captureBeyondViewport: true,
      });
      fs.writeFileSync(path.join(out, `${name}.png`), Buffer.from(shot.data, 'base64'));
      console.log(`${name}.png`);
    }
  } finally {
    if (socket) socket.close();
    chrome.kill('SIGTERM');
    await new Promise((resolve) => server.close(resolve));
    await delay(200);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}
main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
