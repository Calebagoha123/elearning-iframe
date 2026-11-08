(() => {
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
