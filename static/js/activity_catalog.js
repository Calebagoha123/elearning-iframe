(() => {
  const settings = document.querySelector('[data-entry-settings]');
  const presetData = document.getElementById('entry-language-presets');
  if (settings && presetData) {
    const presets = JSON.parse(presetData.textContent);
    const language = settings.querySelector('[name$="-landing_language"]');
    let previous = language.value || 'en';
    language.addEventListener('change', () => {
      const selected = language.value || 'en';
      Object.entries(presets[previous]).forEach(([key, value]) => {
        const input = settings.querySelector(`[name$="-entry_${key}"]`);
        if (input && (input.value === value || input.value === '')) input.value = presets[selected][key];
      });
      previous = selected;
      language.dispatchEvent(new Event('input', {bubbles: true}));
    });
  }
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const input = document.getElementById(button.dataset.copy);
      const status = document.querySelector('[data-copy-status]');
      try {
        await navigator.clipboard.writeText(input.value);
        status.textContent = 'Copied.';
      } catch {
        input.select();
        status.textContent = 'Copy the highlighted text.';
      }
    });
  });
})();
