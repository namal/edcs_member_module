(() => {
  const sidebar = document.getElementById('sidebar');
  const menu = document.getElementById('mobile-menu');
  const overlay = document.getElementById('mobile-overlay');
  const toggleMenu = (open) => {
    sidebar?.classList.toggle('-translate-x-full', !open);
    overlay?.classList.toggle('hidden', !open);
    menu?.setAttribute('aria-expanded', String(open));
    if (open) sidebar?.querySelector('a')?.focus();
    else menu?.focus();
  };
  menu?.addEventListener('click', () =>
    toggleMenu(menu.getAttribute('aria-expanded') !== 'true'),
  );
  overlay?.addEventListener('click', () => toggleMenu(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menu?.getAttribute('aria-expanded') === 'true')
      toggleMenu(false);
  });
  document
    .querySelectorAll('.dismiss-message')
    .forEach((button) =>
      button.addEventListener('click', () =>
        button.closest('[role="status"]').remove(),
      ),
    );
  document
    .getElementById('print-profile')
    ?.addEventListener('click', () => window.print());

  document.querySelectorAll('[data-permission-matrix]').forEach((matrix) => {
    matrix.querySelectorAll('[data-permissions]').forEach((button) => {
      button.addEventListener('click', () => {
        matrix.querySelectorAll('input[name="module_permissions"]').forEach((input) => {
          input.checked = button.dataset.permissions === 'all';
        });
      });
    });
  });

  const fillOptions = (select, rows, label, selected = '') => {
    if (!select) return;
    select.replaceChildren(new Option(label, ''));
    rows.forEach((row) => select.add(new Option(row.name, row.id)));
    select.value = rows.some((row) => String(row.id) === String(selected))
      ? String(selected)
      : '';
  };
  const readJSON = (id) => JSON.parse(document.getElementById(id)?.textContent || '{}');
  document.querySelectorAll('[data-member-identity]').forEach((form) => {
    const fullName = form.querySelector('#id_full_name');
    const title = form.querySelector('#id_title');
    const shortName = form.querySelector('#id_short_name');
    const birth = form.querySelector('#id_date_of_birth');
    const expected = form.querySelector('#id_expected_retirement_date');
    const age = Number(form.querySelector('#id_retirement_age')?.value || 60);
    const updateName = () => {
      const parts = fullName.value.trim().split(/\s+/u).filter(Boolean);
      const name = parts.length
        ? [
            ...parts
              .slice(0, -1)
              .map((part) => `${Array.from(part)[0].toUpperCase()}.`),
            parts.at(-1),
          ].join(' ')
        : '';
      shortName.value = name ? [title.value, name].filter(Boolean).join(' ') : '';
    };
    const updateRetirement = () => {
      expected.value = '';
      if (!birth.value || !birth.validity.valid) return;
      const [birthYear, month, day] = birth.value.split('-').map(Number);
      const year = birthYear + age;
      if (year > 9999) return;
      const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
      const lastDay = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][
        month - 1
      ];
      expected.value = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(Math.min(day, lastDay)).padStart(2, '0')}`;
    };
    if (form.dataset.today) birth.max = form.dataset.today;
    fullName.addEventListener('input', updateName);
    title.addEventListener('change', updateName);
    birth.addEventListener('input', updateRetirement);
    birth.addEventListener('change', updateRetirement);
    updateName();
    updateRetirement();
  });

  document.querySelectorAll('[data-template-url]').forEach((form) => {
    const select = form.querySelector('#id_apply_template');
    const feedback = form.querySelector('[data-template-feedback]');
    const amounts = [...form.querySelectorAll('[data-monthly-amount]')];
    const submit = form.querySelector('[data-save-plan]');
    let pending;
    let loading = false;
    select.addEventListener('change', async () => {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      select.setCustomValidity('');
      if (!select.value) {
        loading = false;
        submit.disabled = !amounts.length;
        amounts.forEach((input) => (input.disabled = false));
        feedback.textContent = 'Current draft amounts kept as a custom plan.';
        return;
      }
      loading = true;
      submit.disabled = true;
      amounts.forEach((input) => (input.disabled = true));
      feedback.textContent = 'Loading template amounts…';
      try {
        const response = await fetch(
          `${form.dataset.templateUrl}?template=${encodeURIComponent(select.value)}`,
          { signal: controller.signal, headers: { Accept: 'application/json' } },
        );
        if (
          !response.ok ||
          !response.headers.get('content-type')?.includes('application/json')
        )
          throw new Error('Unable to load template');
        const data = await response.json();
        if (pending !== controller) return;
        const copied = new Map(
          data.amounts.map((item) => [String(item.payment_type_id), item.amount]),
        );
        amounts.forEach((input) => {
          input.value = copied.get(input.name.replace('amount_', '')) || '0.00';
          input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        feedback.textContent = `Copied ${data.name}. You can customize these amounts for this member.`;
      } catch (error) {
        if (pending !== controller || error.name === 'AbortError') return;
        feedback.textContent =
          'Template could not be loaded. Select it again, or choose Custom plan to keep the current amounts.';
        select.setCustomValidity(
          'Select the template again to load its amounts, or choose Custom plan.',
        );
      } finally {
        if (pending === controller) {
          loading = false;
          amounts.forEach((input) => (input.disabled = false));
          submit.disabled = !amounts.length;
        }
      }
    });
    form.addEventListener('submit', (event) => {
      if (loading) {
        event.preventDefault();
        feedback.textContent = 'Wait for the template amounts to finish loading.';
      }
    });
  });
  const searchableSelect = (select, searchTerms, labels = {}) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'school-combo';
    const input = document.createElement('input');
    input.id = `${select.id}_combo`;
    input.type = 'text';
    input.className = 'field-input school-combo-input';
    input.placeholder = labels.placeholder || 'Type or select a department…';
    input.autocomplete = 'off';
    input.required = select.required;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-required', String(select.required));
    for (const attribute of ['aria-describedby', 'aria-invalid']) {
      if (select.hasAttribute(attribute))
        input.setAttribute(attribute, select.getAttribute(attribute));
    }
    const list = document.createElement('div');
    list.id = `${select.id}_options`;
    list.className = 'school-combo-options';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', labels.plural || 'Departments');
    list.hidden = true;
    input.setAttribute('aria-controls', list.id);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'school-combo-toggle';
    toggle.tabIndex = -1;
    toggle.textContent = '▾';
    toggle.setAttribute('aria-label', `Show ${labels.plural || 'departments'}`);
    toggle.setAttribute('aria-controls', list.id);
    const status = document.createElement('span');
    status.className = 'sr-only';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    select.before(wrapper);
    wrapper.append(input, toggle, list, status, select);
    select.closest('.form-section')?.classList.add('has-school-combo');
    const label = select.closest('.form-field')?.querySelector('label');
    if (label) label.htmlFor = input.id;
    select.hidden = true;
    select.required = false;
    let query = '';
    let matches = [];
    let active = -1;
    const validate = () => {
      input.setCustomValidity(
        input.value && !select.value
          ? `Select a ${labels.singular || 'department'} from the dropdown.`
          : '',
      );
    };
    const close = () => {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    };
    const highlight = (index) => {
      active = index;
      [...list.children].forEach((option, position) => {
        option.classList.toggle('is-highlighted', position === active);
      });
      const option = list.children[active];
      if (option && matches.length) {
        input.setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({ block: 'nearest' });
      } else input.removeAttribute('aria-activedescendant');
    };
    const choose = (option) => {
      select.value = option.value;
      query = '';
      input.value = option.text;
      validate();
      close();
      select.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const renderOptions = () => {
      const term = query.trim().toLocaleLowerCase();
      matches = [...select.options].filter(
        (option) =>
          option.value &&
          `${option.text} ${searchTerms.get(option.value) || ''}`
            .toLocaleLowerCase()
            .includes(term),
      );
      list.replaceChildren();
      matches.forEach((option, index) => {
        const item = document.createElement('div');
        item.id = `${list.id}_${index}`;
        item.className = 'school-combo-option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.value === select.value));
        item.textContent = option.text;
        item.addEventListener('pointerdown', (event) => event.preventDefault());
        item.addEventListener('click', () => choose(option));
        list.append(item);
      });
      if (!matches.length) {
        const empty = document.createElement('div');
        empty.className = 'school-combo-empty';
        empty.textContent =
          labels.empty ||
          'No matching departments. Change the text, district or zonal office.';
        list.append(empty);
      }
      status.textContent = `${matches.length} ${labels.plural || 'departments'} available.`;
      highlight(
        matches.length
          ? Math.max(
              0,
              matches.findIndex((option) => option.value === select.value),
            )
          : -1,
      );
    };
    const open = () => {
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      renderOptions();
    };
    input.addEventListener('focus', () => {
      input.select();
      open();
    });
    input.addEventListener('click', open);
    input.addEventListener('input', () => {
      query = input.value;
      select.value = '';
      validate();
      open();
    });
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const wasClosed = list.hidden;
        if (wasClosed) open();
        if (matches.length)
          highlight(
            wasClosed
              ? event.key === 'ArrowDown'
                ? 0
                : matches.length - 1
              : (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) %
                  matches.length,
          );
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (!list.hidden && matches[active]) choose(matches[active]);
        else input.reportValidity();
      } else if (event.key === 'Escape' || event.key === 'Tab') close();
    });
    input.addEventListener('blur', close);
    toggle.addEventListener('pointerdown', (event) => event.preventDefault());
    toggle.addEventListener('click', () => {
      const wasClosed = list.hidden;
      input.focus();
      if (wasClosed) open();
      else close();
    });
    return {
      refresh() {
        const option = select.selectedOptions[0];
        if (select.value) query = '';
        input.value = select.value ? option.text : query;
        validate();
        if (!list.hidden) renderOptions();
      },
    };
  };
  document.querySelectorAll('[data-searchable-select]').forEach((select) => {
    if (select.disabled) return;
    const singular =
      select.dataset.searchLabel ||
      (select.name === 'member' ? 'member' : 'payment type');
    const combo = searchableSelect(select, new Map(), {
      singular,
      plural: `${singular}s`,
      placeholder: `Type or select a ${singular}…`,
      empty: `No matching ${singular}s. Change the search text.`,
    });
    combo.refresh();
    select.addEventListener('change', () => combo.refresh());
  });
  document.querySelectorAll('[data-fix-action]').forEach((form) => {
    const day = form.querySelector('[name="day"]');
    const status = form.querySelector('[data-fix-eligibility]');
    const fields = {
      deposit_balance: '[data-fix-balance]',
      loan_limit: '[data-fix-loan-limit]',
      withdrawal_limit: '[data-fix-withdrawal-limit]',
      minimum_held: '[data-fix-held]',
    };
    let pending;
    const refresh = async () => {
      pending?.abort();
      for (const selector of Object.values(fields))
        form.querySelector(selector).textContent = '—';
      if (!day.value || !day.validity.valid) {
        status.textContent = 'Choose a valid date to check eligibility.';
        return;
      }
      const controller = new AbortController();
      pending = controller;
      status.textContent = 'Checking deposit balance and eligibility…';
      const url = new URL(form.dataset.statusUrl, window.location.href);
      url.searchParams.set('date', day.value);
      try {
        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Eligibility unavailable');
        const data = await response.json();
        if (controller.signal.aborted || pending !== controller) return;
        for (const [key, selector] of Object.entries(fields))
          form.querySelector(selector).textContent = data[key];
        const reasons =
          form.dataset.fixAction === 'loan'
            ? data.loan_reasons
            : data.withdrawal_reasons;
        status.textContent = reasons.length
          ? reasons.join(' ')
          : 'Eligible within the displayed limit. Eligibility is checked again when posting.';
        form.querySelector('[name="amount"]').max =
          form.dataset.fixAction === 'loan' ? data.loan_limit : data.withdrawal_limit;
      } catch (error) {
        if (controller.signal.aborted || pending !== controller) return;
        status.textContent =
          'Unable to check eligibility. Change the date or reload the page to try again.';
      }
    };
    day.addEventListener('change', refresh);
    day.addEventListener('input', refresh);
  });
  document.querySelectorAll('[data-child-deposit-form]').forEach((form) => {
    const child = form.querySelector('[name="child"]');
    const type = form.querySelector('[name="deposit_type"]');
    const date = form.querySelector('[name="opened_on"]');
    const output = form.querySelector('[data-child-rate-preview]');
    const dates = readJSON('child-birth-dates');
    const rules = readJSON('child-rate-rules');
    const preview = () => {
      const birth = dates[child.value];
      const rule = rules[type.value];
      if (
        !birth ||
        !rule ||
        !date.value ||
        !date.validity.valid ||
        date.value < birth
      ) {
        output.textContent = 'Select a child, deposit type and valid opening date.';
        return;
      }
      const [year, month, day] = date.value.split('-').map(Number);
      const [birthYear, birthMonth, birthDay] = birth.split('-').map(Number);
      const age =
        year -
        birthYear -
        Number(month < birthMonth || (month === birthMonth && day < birthDay));
      output.textContent = `${age >= rule.age ? rule.from : rule.below}% at age ${age}. ${rule.below}% below age ${rule.age}; ${rule.from}% from age ${rule.age}.`;
    };
    for (const field of [child, type, date]) field.addEventListener('change', preview);
    form.addEventListener('input', preview);
    preview();
  });
  document.querySelectorAll('[data-child-entry-form]').forEach((form) => {
    const date = form.querySelector('[name="transaction_date"]');
    const balance = form.querySelector('[data-child-entry-balance]');
    const rate = form.querySelector('[data-child-entry-rate]');
    const age = form.querySelector('[data-child-entry-age]');
    const status = form.querySelector('[data-child-entry-status]');
    let pending;
    const refresh = async () => {
      pending?.abort();
      for (const output of [balance, rate, age]) output.textContent = '—';
      if (!date.value || !date.validity.valid) {
        status.textContent =
          'Choose a valid transaction date to show the balance and configured rate.';
        return;
      }
      const controller = new AbortController();
      pending = controller;
      const url = new URL(form.dataset.statusUrl, window.location.href);
      url.searchParams.set('date', date.value);
      if (form.dataset.entryId) url.searchParams.set('entry', form.dataset.entryId);
      status.textContent = 'Loading balance and configured rate…';
      try {
        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Balance unavailable');
        const data = await response.json();
        if (controller.signal.aborted || pending !== controller) return;
        balance.textContent = data.balance;
        rate.textContent = data.rate === null ? '—' : `${data.rate}%`;
        age.textContent = data.age === null ? '—' : String(data.age);
        status.textContent = `Saved balance as of ${data.as_of}, excluding this entry. Interest is not automatically posted.`;
      } catch (error) {
        if (controller.signal.aborted || pending !== controller) return;
        status.textContent =
          'Unable to load the balance and configured rate. Change the date or reload to try again.';
      }
    };
    date.addEventListener('change', refresh);
    date.addEventListener('input', refresh);
  });
  document.querySelectorAll('[data-interest-period-form]').forEach((form) => {
    const field = (name) => form.querySelector(`[name="${name}"]`);
    const frequency = field('interest_frequency');
    const mode = field('interest_period_type');
    const count = field('interest_interval_count');
    const unit = field('interest_interval_unit');
    const start = field('interest_start_date');
    const end = field('interest_end_date');
    const output = form.querySelector('[data-interest-period-summary]');
    const updatePeriod = () => {
      const custom = frequency.value === 'custom';
      form.querySelectorAll('[data-interest-period-control]').forEach((group) => {
        const active =
          custom &&
          (group.dataset.interestPeriodControl === 'custom' ||
            group.dataset.interestPeriodControl === mode.value);
        group.hidden = !active;
        group.querySelectorAll('input, select').forEach((input) => {
          input.disabled = !active;
          input.required = active;
        });
        group.querySelectorAll('.field-requirement').forEach((label) => {
          label.textContent = active ? '* Required' : '(optional)';
        });
      });
      end.min = start.value;
      if (!custom) {
        output.textContent =
          frequency.selectedOptions[0]?.textContent || 'Choose a frequency.';
      } else if (mode.value === 'repeating') {
        const amount = Number(count.value);
        output.textContent =
          count.value &&
          count.validity.valid &&
          Number.isInteger(amount) &&
          amount > 0 &&
          unit.value
            ? `Custom: every ${amount} ${amount === 1 ? unit.value.replace(/s$/, '') : unit.value}`
            : 'Enter an interval and choose days, months or years.';
      } else if (mode.value === 'date_range') {
        output.textContent =
          start.value && end.value && end.value >= start.value
            ? `Custom: ${start.value} to ${end.value}`
            : 'Choose a start date and an end date on or after the start date.';
      } else {
        output.textContent =
          'Choose a repeating interval or specific start and end dates.';
      }
    };
    for (const input of [frequency, mode, count, unit, start, end]) {
      input.addEventListener('change', updatePeriod);
      input.addEventListener('input', updatePeriod);
    }
    updatePeriod();
  });
  document.querySelectorAll('[data-transaction-form]').forEach((form) => {
    const select = form.querySelector('[name="payment_type"]');
    const output = form.querySelector('[data-interest-rate]');
    const rates = readJSON('payment-type-rates');
    const frequency = form.querySelector('[data-interest-frequency]');
    const frequencies = readJSON('payment-type-frequencies');
    const showRate = () => {
      output.textContent = Object.hasOwn(rates, select.value)
        ? `${rates[select.value]}%`
        : '—';
      if (frequency) frequency.textContent = frequencies[select.value] || '—';
    };
    select.addEventListener('change', showRate);
    form.addEventListener('input', (event) => {
      if (event.target.id === `${select.id}_combo`) showRate();
    });
    showRate();

    const panel = form.querySelector('[data-balance-panel]');
    if (!panel) return;
    const member = form.querySelector('[name="member"]');
    const date = form.querySelector('[name="transaction_date"]');
    const status = panel.querySelector('[data-balance-status]');
    const content = panel.querySelector('[data-balance-content]');
    const total = panel.querySelector('[data-member-balance]');
    const selectedBalance = panel.querySelector('[data-payment-type-balance]');
    const rows = panel.querySelector('[data-balance-rows]');
    let balanceData = readJSON('initial-member-balances');
    let pendingBalance;
    const renderBalances = () => {
      if (!balanceData) return;
      content.hidden = false;
      total.textContent = balanceData.total;
      const selected = balanceData.balances.find(
        (row) => String(row.payment_type_id) === select.value,
      );
      selectedBalance.textContent = select.value ? selected?.balance || '0.00' : '—';
      rows.replaceChildren();
      for (const row of balanceData.balances) {
        const tr = document.createElement('tr');
        if (String(row.payment_type_id) === select.value)
          tr.className = 'bg-brand-50 font-semibold';
        const name = document.createElement('td');
        name.textContent = row.name + (row.is_active ? '' : ' (inactive)');
        const amount = document.createElement('td');
        amount.textContent = row.balance;
        tr.append(name, amount);
        rows.append(tr);
      }
      status.textContent =
        `Balances as of ${balanceData.as_of}.` +
        (balanceData.has_records
          ? ''
          : ' No saved opening amounts or transactions for this date.');
    };
    const loadBalances = async () => {
      pendingBalance?.abort();
      balanceData = null;
      content.hidden = true;
      total.textContent = '—';
      selectedBalance.textContent = '—';
      rows.replaceChildren();
      if (!member.value) {
        status.textContent = 'Select a member to see their balance.';
        return;
      }
      if (!date.value || !date.validity.valid) {
        status.textContent = 'Choose a valid transaction date to see the balance.';
        return;
      }
      const controller = new AbortController();
      pendingBalance = controller;
      status.textContent = 'Loading member balance…';
      const url = new URL(form.dataset.balancesUrl, window.location.href);
      url.searchParams.set('member', member.value);
      url.searchParams.set('date', date.value);
      if (form.dataset.entryId) url.searchParams.set('entry', form.dataset.entryId);
      try {
        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Balance unavailable');
        const data = await response.json();
        if (controller.signal.aborted || pendingBalance !== controller) return;
        balanceData = data;
        renderBalances();
      } catch (error) {
        if (controller.signal.aborted || pendingBalance !== controller) return;
        status.textContent =
          'Unable to load the member balance. Reselect the member or reload the page to try again.';
      }
    };
    member.addEventListener('change', loadBalances);
    date.addEventListener('change', loadBalances);
    select.addEventListener('change', renderBalances);
    form.addEventListener('input', (event) => {
      if (event.target.id === `${member.id}_combo`) loadBalances();
      if (event.target.id === `${select.id}_combo`) renderBalances();
    });
    if (balanceData) renderBalances();
    else loadBalances();
  });
  document.querySelectorAll('[data-location-form]').forEach((form) => {
    const district = form.querySelector('#id_district');
    const province = form.querySelector('#id_province');
    const provinceShortName = form.querySelector('#id_province_short_name');
    const office = form.querySelector(`#id_${form.dataset.officeField}`);
    const subdivision = form.querySelector('#id_subdivision');
    const school = form.querySelector('#id_school');
    const lager = form.querySelector('#id_lager');
    const officer = form.querySelector('#id_maintenance_officer');
    const officerEpf = form.querySelector('#id_officer_epf');
    const officerData = readJSON('ledger-officers');
    const allOfficers = Array.isArray(officerData) ? officerData : [];
    const showOfficerEpf = () => {
      if (officerEpf)
        officerEpf.value =
          allOfficers.find((row) => String(row.id) === officer?.value)?.epf_number ||
          '';
    };
    const assignLagerOfficer = () => {
      if (!officer || !lager) return;
      const selected = (locations.lagers || []).find(
        (row) => String(row.id) === lager.value,
      );
      officer.value = selected?.maintenance_officer_id
        ? String(selected.maintenance_officer_id)
        : '';
      showOfficerEpf();
    };
    officer?.addEventListener('change', () => {
      if (lager?.value) assignLagerOfficer();
      else showOfficerEpf();
    });
    lager?.addEventListener('change', assignLagerOfficer);
    const feedback = form.querySelector('#office-feedback');
    const provinces = readJSON('district-provinces');
    const provinceShortNames = readJSON('district-province-short-names');
    const schools = school ? readJSON('member-schools') : [];
    const schoolCombo = school
      ? searchableSelect(
          school,
          new Map(schools.map((row) => [String(row.id), row.code])),
        )
      : null;
    let locations = readJSON('initial-locations');
    let pending;
    let loading = false;
    const say = (message) => {
      if (feedback) feedback.textContent = message;
    };
    const admitDate = form.querySelector('#id_admit_date');
    if (admitDate && form.dataset.today) admitDate.max = form.dataset.today;
    const updateProvince = () => {
      if (province) province.value = provinces[district.value] || '';
      if (provinceShortName)
        provinceShortName.value = provinceShortNames[district.value] || '';
    };
    const updateSchool = (selected = school?.value || '') => {
      if (!school) return;
      const rows = schools.filter(
        (row) =>
          (!district.value || String(row.district_id) === district.value) &&
          (!office.value || String(row.zonal_office_id) === office.value),
      );
      fillOptions(
        school,
        rows,
        rows.length ? 'Select a department' : 'No matching departments',
        selected,
      );
      schoolCombo.refresh();
    };
    const updateSubdivisions = (selected = '') => {
      const rows = (locations.subdivisions || []).filter(
        (row) => String(row.zonal_office_id) === office.value,
      );
      fillOptions(subdivision, rows, 'No subdivision', selected);
    };
    const updateLagers = (selected = '') => {
      const lagers = (locations.lagers || [])
        .filter((row) => String(row.zonal_office_id) === office.value)
        .map((row) => ({
          ...row,
          name: `${row.code} · ${row.name}`,
        }));
      fillOptions(
        lager,
        lagers,
        office.value ? 'Select a lager' : 'Select a zonal office first',
        selected,
      );
    };
    const render = (selection = {}) => {
      fillOptions(
        office,
        locations.offices || [],
        district.value
          ? 'Select a zonal office'
          : 'Select a department or district first',
        selection.office,
      );
      updateLagers(selection.lager);
      fillOptions(
        officer,
        allOfficers,
        'Select a Ledger Maintenance Officer',
        selection.officer,
      );
      if (lager) assignLagerOfficer();
      else showOfficerEpf();
      updateSubdivisions(selection.subdivision);
      updateSchool(selection.school);
      say(
        district.value && !locations.offices?.length
          ? 'No active zonal offices in this district. Add a zonal office under Master data.'
          : '',
      );
    };
    updateProvince();
    render({
      office: office.value,
      subdivision: subdivision?.value,
      school: school?.value,
      lager: lager?.value,
      officer: officer?.value,
    });
    const loadDistrict = async (selection = {}) => {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      const previousLager = (locations.lagers || []).find(
        (row) => String(row.id) === String(selection.lager),
      );
      updateProvince();
      locations = { offices: [], subdivisions: [], schools: [], lagers: [] };
      render({ school: selection.school });
      office.setCustomValidity('');
      if (!district.value) {
        loading = false;
        office.disabled = false;
        if (lager) lager.disabled = false;
        return false;
      }
      loading = true;
      office.disabled = true;
      if (lager) lager.disabled = true;
      say('Loading locations…');
      try {
        const response = await fetch(
          `${form.dataset.locationUrl}?district=${encodeURIComponent(district.value)}${form.dataset.locationPurpose === 'transfer' ? '&purpose=transfer' : ''}`,
          { signal: controller.signal, headers: { Accept: 'application/json' } },
        );
        if (
          !response.ok ||
          !response.headers.get('content-type')?.includes('application/json')
        )
          throw new Error('Unable to load locations');
        const data = await response.json();
        if (pending !== controller) return false;
        const selectedSchool = schools.find(
          (row) => String(row.id) === String(selection.school),
        );
        // Existing inactive selections remain available when editing a member.
        if (
          selectedSchool &&
          !data.offices.some((row) => row.id === selectedSchool.zonal_office_id)
        ) {
          data.offices.push({
            id: selectedSchool.zonal_office_id,
            name: selectedSchool.zonal_office_name,
            address: selectedSchool.office_address,
            phone: selectedSchool.phone,
          });
        }
        if (
          selectedSchool?.subdivision_id &&
          !data.subdivisions.some((row) => row.id === selectedSchool.subdivision_id)
        ) {
          data.subdivisions.push({
            id: selectedSchool.subdivision_id,
            name: selectedSchool.subdivision_name,
            zonal_office_id: selectedSchool.zonal_office_id,
          });
        }
        if (
          previousLager &&
          String(previousLager.zonal_office_id) === String(selection.office) &&
          !data.lagers.some((row) => row.id === previousLager.id)
        )
          data.lagers.push(previousLager);
        locations = data;
        render({
          ...selection,
          school:
            selection.school && school.value !== String(selection.school)
              ? school.value
              : selection.school,
        });
        return true;
      } catch (error) {
        if (pending !== controller || error.name === 'AbortError') return false;
        say(
          'Locations could not be loaded. Check your connection and select the department or district again.',
        );
        office.setCustomValidity(
          'Select the department or district again to load its locations.',
        );
        return false;
      } finally {
        if (pending === controller) {
          loading = false;
          office.disabled = false;
          if (lager) lager.disabled = false;
        }
      }
    };
    district.addEventListener('change', () => loadDistrict());
    office.addEventListener('change', () => {
      fillOptions(officer, allOfficers, 'Select a Ledger Maintenance Officer');
      updateLagers();
      showOfficerEpf();
      updateSubdivisions();
      updateSchool();
    });
    subdivision?.addEventListener('change', () => {
      const selectedSchool = schools.find((row) => String(row.id) === school?.value);
      if (
        selectedSchool &&
        subdivision.value &&
        String(selectedSchool.subdivision_id || '') !== subdivision.value
      )
        updateSchool('');
    });
    school?.addEventListener('change', () => {
      const row = schools.find((item) => String(item.id) === school.value);
      if (!row) {
        pending?.abort();
        pending = undefined;
        loading = false;
        office.disabled = false;
        if (lager) lager.disabled = false;
        return;
      }
      const selectedLager =
        office.value === String(row.zonal_office_id) ? lager?.value : '';
      const selectedOfficer =
        office.value === String(row.zonal_office_id) ? officer?.value : '';
      district.value = String(row.district_id);
      loadDistrict({
        office: row.zonal_office_id,
        subdivision: row.subdivision_id || '',
        school: row.id,
        lager: selectedLager,
        officer: selectedOfficer,
      });
    });
    form.addEventListener('submit', (event) => {
      if (loading) {
        event.preventDefault();
        say('Please wait for the locations to finish loading.');
      }
    });
  });

  document.querySelectorAll('[data-office-target]').forEach((district) => {
    let pending;
    const target = document.getElementById(district.dataset.officeTarget);
    if (!target) return;
    district.addEventListener('change', async () => {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      target.disabled = true;
      fillOptions(target, [], 'Loading zonal offices…');
      try {
        const response = await fetch(
          `${district.dataset.officeUrl}?district=${encodeURIComponent(district.value)}`,
          { signal: controller.signal, headers: { Accept: 'application/json' } },
        );
        if (
          !response.ok ||
          !response.headers.get('content-type')?.includes('application/json')
        )
          throw new Error('Unable to load offices');
        const data = await response.json();
        if (pending === controller)
          fillOptions(
            target,
            data.offices,
            district.dataset.allLabel || 'All zonal offices',
          );
      } catch (error) {
        if (pending === controller && error.name !== 'AbortError')
          fillOptions(
            target,
            [],
            'Could not load zonal offices — select district again',
          );
      } finally {
        if (pending === controller) target.disabled = false;
      }
    });
  });

  const directoryDistrict = document.getElementById('filter-district');
  const directoryOffice = document.getElementById('filter-office');
  const directoryLager = document.getElementById('filter-lager');
  if (directoryDistrict && directoryLager) {
    const lagers = readJSON('directory-lagers');
    const updateDirectoryLagers = () => {
      const rows = lagers
        .filter(
          (row) =>
            (!directoryDistrict.value ||
              String(row.district_id) === directoryDistrict.value) &&
            (!directoryOffice?.value ||
              String(row.zonal_office_id) === directoryOffice.value),
        )
        .map((row) => ({
          ...row,
          name: `${row.code} · ${row.name} · ${row.zonal_office__name} · ${row.district__name}`,
        }));
      fillOptions(directoryLager, rows, 'All lagers');
    };
    directoryDistrict.addEventListener('change', () => {
      if (directoryOffice) directoryOffice.value = '';
      updateDirectoryLagers();
    });
    directoryOffice?.addEventListener('change', updateDirectoryLagers);
  }

  document
    .querySelectorAll('[data-payment-plan-form], [data-opening-balance-form]')
    .forEach((form) => {
      const amounts = [
        ...form.querySelectorAll('[data-monthly-amount], [data-balance-amount]'),
      ];
      const total = form.querySelector('#monthly-plan-total, #opening-balance-total');
      const formatter = new Intl.NumberFormat('en-LK', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
      const updateTotal = () => {
        const cents = amounts.reduce((sum, input) => {
          const value = Number(input.value);
          return (
            sum + (Number.isFinite(value) && value >= 0 ? Math.round(value * 100) : 0)
          );
        }, 0);
        total.textContent = formatter.format(cents / 100);
      };
      amounts.forEach((input) => input.addEventListener('input', updateTotal));
      updateTotal();
    });

  document.querySelectorAll('[data-approval-hierarchy]').forEach((form) => {
    const moduleSelect = form.querySelector('[data-approval-module]');
    const actionSelect = form.querySelector('[data-approval-action]');
    const updateApprovalAction = () => {
      const allowed = ['membertransfer', 'paymentexception'].includes(
        moduleSelect.value,
      )
        ? 'add'
        : 'change';
      for (const option of actionSelect.options)
        option.disabled = option.value !== allowed;
      actionSelect.value = allowed;
    };
    moduleSelect?.addEventListener('change', updateApprovalAction);
    if (moduleSelect && actionSelect) updateApprovalAction();
    const add = form.querySelector('[data-add-approval-level]');
    const total = form.querySelector('[name="levels-TOTAL_FORMS"]');
    const levels = form.querySelector('[data-approval-levels]');
    const template = form.querySelector('[data-approval-level-template]');
    add?.addEventListener('click', () => {
      const index = Number(total.value);
      if (index >= 20) return;
      const fragment = document.createElement('template');
      fragment.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index));
      levels.append(fragment.content.cloneNode(true));
      total.value = String(index + 1);
      add.disabled = index + 1 >= 20;
      levels.lastElementChild?.querySelector('input[type="number"]')?.focus();
    });
  });

  document.querySelectorAll('[data-submit-label]').forEach((button) => {
    button.form?.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return;
      if (event.submitter === button && button.name) {
        const value = document.createElement('input');
        value.type = 'hidden';
        value.name = button.name;
        value.value = button.value;
        button.form.append(value);
      }
      button.disabled = true;
      button.textContent = button.dataset.submitLabel;
    });
  });
})();
