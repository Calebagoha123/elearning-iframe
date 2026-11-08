(() => {
  const form = document.querySelector('[data-schema-editor]');
  if (!form) return;
  const host = form.querySelector('[data-builder-sections]');
  const types = JSON.parse(document.getElementById('builder-field-types').textContent);
  const schema = JSON.parse(document.getElementById('builder-schema').textContent);
  const config = JSON.parse(document.getElementById('language-editor-config').textContent);
  const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), byte => byte.toString(16).padStart(2, '0')).join('');
  const clone = value => JSON.parse(JSON.stringify(value));
  const source = config.source;
  const translations = clone(config.translations || {});
  const title = form.querySelector('[name="activity-title"]');
  const description = form.querySelector('[name="activity-description"]');
  const original = {title: title.value, description: description.value};
  const languageTabs = form.querySelector('[data-language-tabs]');
  const languagePicker = form.querySelector('[data-language-picker]');
  const openLanguage = form.querySelector('[data-open-language]');
  const addSelect = form.querySelector('[data-new-language]');
  let language = source, dirty = false;
  const sections = () => Array.from(host.children);
  const questions = section => Array.from(section.querySelector('[data-question-list]').children);
  const property = (row, kind, key) => row.querySelector(`[data-${kind}-property="${key}"]`);
  const textKeys = {section: ['title', 'instructions', 'add_label'], field: ['label', 'help_text']};
  const names = Object.fromEntries(config.languages.map(item => [item.code, item.name]));
  const variants = () => [source, ...Object.keys(translations).filter(code => code !== source)];
  const target = spec => language === source ? spec : (spec.translations ||= {})[language] ||= {};
  const displayed = (spec, key) => language === source ? spec[key] || '' : spec.translations?.[language]?.[key] || '';
  const defaultLabels = count => Array.from({length: count}, (_, i) => String(i + 1));

  const flushQuestion = row => {
    const spec = row._spec;
    textKeys.field.forEach(key => { target(spec)[key] = property(row, 'field', key).value; });
    const raw = property(row, 'field', 'options').value;
    if (language === source) {
      ['type', 'min_value'].forEach(key => { spec[key] = property(row, 'field', key).value; });
      spec.required = property(row, 'field', 'required').checked;
      spec.allow_other = property(row, 'field', 'allow_other').checked;
      const previous = new Map(spec.choices.map(choice => [choice.label, choice]));
      spec.choices = ['choice', 'multiple', 'likert'].includes(spec.type) ? raw.split('\n').map(item => item.trim()).filter(Boolean).map(label => ({...(previous.get(label) || {}), label, value: previous.get(label)?.value || uid()})) : [];
    } else if (['choice', 'multiple', 'likert'].includes(spec.type)) {
      const labels = Array.from(row.querySelectorAll('[data-choice-translation]'), input => input.value.trim());
      if (labels.length > spec.choices.length) throw new Error(`Use at most ${spec.choices.length} translated option labels, in their original order.`);
      spec.choices.forEach((choice, index) => { target(choice).label = labels[index] || ''; });
    }
  };
  const flush = () => {
    if (language === source) Object.assign(original, {title: title.value, description: description.value});
    else Object.assign(translations[language] ||= {}, {title: title.value, description: description.value});
    sections().forEach(section => {
      textKeys.section.forEach(key => { target(section._spec)[key] = property(section, 'section', key).value; });
      if (language === source) section._spec.repeatable = property(section, 'section', 'repeatable').checked;
      questions(section).forEach(flushQuestion);
    });
  };
  const readSchema = () => ({sections: sections().map(section => ({...clone(section._spec), fields: questions(section).map(row => clone(row._spec))}))});
  const missing = code => {
    if (code === source) return 0;
    let total = ['title', 'description'].filter(key => original[key] && !translations[code]?.[key]?.trim()).length;
    sections().forEach(section => {
      const spec = section._spec;
      textKeys.section.forEach(key => {
        if ((key !== 'add_label' || spec.repeatable) && spec[key] && !spec.translations?.[code]?.[key]?.trim()) total++;
      });
      questions(section).forEach(row => {
        const field = row._spec;
        textKeys.field.forEach(key => { if (field[key] && !field.translations?.[code]?.[key]?.trim()) total++; });
        field.choices.forEach(choice => { if (!choice.translations?.[code]?.label?.trim()) total++; });
      });
    });
    return total;
  };
  const share = () => {
    const code = language;
    const input = document.getElementById('embed-url');
    const url = new URL(input.dataset.baseUrl || input.value);
    input.dataset.baseUrl ||= input.value.split('?')[0];
    url.searchParams.set('lang', code);
    input.value = url.href;
    const iframe = document.createElement('iframe');
    iframe.src = url.href;
    iframe.title = code === source ? original.title : translations[code]?.title || original.title;
    iframe.width = '100%'; iframe.height = '800'; iframe.style.border = '0'; iframe.loading = 'lazy';
    document.getElementById('embed-code').value = iframe.outerHTML;
  };
  const updateLanguages = () => {
    languageTabs.replaceChildren(...variants().map(code => {
      const tab = document.createElement('button');
      tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.id = `language-tab-${code}`;
      tab.dataset.languageTab = code; tab.textContent = names[code];
      tab.setAttribute('aria-controls', 'activity-language-panel');
      tab.setAttribute('aria-selected', String(code === language));
      tab.tabIndex = code === language ? 0 : -1;
      return tab;
    }));
    form.querySelector('[data-language-panel]').setAttribute('aria-labelledby', `language-tab-${language}`);
    const selected = addSelect.value;
    addSelect.replaceChildren(...config.languages.filter(item => !variants().includes(item.code)).map(item => new Option(item.name, item.code)));
    if (Array.from(addSelect.options).some(option => option.value === selected)) addSelect.value = selected;
    openLanguage.disabled = addSelect.options.length === 0;
    updateStatus();
  };
  const updateStatus = () => {
    const count = missing(language);
    form.querySelector('[data-language-status]').textContent = language === source ? '' : `${names[language]} · ${count ? `${count} text fields left` : 'All text complete'}. Question structure is shared with ${names[source]}.`;
    form.querySelector('[data-share-title]').textContent = `${names[language]} · Moodle link`;
    form.querySelector('[data-share-status]').textContent = count ? 'Complete the empty fields and save to share this language.' : dirty ? 'Save changes to enable sharing.' : '';
    form.querySelectorAll('[data-copy]').forEach(button => { button.disabled = count > 0 || dirty; });
    const copyStatus = form.querySelector('[data-copy-status]');
    if (copyStatus) copyStatus.textContent = '';
    share();
  };
  const renderText = (row, kind) => {
    textKeys[kind].forEach(key => {
      const input = property(row, kind, key);
      input.value = displayed(row._spec, key);
      input.placeholder = language === source ? '' : row._spec[key] || '';
    });
  };
  const syncVisibility = row => {
    const type = property(row, 'field', 'type').value;
    row.querySelector('[data-choice-settings]').hidden = !['choice', 'multiple', 'likert'].includes(type);
    row.querySelector('[data-likert-settings]').hidden = type !== 'likert' || language !== source;
    row.querySelector('[data-other-setting]').hidden = type !== 'choice';
    row.querySelector('[data-numeric-settings]').hidden = !['integer', 'decimal', 'currency_eur'].includes(type);
  };
  const update = () => {
    const groups = sections(), translating = language !== source;
    form.querySelector('[data-builder-empty]').hidden = groups.length > 0;
    groups.forEach((section, index) => {
      section.querySelector('[data-section-number]').textContent = index + 1;
      section.querySelector('[data-action="section-up"]').disabled = translating || index === 0;
      section.querySelector('[data-action="section-down"]').disabled = translating || index === groups.length - 1;
      section.querySelector('[data-action="section-remove"]').disabled = translating;
      property(section, 'section', 'repeatable').disabled = translating;
      section.querySelector('[data-repeat-setting]').hidden = !property(section, 'section', 'repeatable').checked;
      const rows = questions(section);
      rows.forEach((row, index) => {
        row.querySelector('[data-question-number]').textContent = index + 1;
        row.querySelector('[data-action="question-up"]').disabled = translating || index === 0;
        row.querySelector('[data-action="question-down"]').disabled = translating || index === rows.length - 1;
        row.querySelector('[data-action="question-remove"]').disabled = translating;
        ['type', 'required', 'allow_other', 'min_value', 'points'].forEach(key => { property(row, 'field', key).disabled = translating; });
        syncVisibility(row);
      });
      section.querySelector('[data-action="question-add"]').disabled = translating || rows.length >= 200;
    });
    form.querySelector('[data-add-section]').disabled = translating || groups.length >= 30;
    form.querySelector('[data-first-question]').disabled = translating;
    updateStatus();
  };
  const showLanguage = () => {
    title.value = language === source ? original.title : translations[language]?.title || '';
    description.value = language === source ? original.description : translations[language]?.description || '';
    title.placeholder = language === source ? '' : original.title;
    description.placeholder = language === source ? '' : original.description;
    title.required = language === source;
    sections().forEach(section => {
      renderText(section, 'section');
      questions(section).forEach(row => {
        renderText(row, 'field');
        property(row, 'field', 'options').value = row._spec.choices.map(choice => displayed(choice, 'label')).join('\n');
        property(row, 'field', 'options').placeholder = language === source ? 'One option per line' : row._spec.choices.map(choice => choice.label).join('\n');
        const optionInput = property(row, 'field', 'options');
        const choiceList = row.querySelector('[data-choice-translations]');
        optionInput.hidden = language !== source;
        row.querySelector('[data-source-choice-hint]').hidden = language !== source;
        choiceList.hidden = language === source;
        choiceList.replaceChildren(...row._spec.choices.map((choice, index) => {
          const label = document.createElement('label');
          const reference = document.createElement('span');
          reference.textContent = choice.label;
          const input = document.createElement('input');
          input.dataset.choiceTranslation = choice.value; input.maxLength = 200;
          input.value = displayed(choice, 'label');
          input.placeholder = names[language];
          label.append(reference, input); return label;
        }));
      });
    });
    document.querySelectorAll('[data-language-preview]').forEach(link => {
      const url = new URL(link.href); url.searchParams.set('lang', language); link.href = url.href;
    });
    update(); updateLanguages();
  };
  const addQuestion = (section, initial = {}) => {
    const row = document.getElementById('builder-question-template').content.firstElementChild.cloneNode(true);
    row._spec = {id: uid(), version: 1, label: '', help_text: '', min_value: '', type: 'text', required: true, allow_other: false, choices: [], ...clone(initial)};
    row.dataset.id = row._spec.id;
    row.querySelector('[data-question-version]').textContent = `Version ${row._spec.version}`;
    types.forEach(type => property(row, 'field', 'type').add(new Option(type.label, type.value)));
    ['type', 'min_value'].forEach(key => { property(row, 'field', key).value = row._spec[key]; });
    ['required', 'allow_other'].forEach(key => { property(row, 'field', key).checked = !!row._spec[key]; });
    renderText(row, 'field');
    property(row, 'field', 'options').value = row._spec.choices.map(choice => displayed(choice, 'label')).join('\n');
    property(row, 'field', 'points').value = row._spec.type === 'likert' ? row._spec.choices.length : 5;
    row.querySelectorAll('[data-field-property]').forEach(input => {
      input.id = `question-${row.dataset.id}-${input.dataset.fieldProperty}`;
      const label = row.querySelector(`[data-field-label="${input.dataset.fieldProperty}"]`);
      if (label) label.htmlFor = input.id;
    });
    section.querySelector('[data-question-list]').appendChild(row);
    return row;
  };
  const addSection = (initial = {}) => {
    const section = document.getElementById('builder-section-template').content.firstElementChild.cloneNode(true);
    section._spec = {id: uid(), title: '', instructions: '', add_label: config.ui[source].add_entry || 'Add another entry', repeatable: false, ...clone(initial)};
    section.dataset.id = section._spec.id;
    renderText(section, 'section');
    property(section, 'section', 'repeatable').checked = !!section._spec.repeatable;
    section.querySelectorAll('[data-section-property]').forEach(input => {
      input.id = `section-${section.dataset.id}-${input.dataset.sectionProperty}`;
      const label = section.querySelector(`[data-section-label="${input.dataset.sectionProperty}"]`);
      if (label) label.htmlFor = input.id;
    });
    host.appendChild(section);
    (initial.fields || []).forEach(field => addQuestion(section, field));
    return section;
  };
  schema.sections.forEach(addSection);
  language = variants().includes(config.selected) ? config.selected : source;
  showLanguage();
  const switchLanguage = selected => {
    try { flush(); language = selected; showLanguage(); languageTabs.querySelector(`[data-language-tab="${selected}"]`).focus(); }
    catch (error) { alert(error.message); }
  };
  languageTabs.addEventListener('click', event => {
    const tab = event.target.closest('[data-language-tab]');
    if (tab) switchLanguage(tab.dataset.languageTab);
  });
  languageTabs.addEventListener('keydown', event => {
    const tabs = Array.from(languageTabs.children), index = tabs.indexOf(event.target);
    if (index < 0) return;
    let next;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault(); switchLanguage(next.dataset.languageTab);
  });
  const closePicker = () => {
    languagePicker.hidden = true; openLanguage.setAttribute('aria-expanded', 'false'); openLanguage.focus();
  };
  openLanguage.addEventListener('click', () => {
    const opening = languagePicker.hidden;
    languagePicker.hidden = !opening; openLanguage.setAttribute('aria-expanded', String(opening));
    if (opening) addSelect.focus();
  });
  form.querySelector('[data-cancel-language]').addEventListener('click', closePicker);
  languagePicker.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closePicker(); } });
  form.querySelector('[data-add-language]').addEventListener('click', () => {
    const selected = addSelect.value;
    if (!selected) return;
    try {
      flush(); translations[selected] = {};
      sections().forEach(section => {
        if (section._spec.translations) delete section._spec.translations[selected];
        questions(section).forEach(row => {
          if (row._spec.translations) delete row._spec.translations[selected];
          row._spec.choices.forEach(choice => { if (choice.translations) delete choice.translations[selected]; });
        });
      });
      language = selected; dirty = true;
      languagePicker.hidden = true; openLanguage.setAttribute('aria-expanded', 'false');
      showLanguage(); title.focus();
    } catch (error) { alert(error.message); }
  });
  form.querySelector('[data-add-section]').addEventListener('click', () => {
    const section = addSection(); addQuestion(section); dirty = true; update(); property(section, 'section', 'title').focus();
  });
  form.querySelector('[data-first-question]').addEventListener('click', () => {
    const row = addQuestion(addSection()); dirty = true; update(); property(row, 'field', 'label').focus();
  });
  host.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.action, section = button.closest('[data-builder-section]'), row = button.closest('[data-question]');
    const node = action.startsWith('section-') ? section : row;
    if (action === 'question-add') {
      const added = addQuestion(section); update(); property(added, 'field', 'label').focus();
    } else if (action.endsWith('remove')) node.remove();
    else if (action.endsWith('up') && node.previousElementSibling) { node.parentNode.insertBefore(node, node.previousElementSibling); button.focus(); }
    else if (action.endsWith('down') && node.nextElementSibling) { node.parentNode.insertBefore(node.nextElementSibling, node); button.focus(); }
    dirty = true; update();
  });
  let statusTimer;
  form.addEventListener('input', event => {
    if (event.target.matches('[data-new-language]')) return;
    dirty = true;
    form.querySelectorAll('[data-copy]').forEach(button => { button.disabled = true; });
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { try { flush(); updateStatus(); } catch (_) { /* Validate option count on save/switch. */ } }, 150);
  });
  host.addEventListener('change', event => {
    const row = event.target.closest('[data-question]');
    if (row && event.target.dataset.fieldProperty === 'type' && event.target.value === 'likert') {
      const count = property(row, 'field', 'options').value.split('\n').filter(item => item.trim()).length;
      if (count < 3 || count > 11) property(row, 'field', 'options').value = defaultLabels(5).join('\n');
      property(row, 'field', 'points').value = count >= 3 && count <= 11 ? count : 5;
    }
    if (row && event.target.dataset.fieldProperty === 'points') {
      const count = Math.max(3, Math.min(11, Number(event.target.value) || 5));
      event.target.value = count; property(row, 'field', 'options').value = defaultLabels(count).join('\n');
    }
    update();
  });
  form.addEventListener('submit', event => {
    clearTimeout(statusTimer);
    try {
      flush();
      form.querySelector('[name="schema"]').value = JSON.stringify(readSchema());
      form.querySelector('[name="activity-activity_translations"]').value = JSON.stringify(translations);
      let selected = form.querySelector('[name="editor_language"]');
      if (!selected) { selected = document.createElement('input'); selected.type = 'hidden'; selected.name = 'editor_language'; form.appendChild(selected); }
      selected.value = language;
      title.value = original.title; description.value = original.description;
      dirty = false;
    } catch (error) { event.preventDefault(); alert(error.message); }
  });
  window.addEventListener('pageshow', event => { if (event.persisted) showLanguage(); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
