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
  menu?.addEventListener('click', () => toggleMenu(menu.getAttribute('aria-expanded') !== 'true'));
  overlay?.addEventListener('click', () => toggleMenu(false));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && menu?.getAttribute('aria-expanded') === 'true') toggleMenu(false); });
  document.querySelectorAll('.dismiss-message').forEach((button) => button.addEventListener('click', () => button.closest('[role="status"]').remove()));
  document.getElementById('print-profile')?.addEventListener('click', () => window.print());

  const fillOptions = (select, rows, label, selected = '') => {
    if (!select) return;
    select.replaceChildren(new Option(label, ''));
    rows.forEach((row) => select.add(new Option(row.name, row.id)));
    select.value = rows.some((row) => String(row.id) === String(selected)) ? String(selected) : '';
  };
  const readJSON = (id) => JSON.parse(document.getElementById(id)?.textContent || '{}');
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
    let locations = readJSON('initial-locations');
    let pending;
    let loading = false;
    const say = (message) => { if (feedback) feedback.textContent = message; };
    const admitDate = form.querySelector('#id_admit_date');
    if (admitDate && form.dataset.today) admitDate.max = form.dataset.today;
    const updateProvince = () => {
      if (province) province.value = provinces[district.value] || '';
      if (provinceShortName) provinceShortName.value = provinceShortNames[district.value] || '';
    };
    const updateSchool = (selected = '') => fillOptions(school, schools, 'Select a school', selected);
    const updateSubdivisions = (selected = '') => {
      const rows = (locations.subdivisions || []).filter((row) => String(row.zonal_office_id) === office.value);
      fillOptions(subdivision, rows, 'No subdivision', selected);
    };
    const render = (selection = {}) => {
      const lagers = (locations.lagers || []).map((row) => ({...row, name: `${row.code} · ${row.name}`}));
      fillOptions(lager, lagers, district.value ? 'Select a lager' : 'Select a school or district first', selection.lager);
      fillOptions(office, locations.offices || [], district.value ? 'Select a zonal office' : 'Select a school or district first', selection.office);
      updateSubdivisions(selection.subdivision);
      updateSchool(selection.school);
      say(district.value && !locations.offices?.length ? 'No active zonal offices in this district. Add a zonal office under Master data.' : '');
    };
    updateProvince();
    render({office: office.value, subdivision: subdivision?.value, school: school?.value, lager: lager?.value});
    const loadDistrict = async (selection = {}) => {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      const previousLager = (locations.lagers || []).find((row) => String(row.id) === String(selection.lager));
      updateProvince();
      locations = {offices: [], subdivisions: [], schools: [], lagers: []};
      render({school: selection.school});
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
        const response = await fetch(`${form.dataset.locationUrl}?district=${encodeURIComponent(district.value)}`, {signal: controller.signal, headers: {'Accept': 'application/json'}});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Unable to load locations');
        const data = await response.json();
        if (pending !== controller) return false;
        const selectedSchool = schools.find((row) => String(row.id) === String(selection.school));
        // Existing inactive selections remain available when editing a member.
        if (selectedSchool && !data.offices.some((row) => row.id === selectedSchool.zonal_office_id)) {
          data.offices.push({id: selectedSchool.zonal_office_id, name: selectedSchool.zonal_office_name, address: selectedSchool.office_address, phone: selectedSchool.phone});
        }
        if (selectedSchool?.subdivision_id && !data.subdivisions.some((row) => row.id === selectedSchool.subdivision_id)) {
          data.subdivisions.push({id: selectedSchool.subdivision_id, name: selectedSchool.subdivision_name, zonal_office_id: selectedSchool.zonal_office_id});
        }
        if (previousLager && !data.lagers.some((row) => row.id === previousLager.id)) data.lagers.push(previousLager);
        locations = data;
        render(selection);
        return true;
      } catch (error) {
        if (pending !== controller || error.name === 'AbortError') return false;
        say('Locations could not be loaded. Check your connection and select the school or district again.');
        office.setCustomValidity('Select the school or district again to load its locations.');
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
      const row = (locations.offices || []).find((item) => String(item.id) === office.value);
      autofill(form.querySelector('#id_office_address'), row?.address);
      autofill(form.querySelector('#id_phone_office'), row?.phone);
    });
    subdivision?.addEventListener('change', () => {
      const selectedSchool = schools.find((row) => String(row.id) === school?.value);
      if (selectedSchool && subdivision.value && String(selectedSchool.subdivision_id || '') !== subdivision.value) updateSchool();
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
      const selectedLager = district.value === String(row.district_id) ? lager?.value : '';
      district.value = String(row.district_id);
      const loaded = await loadDistrict({office: row.zonal_office_id, subdivision: row.subdivision_id || '', school: row.id, lager: selectedLager});
      if (!loaded || school.value !== String(row.id)) return;
      autofill(form.querySelector('#id_office_address'), row.address || row.office_address);
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
        const response = await fetch(`${district.dataset.officeUrl}?district=${encodeURIComponent(district.value)}`, {signal: controller.signal, headers: {'Accept': 'application/json'}});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Unable to load offices');
        const data = await response.json();
        if (pending === controller) fillOptions(target, data.offices, district.dataset.allLabel || 'All zonal offices');
      } catch (error) {
        if (pending === controller && error.name !== 'AbortError') fillOptions(target, [], 'Could not load zonal offices — select district again');
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
      const rows = lagers.filter((row) => !directoryDistrict.value || String(row.district_id) === directoryDistrict.value)
        .map((row) => ({...row, name: `${row.code} · ${row.name} · ${row.district__name}`}));
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
