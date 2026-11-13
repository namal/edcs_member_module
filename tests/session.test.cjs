const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const source = readFileSync('static/js/session.js', 'utf8');
const key = 'edcs-session-test-scope';
const flush = () => new Promise(setImmediate);

function tab({ storage = new Map(), storageBlocked = false, status = 204 } = {}) {
  let now = 1000000;
  const listeners = new Map();
  const intervals = [];
  const requests = [];
  const redirects = [];
  const body = { hidden: false };
  const policy = {
    timeout: 900,
    scope: 'test-scope',
    activityUrl: '/session/activity/',
    logoutUrl: '/logout/',
    loginUrl: '/login/',
    csrfToken: 'test-csrf',
  };
  const listen = (name, callback) => {
    if (!listeners.has(name)) listeners.set(name, []);
    listeners.get(name).push(callback);
  };
  runInNewContext(source, {
    document: {
      body,
      getElementById: () => ({ textContent: JSON.stringify(policy) }),
      addEventListener: listen,
    },
    window: { addEventListener: listen },
    location: {
      href: 'https://example.test/members/',
      replace: (url) => redirects.push(url),
    },
    localStorage: {
      getItem: (name) => {
        if (storageBlocked) throw new Error('Storage disabled');
        return storage.get(name) ?? null;
      },
      setItem: (name, value) => {
        if (storageBlocked) throw new Error('Storage disabled');
        storage.set(name, value);
      },
    },
    Date: { now: () => now },
    URL,
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { status };
    },
    setTimeout: () => {},
    setInterval: (callback) => intervals.push(callback),
  });
  return {
    body,
    storage,
    requests,
    redirects,
    advance: (milliseconds, tick = true) => {
      now += milliseconds;
      if (tick) intervals.forEach((callback) => callback());
    },
    emit: (name, event = {}) => {
      for (const callback of listeners.get(name) ?? []) callback(event);
    },
  };
}

test('an unattended tab expires without sending keepalive requests', async () => {
  const page = tab();
  page.advance(899000);
  assert.equal(page.requests.length, 0);
  page.advance(1000);
  await flush();
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, '/logout/');
  assert.equal(page.requests[0].options.method, 'POST');
  assert.equal(page.requests[0].options.headers['X-CSRFToken'], 'test-csrf');
  assert.equal(page.requests[0].options.keepalive, true);
  assert.equal(page.body.hidden, true);
  assert.deepEqual(page.redirects, ['/login/?expired=1']);
});

test('typing in a long form keeps the session alive', async () => {
  const page = tab();
  for (let minute = 0; minute < 31; minute++) {
    page.advance(30000);
    page.emit('keydown', { isTrusted: true });
  }
  await flush();
  assert.ok(page.requests.length > 0);
  assert.ok(page.requests.every(({ url }) => url === '/session/activity/'));
  assert.equal(page.redirects.length, 0);
});

test('activity in another tab prevents logout of the shared session', () => {
  const page = tab();
  page.advance(890000, false);
  page.storage.set(key, '1890000');
  page.emit('storage', { key });
  page.advance(10000);
  assert.equal(page.body.hidden, false);
  assert.ok(page.requests.every(({ url }) => url === '/session/activity/'));
});

test('a key press after sleep cannot revive an idle session', async () => {
  const page = tab();
  page.advance(900000, false);
  page.emit('keydown', { isTrusted: true });
  await flush();
  assert.equal(page.requests[0].url, '/logout/');
  assert.equal(page.body.hidden, true);
});

test('logout in another tab redirects without a second logout request', () => {
  const page = tab();
  page.storage.set(key, 'logout');
  page.emit('storage', { key });
  assert.equal(page.requests.length, 0);
  assert.deepEqual(page.redirects, ['/login/?expired=1']);
});

test('synthetic events do not extend the idle timeout', async () => {
  const page = tab();
  page.advance(890000);
  page.emit('pointermove', { isTrusted: false });
  page.advance(10000);
  await flush();
  assert.equal(page.requests[0].url, '/logout/');
});

test('server expiry redirects even if there was recent browser activity', async () => {
  const page = tab({ status: 401 });
  page.advance(1000);
  page.emit('pointerdown', { isTrusted: true });
  page.advance(30000);
  await flush();
  assert.deepEqual(page.redirects, ['/login/?expired=1']);
  assert.equal(page.requests.length, 1);
});

test('idle logout still works when browser storage is disabled', async () => {
  const page = tab({ storageBlocked: true });
  page.advance(900000);
  await flush();
  assert.deepEqual(page.redirects, ['/login/?expired=1']);
});

test('normal navigation and refresh do not sign the user out', () => {
  const page = tab();
  page.emit('pagehide');
  page.emit('pageshow');
  assert.equal(page.requests.length, 0);
  assert.equal(page.redirects.length, 0);
});
