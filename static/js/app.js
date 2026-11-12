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
  const searchableSelect = (select, searchTerms) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'school-combo';
    const input = document.createElement('input');
    input.id = `${select.id}_combo`;
    input.type = 'text';
    input.className = 'field-input school-combo-input';
    input.placeholder = 'Type or select a school…';
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
    list.setAttribute('aria-label', 'Schools');
    list.hidden = true;
    input.setAttribute('aria-controls', list.id);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'school-combo-toggle';
    toggle.tabIndex = -1;
    toggle.textContent = '▾';
    toggle.setAttribute('aria-label', 'Show schools');
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
        input.value && !select.value ? 'Select a school from the dropdown.' : '',
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
          'No matching schools. Change the text, district or zonal office.';
        list.append(empty);
      }
      status.textContent = `${matches.length} school${matches.length === 1 ? '' : 's'} available.`;
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
  document.querySelectorAll('[data-location-form]').forEach((form) => {
    const district = form.querySelector('#id_district');
    const province = form.querySelector('#id_province');
    const provinceShortName = form.querySelector('#id_province_short_name');
    const office = form.querySelector(`#id_${form.dataset.officeField}`);
    const subdivision = form.querySelector('#id_subdivision');
    const school = form.querySelector('#id_school');
    const lager = form.querySelector('#id_lager');
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
        rows.length ? 'Select a school' : 'No matching schools',
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
    const render = (selection = {}) => {
      const lagers = (locations.lagers || []).map((row) => ({
        ...row,
        name: `${row.code} · ${row.name}`,
      }));
      fillOptions(
        lager,
        lagers,
        district.value ? 'Select a lager' : 'Select a school or district first',
        selection.lager,
      );
      fillOptions(
        office,
        locations.offices || [],
        district.value ? 'Select a zonal office' : 'Select a school or district first',
        selection.office,
      );
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
          `${form.dataset.locationUrl}?district=${encodeURIComponent(district.value)}`,
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
        if (previousLager && !data.lagers.some((row) => row.id === previousLager.id))
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
          'Locations could not be loaded. Check your connection and select the school or district again.',
        );
        office.setCustomValidity(
          'Select the school or district again to load its locations.',
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
    const autofill = (input, value) => {
      if (input && (!input.value || input.value === input.dataset.autofilled)) {
        input.value = value || '';
        input.dataset.autofilled = input.value;
      }
    };
    office.addEventListener('change', () => {
      updateSubdivisions();
      updateSchool();
      const row = (locations.offices || []).find(
        (item) => String(item.id) === office.value,
      );
      autofill(form.querySelector('#id_office_address'), row?.address);
      autofill(form.querySelector('#id_phone_office'), row?.phone);
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
    school?.addEventListener('change', async () => {
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
        district.value === String(row.district_id) ? lager?.value : '';
      district.value = String(row.district_id);
      const loaded = await loadDistrict({
        office: row.zonal_office_id,
        subdivision: row.subdivision_id || '',
        school: row.id,
        lager: selectedLager,
      });
      if (!loaded || school.value !== String(row.id)) return;
      autofill(
        form.querySelector('#id_office_address'),
        row.address || row.office_address,
      );
      autofill(form.querySelector('#id_phone_office'), row.phone);
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
  const directoryLager = document.getElementById('filter-lager');
  if (directoryDistrict && directoryLager) {
    const lagers = readJSON('directory-lagers');
    directoryDistrict.addEventListener('change', () => {
      const rows = lagers
        .filter(
          (row) =>
            !directoryDistrict.value ||
            String(row.district_id) === directoryDistrict.value,
        )
        .map((row) => ({
          ...row,
          name: `${row.code} · ${row.name} · ${row.district__name}`,
        }));
      fillOptions(directoryLager, rows, 'All lagers');
    });
  }

  document.querySelectorAll('[data-submit-label]').forEach((button) => {
    button.form?.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return;
      button.disabled = true;
      button.textContent = button.dataset.submitLabel;
    });
  });
})();
