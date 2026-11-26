#!/usr/bin/env node
// Photograph current Django-rendered pages. No page is posted by this browser.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const root = path.resolve(__dirname, '../..');
const html = '/tmp/edcs-payment-plan-html';
const out = path.join(root, 'docs/payment_plan_assets');
const profile = fs.mkdtempSync('/tmp/edcs-plan-chrome-');
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  const pathname = url.pathname;
  const api =
    pathname === '/payment-plan-templates/options/'
      ? `api-template-${url.searchParams.get('template')}.json`
      : pathname === '/api/locations/'
        ? `api-locations-${url.searchParams.get('district')}.json`
        : pathname === '/api/offices/'
          ? `api-offices-${url.searchParams.get('district')}.json`
          : pathname === '/api/debit-credit/balances/'
            ? 'api-balances.json'
            : pathname === '/api/contributions/child-accounts/'
              ? 'api-child-accounts.json'
              : null;
  if (api && fs.existsSync(path.join(html, api))) {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(fs.readFileSync(path.join(html, api)));
    return;
  }
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
      width: 1800,
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
    const captures = JSON.parse(
      fs.readFileSync(path.join(out, 'capture-evidence.json'), 'utf8'),
    ).captures;
    for (const [source, name, mode, height, fill] of captures.filter(
      (item) => !process.argv[2] || item[1].includes(process.argv[2]),
    )) {
      await call('Emulation.setEmulatedMedia', { media: 'screen' });
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
      if (name !== '17-deletion-protected')
        await evaluate(
          "(() => { document.querySelectorAll('.dismiss-message').forEach(button => button.click()); return true; })()",
        );
      if (name === '17-deletion-protected')
        await evaluate(
          "(() => { document.querySelectorAll('.message-success .dismiss-message').forEach(button => button.click()); return true; })()",
        );
      for (const [fieldName, fieldValue] of Object.entries(fill || {})) {
        await evaluate(`(() => {
          const fields = [...document.getElementsByName(${JSON.stringify(fieldName)})];
          const value = ${JSON.stringify(fieldValue)};
          for (const field of fields) {
            if (field.disabled || field.type === 'password') continue;
            if (field.type === 'checkbox') {
              const checked = Array.isArray(value) ? value.includes(field.value) : Boolean(value);
              if (field.checked === checked) continue;
              field.checked = checked;
            } else { if (field.value === String(value)) continue; field.value = String(value); }
            field.dispatchEvent(new Event('input', {bubbles: true}));
            field.dispatchEvent(new Event('change', {bubbles: true}));
          }
          return true;
        })()`);
        await delay(180);
      }
      await delay(250);
      if (mode === 'printProfile')
        await call('Emulation.setEmulatedMedia', { media: 'print' });
      const clip = await evaluate(`(() => {
        const main = document.querySelector('#main-content');
        const bounds = element => { const r = element.getBoundingClientRect(); return {x:r.x+scrollX, y:r.y+scrollY, width:r.width, height:r.height}; };
        const mode = ${JSON.stringify(mode)};
        let r = bounds(main);
        const section = label => [...main.querySelectorAll('section')].find(node => node.querySelector('h2')?.textContent.includes(label));
        if (mode === 'planForm') {
          r=bounds(main.querySelector('[data-payment-plan-form]'));
          r.height=Math.min(r.height,1500);
        }
        else if (mode === 'templateForm') r.height=Math.min(r.height,1600);
        else if (mode === 'agreement') r=bounds(section('Monthly payment agreement'));
        else if (mode === 'contributionPreview') r=bounds(section('Allocation preview'));
        else if (mode === 'deleteDialog') r=bounds(main.querySelector('section.card'));
        else if (mode === 'currentPlan') r=bounds(section('Current payment plan'));
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
