(function (root) {
  const cents = (value) => {
    const text = String(value).trim();
    if (text === '') return 0;
    if (!/^(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(text)) return null;
    const [whole, fraction = ''] = text.split('.');
    const amount = Number(whole || 0) * 100 + Number(fraction.padEnd(2, '0'));
    return Number.isSafeInteger(amount) && amount <= 999999999999 ? amount : null;
  };
  const calculate = (received, standardAmounts, accountAmounts) => {
    const receivedCents = cents(received);
    const standard = standardAmounts.map(cents);
    const accounts = accountAmounts.map(cents);
    if ([receivedCents, ...standard, ...accounts].includes(null)) {
      return {
        valid: false,
        standard: null,
        available: null,
        allocated: null,
        remaining: null,
        rowBalances: accounts.map(() => null),
        limits: accounts.map(() => null),
      };
    }
    const standardTotal = standard.reduce((sum, amount) => sum + amount, 0);
    const available = receivedCents - standardTotal;
    const allocated = accounts.reduce((sum, amount) => sum + amount, 0);
    let running = available;
    return {
      valid: true,
      standard: standardTotal,
      available,
      allocated,
      remaining: available - allocated,
      rowBalances: accounts.map((amount) => (running -= amount)),
      limits: accounts.map((amount) => available - allocated + amount),
    };
  };
  const api = { calculate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ContributionBalances = api;
})(typeof window === 'undefined' ? globalThis : window);
