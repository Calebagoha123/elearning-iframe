(() => {
  document.querySelectorAll('[data-chart-visibility]').forEach(form => {
    form.querySelector('[type="checkbox"]').addEventListener('change', () => form.requestSubmit());
  });
})();
