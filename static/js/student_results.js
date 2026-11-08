(() => {
  const root = document.querySelector('[data-student-results]');
  if (!root) return;
  const tabs = Array.from(root.querySelectorAll('[data-result-tab]'));
  const select = tab => {
    tabs.forEach(item => {
      item.setAttribute('aria-selected', String(item === tab));
      item.tabIndex = item === tab ? 0 : -1;
    });
    root.querySelectorAll('[data-result-panel]').forEach(panel => {
      panel.hidden = panel.dataset.resultPanel !== tab.dataset.resultTab;
    });
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') next = tabs[(index + 1) % tabs.length];
      if (event.key === 'Home') next = tabs[0];
      if (event.key === 'End') next = tabs[tabs.length - 1];
      if (!next) return;
      event.preventDefault(); select(next); next.focus();
    });
  });
})();
