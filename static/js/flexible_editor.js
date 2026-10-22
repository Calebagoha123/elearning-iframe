(() => {
  const form = document.querySelector('[data-schema-editor]');
  if (!form) return;
  const host = form.querySelector('[data-builder-sections]');
  const types = JSON.parse(document.getElementById('builder-field-types').textContent);
  const schema = JSON.parse(document.getElementById('builder-schema').textContent);
  const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), byte => byte.toString(16).padStart(2, '0')).join('');
  const sections = () => Array.from(host.children);
  const questions = section => Array.from(section.querySelector('[data-question-list]').children);
  const property = (row, type, name) => row.querySelector(`[data-${type}-property="${name}"]`);
  const defaultLabels = count => ({3: ['Disagree', 'Neither agree nor disagree', 'Agree'], 5: ['Strongly disagree', 'Disagree', 'Neither agree nor disagree', 'Agree', 'Strongly agree'], 7: ['Strongly disagree', 'Disagree', 'Somewhat disagree', 'Neither agree nor disagree', 'Somewhat agree', 'Agree', 'Strongly agree']}[count] || Array.from({length: count}, (_, i) => String(i+1)));
  let dirty = false;
  const syncVisibility = row => {
    const type = property(row, 'field', 'type').value;
    row.querySelector('[data-choice-settings]').hidden = !['choice', 'multiple', 'likert'].includes(type);
    row.querySelector('[data-likert-settings]').hidden = type !== 'likert';
    row.querySelector('[data-other-setting]').hidden = type !== 'choice';
    row.querySelector('[data-numeric-settings]').hidden = !['integer', 'decimal', 'currency_eur'].includes(type);
  };
  const update = () => {
    const groups = sections();
    form.querySelector('[data-builder-empty]').hidden = groups.length > 0;
    groups.forEach((section, index) => {
      section.querySelector('[data-section-number]').textContent = index + 1;
      section.querySelector('[data-action="section-up"]').disabled = index === 0;
      section.querySelector('[data-action="section-down"]').disabled = index === groups.length - 1;
      section.querySelector('[data-repeat-setting]').hidden = !property(section, 'section', 'repeatable').checked;
      const rows = questions(section);
      rows.forEach((row, qindex) => {
        row.querySelector('[data-question-number]').textContent = qindex + 1;
        row.querySelector('[data-action="question-up"]').disabled = qindex === 0;
        row.querySelector('[data-action="question-down"]').disabled = qindex === rows.length - 1;
        syncVisibility(row);
      });
      section.querySelector('[data-action="question-add"]').disabled = rows.length >= 200;
    });
    form.querySelector('[data-add-section]').disabled = groups.length >= 30;
  };
  const addQuestion = (section, spec = {}) => {
    const row = document.getElementById('builder-question-template').content.firstElementChild.cloneNode(true);
    row.dataset.id = spec.id || uid();
    row._choices = spec.choices || [];
    row._version = spec.version || 1;
    row.querySelector("[data-question-version]").textContent = `Version ${row._version}`;
    const select = property(row, 'field', 'type');
    types.forEach(type => select.add(new Option(type.label, type.value)));
    ['label', 'type', 'help_text', 'min_value'].forEach(key => { property(row, 'field', key).value = spec[key] || (key === 'type' ? 'text' : ''); });
    property(row, 'field', 'required').checked = spec.required !== false;
    property(row, 'field', 'allow_other').checked = !!spec.allow_other;
    property(row, 'field', 'options').value = row._choices.map(c => c.label).join('\n');
    property(row, 'field', 'points').value = spec.type === 'likert' ? row._choices.length : 5;
    row.querySelectorAll('[data-field-property]').forEach(input => {
      input.id = `question-${row.dataset.id}-${input.dataset.fieldProperty}`;
      const label = row.querySelector(`[data-field-label="${input.dataset.fieldProperty}"]`);
      if (label) label.htmlFor = input.id;
    });
    section.querySelector('[data-question-list]').appendChild(row);
    return row;
  };
  const addSection = (spec = {}) => {
    const section = document.getElementById('builder-section-template').content.firstElementChild.cloneNode(true);
    section.dataset.id = spec.id || uid();
    ['title', 'instructions', 'add_label'].forEach(key => { property(section, 'section', key).value = spec[key] || (key === 'add_label' ? 'Add another entry' : ''); });
    property(section, 'section', 'repeatable').checked = !!spec.repeatable;
    section.querySelectorAll('[data-section-property]').forEach(input => {
      input.id = `section-${section.dataset.id}-${input.dataset.sectionProperty}`;
      const label = section.querySelector(`[data-section-label="${input.dataset.sectionProperty}"]`);
      if (label) label.htmlFor = input.id;
    });
    host.appendChild(section);
    (spec.fields || []).forEach(field => addQuestion(section, field));
    return section;
  };
  (schema.sections || []).forEach(section => addSection(section));
  update();
  form.querySelector('[data-add-section]').addEventListener('click', () => {
    const section = addSection(); addQuestion(section); dirty = true; update();
    property(section, 'section', 'title').focus();
  });
  form.querySelector('[data-first-question]').addEventListener('click', () => {
    const row = addQuestion(addSection()); dirty = true; update(); property(row, 'field', 'label').focus();
  });
  host.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const section = button.closest('[data-builder-section]');
    const row = button.closest('[data-question]');
    const node = action.startsWith('section-') ? section : row;
    if (action === 'question-add') {
      const added = addQuestion(section); update(); property(added, 'field', 'label').focus();
    } else if (action.endsWith('remove')) {
      node.remove();
    } else if (action.endsWith('up') && node.previousElementSibling) {
      node.parentNode.insertBefore(node, node.previousElementSibling); button.focus();
    } else if (action.endsWith('down') && node.nextElementSibling) {
      node.parentNode.insertBefore(node.nextElementSibling, node); button.focus();
    }
    dirty = true; update();
  });
  form.addEventListener('input', () => { dirty = true; });
  host.addEventListener('change', event => {
    dirty = true;
    const row = event.target.closest('[data-question]');
    if (row && event.target.dataset.fieldProperty === 'type' && event.target.value === 'likert') {
      const count = property(row, 'field', 'options').value.split('\n').filter(s => s.trim()).length;
      if (count < 3 || count > 11) property(row, 'field', 'options').value = defaultLabels(5).join('\n');
      property(row, 'field', 'points').value = count >= 3 && count <= 11 ? count : 5;
    }
    if (row && event.target.dataset.fieldProperty === 'points') {
      const count = Math.max(3, Math.min(11, Number(event.target.value) || 5));
      event.target.value = count;
      property(row, 'field', 'options').value = defaultLabels(count).join('\n');
    }
    update();
  });
  const serialize = () => ({sections: sections().map(section => ({
    id: section.dataset.id, title: property(section, 'section', 'title').value,
    instructions: property(section, 'section', 'instructions').value,
    repeatable: property(section, 'section', 'repeatable').checked,
    add_label: property(section, 'section', 'add_label').value,
    fields: questions(section).map(row => {
      const spec = {id: row.dataset.id, version: row._version, required: property(row, 'field', 'required').checked, allow_other: property(row, 'field', 'allow_other').checked};
      ['label', 'type', 'help_text', 'min_value'].forEach(key => { spec[key] = property(row, 'field', key).value; });
      const previous = new Map(row._choices.map(c => [c.label, c.value]));
      spec.choices = ['choice', 'multiple', 'likert'].includes(spec.type) ? property(row, 'field', 'options').value.split('\n').map(s => s.trim()).filter(Boolean).map(label => ({label, value: previous.get(label) || uid()})) : [];
      return spec;
    })
  }))});
  form.addEventListener('submit', () => {
    form.querySelector('[name="schema"]').value = JSON.stringify(serialize());
    dirty = false;
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
