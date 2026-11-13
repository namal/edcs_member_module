(() => {
  const element = document.getElementById('session-policy');
  if (!element) return;
  const policy = JSON.parse(element.textContent);
  const timeout = policy.timeout * 1000;
  const key = `edcs-session-${policy.scope}`;
  const heartbeatInterval = Math.min(30000, timeout / 3);
  let lastActivity = Date.now();
  let lastSent = lastActivity;
  let lastWritten = 0;
  let ending = false;

  const read = () => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  };
  const write = (value) => {
    try {
      localStorage.setItem(key, String(value));
    } catch {
      // The current tab still expires when browser storage is unavailable.
    }
  };
  const redirect = () => location.replace(`${policy.loginUrl}?expired=1`);
  const post = (url) =>
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': policy.csrfToken },
      keepalive: true,
      cache: 'no-store',
    });

  const expire = (notifyServer = true) => {
    if (ending) return;
    ending = true;
    document.body.hidden = true;
    write('logout');
    if (notifyServer) {
      // Finish logout even when navigation starts; don't leave an offline page visible.
      setTimeout(redirect, 2000);
      post(policy.logoutUrl)
        .catch(() => {})
        .finally(redirect);
    } else {
      redirect();
    }
  };

  const check = () => {
    if (ending) return false;
    const shared = read();
    if (shared === 'logout') {
      expire(false);
      return false;
    }
    lastActivity = Math.max(lastActivity, Number(shared) || 0);
    if (Date.now() - lastActivity >= timeout) {
      expire();
      return false;
    }
    return true;
  };

  const activity = (event) => {
    if (!event.isTrusted || !check()) return;
    lastActivity = Date.now();
    if (lastActivity - lastWritten >= 1000) {
      write(lastActivity);
      lastWritten = lastActivity;
    }
  };

  if (!check()) return;
  write(lastActivity);
  for (const event of [
    'pointerdown',
    'pointermove',
    'keydown',
    'touchstart',
    'scroll',
  ]) {
    document.addEventListener(event, activity, { passive: true, capture: true });
  }
  window.addEventListener('storage', (event) => {
    if (event.key === key) check();
  });
  window.addEventListener('pageshow', check);
  document.addEventListener('visibilitychange', check);
  document.addEventListener('submit', (event) => {
    if (event.target.action === new URL(policy.logoutUrl, location.href).href) {
      write('logout');
    }
  });

  setInterval(
    () => {
      if (!check()) return;
      const now = Date.now();
      // Polling alone must never keep an unattended session alive.
      if (lastActivity <= lastSent || now - lastSent < heartbeatInterval) return;
      lastSent = now;
      post(policy.activityUrl)
        .then((response) => {
          if (response.status === 401 || response.status === 403) expire(false);
        })
        .catch(() => {});
    },
    Math.min(1000, heartbeatInterval),
  );
})();
