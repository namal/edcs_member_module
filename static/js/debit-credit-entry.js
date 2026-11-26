// Presentation and keyboard controls only. Posting stays on the existing form.
(() => {
  const entryUrl = new URL(document.currentScript.dataset.entryUrl, location.href);
  const popupName = 'edcs-debit-credit-entry';
  const workspace = document.querySelector('[data-entry-workspace]');
  const form = workspace?.querySelector('[data-entry-tabs-form]');
  const isPopup = window.name === popupName && !!window.opener;
  let entryWindow;
  let returnFocus;

  // Follow the existing form's successful redirect in the original window.
  if (isPopup && !workspace && location.pathname !== entryUrl.pathname) {
    window.opener.postMessage(
      { type: 'edcs-entry-navigation', url: location.href },
      location.origin,
    );
    window.close();
    return;
  }

  const closeEntry = () => {
    if (isPopup) {
      window.opener.postMessage({ type: 'edcs-entry-close' }, location.origin);
      window.close();
    } else {
      const close = workspace?.querySelector('[data-entry-close]');
      if (close) location.assign(close.href);
    }
  };

  if (form) {
    document.body.classList.add('has-transaction-entry');
    if (isPopup) document.body.classList.add('transaction-entry-popup');
    const tablist = form.querySelector('[data-entry-tabs]');
    const tabs = [...tablist.querySelectorAll('[data-entry-tab]')];
    const panels = [...form.querySelectorAll('[data-entry-panel]')];
    const activate = (id, focus = false) => {
      for (const tab of tabs) {
        const selected = tab.dataset.entryTab === id;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus({ preventScroll: true });
      }
      for (const panel of panels) panel.hidden = panel.id !== id;
      form.querySelector('[data-entry-panels]').scrollTop = 0;
    };
    for (const tab of tabs) {
      const panel = panels.find((item) => item.id === tab.dataset.entryTab);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.tabIndex = 0;
      tab.addEventListener('click', () => activate(panel.id));
      tab.addEventListener('keydown', (event) => {
        const index = tabs.indexOf(tab);
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        activate(tabs[next].dataset.entryTab, true);
      });
    }
    tablist.hidden = false;
    const firstError = form.querySelector('.has-error')?.closest('[data-entry-panel]');
    activate(
      firstError?.id ||
        (form.dataset.entryBound === 'true'
          ? 'entry-panel-details'
          : tabs[0].dataset.entryTab),
    );
    let revealingInvalid = false;
    form.addEventListener(
      'invalid',
      (event) => {
        // Native validation can report several controls in one synchronous pass.
        // Reveal the first invalid control before the browser tries to focus it.
        if (revealingInvalid) return;
        const panel = event.target.closest('[data-entry-panel]');
        if (!panel) return;
        revealingInvalid = true;
        activate(panel.id);
        queueMicrotask(() => {
          revealingInvalid = false;
          event.target.focus({ preventScroll: true });
        });
      },
      true,
    );
    workspace.querySelectorAll('[data-entry-close]').forEach((link) => {
      link.addEventListener('click', (event) => {
        if (!isPopup) return;
        event.preventDefault();
        closeEntry();
      });
    });
  }

  document.addEventListener('keydown', (event) => {
    if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Escape' && form) {
      event.preventDefault();
      closeEntry();
      return;
    }
    if (event.key !== 'F2' || event.shiftKey || event.repeat) return;
    if (form) {
      event.preventDefault();
      form.querySelector('[data-entry-tab][aria-selected="true"]')?.focus();
      return;
    }
    if (!document.querySelector('[data-entry-shortcut]')) return;
    event.preventDefault();
    if (entryWindow && !entryWindow.closed) {
      entryWindow.focus();
      return;
    }
    returnFocus = document.activeElement;
    const link = document.querySelector('[data-add-entry-link]');
    const url = new URL(link?.href || entryUrl.href);
    if (!link) {
      for (const name of ['member', 'payment_type', 'child_deposit']) {
        const value = document.querySelector(`[name="${name}"]`)?.value;
        if (value) url.searchParams.set(name, value);
      }
    }
    url.searchParams.set('entry_popup', '1');
    const width = Math.min(1280, Math.max(360, screen.availWidth - 40));
    const height = Math.min(820, Math.max(480, screen.availHeight - 80));
    entryWindow = window.open(
      url.href,
      popupName,
      `popup,width=${width},height=${height},resizable=yes,scrollbars=no`,
    );
    if (entryWindow) entryWindow.focus();
    else {
      url.searchParams.delete('entry_popup');
      location.assign(url.href);
    }
  });

  window.addEventListener('message', (event) => {
    if (event.origin !== location.origin || event.source !== entryWindow) return;
    if (event.data?.type === 'edcs-entry-navigation') {
      const target = new URL(event.data.url, location.href);
      if (target.origin === location.origin) location.assign(target.href);
    } else if (event.data?.type === 'edcs-entry-close') {
      window.focus();
      returnFocus?.focus({ preventScroll: true });
    }
  });
})();
