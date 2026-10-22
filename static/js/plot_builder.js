(() => {
  const form = document.querySelector('[data-plot-builder]');
  if (!form) return;
  const fields = JSON.parse(document.getElementById('plot-field-catalog').textContent);
  const get = name => form.querySelector(`[name="${name}"]`);
  const numeric = ['integer', 'decimal', 'currency_eur'];
  const distribution = [...numeric, 'choice', 'multiple', 'likert'];
  const words = ['text', 'long_text', 'choice', 'multiple'];
  const messages = {
    auto: 'Show compatible charts together. Select one question to focus the overview.',
    distribution: 'Compare answer counts for a choice, Likert, or number question.',
    scatter: 'Compare two numeric questions in the same entry. Optionally divide by another value to plot a ratio.',
    stacked: 'See how a Likert response varies by a single-choice category in the same entry.',
    word_cloud: 'Summarize text or categorical answers. Larger words have more responses.'
  };
  const populate = (name, allowed, empty) => {
    const select = get(name), previous = select.value;
    select.replaceChildren(new Option(empty, ''));
    fields.filter(field => (!get('section').value || field.section_id === get('section').value) && allowed.includes(field.type)).forEach(field => {
      const prefix = !get('section').value && field.section_title ? `${field.section_title} · ` : '';
      select.add(new Option(prefix + field.label, field.id));
    });
    if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
  };
  const sync = () => {
    const type = get('chart_type').value || 'auto';
    form.querySelector('[data-plot-help]').textContent = messages[type];
    form.querySelector('[data-plot-question]').hidden = ['scatter', 'stacked'].includes(type);
    form.querySelector('[data-scatter-settings]').hidden = !['auto', 'scatter'].includes(type);
    form.querySelector('[data-stacked-settings]').hidden = !['auto', 'stacked'].includes(type);
    populate('question', type === 'distribution' ? distribution : (type === 'word_cloud' ? words : [...new Set([...distribution, ...words])]), 'All compatible questions');
    ['x','x_denominator','y','y_denominator'].forEach(name => populate(name, numeric, 'None'));
    populate('category', ['choice'], 'None');
    populate('scale', ['likert'], 'None');
    const available = fields.filter(field => !get('section').value || field.section_id === get('section').value);
    const groups = [...new Set(available.map(field => field.section_id))];
    const supports = {
      auto: true,
      distribution: available.some(field => distribution.includes(field.type)),
      word_cloud: available.some(field => words.includes(field.type)),
      scatter: groups.some(group => available.filter(field => field.section_id === group && numeric.includes(field.type)).length >= 2),
      stacked: groups.some(group => available.some(field => field.section_id === group && field.type === 'choice') && available.some(field => field.section_id === group && field.type === 'likert'))
    };
    const focused = type === 'auto' && !!get('question').value;
    form.querySelector('[data-scatter-settings]').hidden = focused || !supports.scatter || !['auto', 'scatter'].includes(type);
    form.querySelector('[data-stacked-settings]').hidden = focused || !supports.stacked || !['auto', 'stacked'].includes(type);
    Array.from(get('chart_type').options).forEach(option => { option.disabled = supports[option.value] === false; });
    if (supports[type] === false) { get('chart_type').value = 'auto'; sync(); }
  };
  get('section').addEventListener('change', sync);
  get('chart_type').addEventListener('change', sync);
  get('question').addEventListener('change', sync);
  sync();
})();
