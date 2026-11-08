(() => {
  const form = document.querySelector('[data-flexible-response]');
  if (!form) return;
  const ui = JSON.parse(document.getElementById('student-ui-copy').textContent);
  const syncOther = row => row.querySelectorAll('[data-other-answer]').forEach(other => {
    const field = other.closest('.flexible-answer');
    other.hidden = field.querySelector('select')?.value !== '__other__';
  });
  form.querySelectorAll('[data-response-section]').forEach(section => {
    const prefix = section.dataset.prefix;
    const list = section.querySelector('[data-entry-list]');
    const total = section.querySelector(`[name="${prefix}-TOTAL_FORMS"]`);
    const reindex = () => {
      Array.from(list.children).forEach((row, index) => {
        const number = row.querySelector('[data-entry-number]');
        if (number) number.textContent = index + 1;
        row.querySelectorAll('[name], [id], [for], [aria-describedby]').forEach(node => {
          ['name', 'id', 'for', 'aria-describedby'].forEach(attr => {
            if (node.hasAttribute(attr)) node.setAttribute(attr, node.getAttribute(attr).replace(new RegExp(`${prefix}-(\\d+|__prefix__)-`, 'g'), `${prefix}-${index}-`));
          });
        });
        row.querySelector('[data-entry-add]')?.toggleAttribute('hidden', index !== list.children.length - 1);
        syncOther(row);
      });
      total.value = list.children.length;
      section.querySelectorAll('[data-add-entry]').forEach(add => { add.disabled = list.children.length >= 1000; });
      section.querySelector('[data-empty-actions]')?.toggleAttribute('hidden', list.children.length > 0);
    };
    section.addEventListener('click', event => {
      const add = event.target.closest('[data-add-entry]');
      if (add && !add.disabled) {
        list.appendChild(section.querySelector('[data-entry-template]').content.cloneNode(true));
        reindex(); list.lastElementChild.querySelector('input:not([type="hidden"]), select, textarea')?.focus();
        return;
      }
      const button = event.target.closest('[data-remove-entry]');
      if (!button) return;
      const row = button.closest('[data-entry-row]');
      const nextFocus = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      reindex();
      (nextFocus?.querySelector('[data-remove-entry]') || section.querySelector('[data-empty-add]'))?.focus();
    });
    reindex();
  });
  form.addEventListener('change', event => {
    const row = event.target.closest('[data-entry-row]');
    if (row) syncOther(row);
  });
  const pending = new WeakMap();
  form.addEventListener('input', event => {
    const input = event.target.closest('[data-place-input]');
    if (!input) return;
    const field = input.closest('[data-place-field]');
    ['osm_type', 'osm_id', 'display_name'].forEach(suffix => { field.querySelector(`[name$="__${suffix}"]`).value = ''; });
    const previous = pending.get(input);
    if (previous) { clearTimeout(previous.timer); previous.controller.abort(); }
    const results = field.querySelector('[data-place-results]');
    const status = field.querySelector('[data-place-status]');
    results.replaceChildren(); results.hidden = true;
    const query = input.value.trim();
    if (query.length < 3) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        status.textContent = ui.searching;
        const url = new URL('https://nominatim.openstreetmap.org/search');
        url.search = new URLSearchParams({q: query, format: 'json', limit: '5'});
        const response = await fetch(url, {signal: controller.signal});
        if (!response.ok) throw new Error('Search unavailable');
        const matches = await response.json();
        if (input.value.trim() !== query) return;
        results.replaceChildren();
        matches.forEach(match => {
          const item = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'place-result-button'; button.textContent = match.display_name;
          button.addEventListener('click', () => {
            input.value = match.display_name;
            ['osm_type', 'osm_id', 'display_name'].forEach(suffix => { field.querySelector(`[name$="__${suffix}"]`).value = match[suffix]; });
            results.hidden = true; status.textContent = ui.place_selected; input.focus();
          });
          item.appendChild(button); results.appendChild(item);
        });
        results.hidden = !matches.length;
        status.textContent = matches.length ? ui.choose_place : ui.no_places;
      } catch (error) {
        if (error.name !== 'AbortError') status.textContent = ui.search_unavailable;
      }
    }, 350);
    pending.set(input, {timer, controller});
  });
})();
