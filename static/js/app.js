/* ctrl-f-vuln viewer interactions.
 *
 * Everything here is progressive enhancement: each row's review toggle and each
 * repository action is a real form that works with JavaScript disabled. When it
 * is enabled we intercept those forms so triaging a page of results never costs
 * a full reload.
 */
'use strict';

(function () {
  const MAX_PREVIEW_LINES = 4000;
  const FLASH_TIMEOUT_MS = 6000;

  const table = document.getElementById('files-table');
  const project = table ? table.dataset.project || '' : '';
  const flashes = document.getElementById('flashes');
  const bulkBar = document.getElementById('bulk-bar');
  const bulkCount = document.getElementById('bulk-count');
  const selectAll = document.getElementById('select-all');
  const helpDialog = document.getElementById('help-dialog');

  const drawer = document.getElementById('preview');
  const previewName = document.getElementById('preview-name');
  const previewPath = document.getElementById('preview-path');
  const previewCode = document.getElementById('preview-code');
  const previewStatus = document.getElementById('preview-status');
  const previewGithub = document.getElementById('preview-github');
  const previewChecked = document.getElementById('preview-checked');
  const previewTerms = document.getElementById('preview-terms');
  const matchNav = document.getElementById('preview-matchnav');
  const matchCount = document.getElementById('preview-match-count');

  let focusedRow = null;
  let previewRow = null;
  let matches = [];
  let matchIndex = -1;
  let previewToken = 0;

  /* ---------------- helpers ---------------- */

  function rows() {
    return table ? Array.from(table.querySelectorAll('tbody tr[data-file-id]')) : [];
  }

  function notify(message, kind) {
    if (!flashes) return;
    const box = document.createElement('div');
    box.className = 'flash flash-' + (kind || 'info');
    const text = document.createElement('span');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'flash-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.addEventListener('click', () => box.remove());
    box.append(text, close);
    flashes.append(box);
    window.setTimeout(() => box.remove(), FLASH_TIMEOUT_MS);
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      /* Non-JSON error body; fall through to the status message. */
    }
    if (!response.ok) {
      throw new Error(data.error || 'Request failed (' + response.status + ')');
    }
    return data;
  }

  function applyChecked(row, checked) {
    const button = row.querySelector('.check-toggle');
    const hidden = row.querySelector('.check-form input[name="checked"]');
    row.classList.toggle('is-checked', checked);
    if (button) {
      button.dataset.checked = checked ? '1' : '0';
      button.setAttribute('aria-pressed', checked ? 'true' : 'false');
      const label = button.querySelector('.sr-only');
      if (label) label.textContent = checked ? 'Mark unreviewed' : 'Mark reviewed';
    }
    if (hidden) hidden.value = checked ? '0' : '1';
    if (previewRow === row && previewChecked) previewChecked.checked = checked;
  }

  function updateSummary(summary) {
    if (!summary) return;
    const checkedEl = document.querySelector('[data-summary-checked]');
    const filesEl = document.querySelector('[data-summary-files]');
    if (checkedEl) checkedEl.textContent = summary.checked;
    if (filesEl) filesEl.textContent = summary.files;
    const pct = summary.files ? (summary.checked / summary.files) * 100 : 0;
    const bar = document.querySelector('.progress-bar');
    if (bar) bar.style.width = pct.toFixed(2) + '%';
    const label = document.querySelector('[data-progress-label]');
    if (label) label.textContent = pct.toFixed(1) + '%';
  }

  /* ---------------- review toggles ---------------- */

  async function toggleRow(row, force) {
    const id = row.dataset.fileId;
    const button = row.querySelector('.check-toggle');
    const next = typeof force === 'boolean'
      ? force
      : !(button && button.dataset.checked === '1');
    if (button) button.classList.add('is-busy');
    try {
      const data = await postJSON('/api/files/' + id + '/checked', {
        checked: next,
        project: project,
      });
      applyChecked(row, data.checked);
      updateSummary(data.summary);
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      if (button) button.classList.remove('is-busy');
    }
  }

  /* ---------------- bulk selection ---------------- */

  function selectedRows() {
    return rows().filter((row) => {
      const box = row.querySelector('.row-select');
      return box && box.checked;
    });
  }

  function refreshBulkBar() {
    if (!bulkBar) return;
    const count = selectedRows().length;
    bulkBar.hidden = count === 0;
    if (bulkCount) {
      bulkCount.textContent = count + (count === 1 ? ' file selected' : ' files selected');
    }
    rows().forEach((row) => {
      const box = row.querySelector('.row-select');
      row.classList.toggle('is-selected', Boolean(box && box.checked));
    });
    if (selectAll) {
      const all = rows().length;
      selectAll.checked = all > 0 && count === all;
      selectAll.indeterminate = count > 0 && count < all;
    }
  }

  async function bulkSet(checked) {
    const targets = selectedRows();
    if (!targets.length) return;
    const ids = targets.map((row) => Number(row.dataset.fileId));
    try {
      const data = await postJSON('/api/files/checked', {
        ids: ids,
        checked: checked,
        project: project,
      });
      targets.forEach((row) => applyChecked(row, data.checked));
      updateSummary(data.summary);
      notify(
        data.changed + ' file(s) marked ' + (checked ? 'reviewed' : 'unreviewed'),
        'success'
      );
      clearSelection();
    } catch (err) {
      notify(err.message, 'error');
    }
  }

  function clearSelection() {
    rows().forEach((row) => {
      const box = row.querySelector('.row-select');
      if (box) box.checked = false;
    });
    refreshBulkBar();
  }

  /* ---------------- preview drawer ---------------- */

  function highlightInto(container, line, needles) {
    if (!needles.length) {
      container.textContent = line;
      return false;
    }
    const lower = line.toLowerCase();
    let position = 0;
    let found = false;
    while (position < line.length) {
      let bestIndex = -1;
      let bestLength = 0;
      for (const needle of needles) {
        const index = lower.indexOf(needle, position);
        if (index === -1) continue;
        if (bestIndex === -1 || index < bestIndex ||
            (index === bestIndex && needle.length > bestLength)) {
          bestIndex = index;
          bestLength = needle.length;
        }
      }
      if (bestIndex === -1 || bestLength === 0) break;
      if (bestIndex > position) {
        container.appendChild(document.createTextNode(line.slice(position, bestIndex)));
      }
      const mark = document.createElement('mark');
      mark.textContent = line.slice(bestIndex, bestIndex + bestLength);
      container.appendChild(mark);
      position = bestIndex + bestLength;
      found = true;
    }
    if (position < line.length) {
      container.appendChild(document.createTextNode(line.slice(position)));
    }
    return found;
  }

  function renderCode(text, terms) {
    previewCode.textContent = '';
    matches = [];
    matchIndex = -1;

    const needles = (terms || [])
      .filter((term) => typeof term === 'string' && term.length >= 2)
      .map((term) => term.toLowerCase());

    const lines = text.split('\n');
    const limited = lines.length > MAX_PREVIEW_LINES;
    const shown = limited ? lines.slice(0, MAX_PREVIEW_LINES) : lines;
    const fragment = document.createDocumentFragment();

    shown.forEach((line, index) => {
      const lineEl = document.createElement('div');
      lineEl.className = 'code-line';
      const number = document.createElement('span');
      number.className = 'ln';
      number.textContent = String(index + 1);
      const content = document.createElement('span');
      content.className = 'lc';
      if (highlightInto(content, line, needles)) lineEl.classList.add('has-match');
      lineEl.append(number, content);
      fragment.append(lineEl);
    });

    previewCode.append(fragment);
    matches = Array.from(previewCode.querySelectorAll('mark'));
    updateMatchNav();
    return limited;
  }

  function updateMatchNav() {
    if (!matchNav) return;
    matchNav.hidden = matches.length === 0;
    if (matchCount) {
      matchCount.textContent = matches.length
        ? (matchIndex + 1) + '/' + matches.length
        : '0';
    }
  }

  function gotoMatch(delta) {
    if (!matches.length) return;
    if (matchIndex >= 0 && matches[matchIndex]) {
      matches[matchIndex].classList.remove('is-current');
    }
    matchIndex = (matchIndex + delta + matches.length) % matches.length;
    const current = matches[matchIndex];
    current.classList.add('is-current');
    current.scrollIntoView({ block: 'center', behavior: 'auto' });
    updateMatchNav();
  }

  function closeDrawer() {
    if (!drawer || drawer.hidden) return false;
    drawer.hidden = true;
    document.body.classList.remove('drawer-open');
    previewRow = null;
    matches = [];
    matchIndex = -1;
    return true;
  }

  async function openPreview(row) {
    if (!drawer) return;
    const id = row.dataset.fileId;
    const token = ++previewToken;
    previewRow = row;
    drawer.hidden = false;
    document.body.classList.add('drawer-open');

    previewName.textContent = row.dataset.name || '';
    previewPath.textContent = row.dataset.path || '';
    previewGithub.href = row.dataset.fileUrl || '#';
    previewStatus.textContent = 'Loading file from GitHub…';
    previewCode.textContent = '';
    previewTerms.hidden = true;
    previewTerms.textContent = '';
    if (matchNav) matchNav.hidden = true;
    const toggle = row.querySelector('.check-toggle');
    if (previewChecked) previewChecked.checked = Boolean(toggle && toggle.dataset.checked === '1');

    try {
      const response = await fetch('/api/files/' + id + '/preview', {
        credentials: 'same-origin',
      });
      let data = {};
      try {
        data = await response.json();
      } catch (err) {
        /* fall through */
      }
      if (token !== previewToken) return; // A newer preview superseded this one.
      if (!response.ok) throw new Error(data.error || 'Preview failed');

      const limited = renderCode(data.content || '', data.terms);
      const notes = [];
      if (data.truncated) notes.push('File truncated to the configured preview size.');
      if (limited) notes.push('Showing the first ' + MAX_PREVIEW_LINES + ' lines.');
      if (!matches.length && (data.terms || []).length) {
        notes.push('No literal match for: ' + data.terms.join(', '));
      }
      previewStatus.textContent = notes.join(' ');

      if ((data.terms || []).length) {
        previewTerms.hidden = false;
        previewTerms.textContent = '';
        const label = document.createElement('span');
        label.textContent = 'highlighting:';
        previewTerms.append(label);
        data.terms.forEach((term) => {
          const chip = document.createElement('code');
          chip.className = 'query-chip';
          chip.textContent = term;
          previewTerms.append(chip);
        });
      }
      if (matches.length) gotoMatch(1);
    } catch (err) {
      if (token !== previewToken) return;
      previewStatus.textContent = err.message;
    }
  }

  /* ---------------- row focus & keyboard ---------------- */

  function focusRow(row) {
    if (!row) return;
    rows().forEach((other) => other.classList.remove('is-focused'));
    row.classList.add('is-focused');
    focusedRow = row;
    row.scrollIntoView({ block: 'nearest' });
  }

  function moveFocus(delta) {
    const all = rows();
    if (!all.length) return;
    const index = focusedRow ? all.indexOf(focusedRow) : -1;
    const next = index === -1
      ? (delta > 0 ? 0 : all.length - 1)
      : Math.min(all.length - 1, Math.max(0, index + delta));
    focusRow(all[next]);
  }

  function isTyping(element) {
    if (!element) return false;
    const tag = element.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
      element.isContentEditable;
  }

  /* ---------------- wiring ---------------- */

  document.addEventListener('submit', (event) => {
    const form = event.target;

    const confirmMessage = form.dataset ? form.dataset.confirm : null;
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
      return;
    }

    if (form.classList.contains('check-form')) {
      event.preventDefault();
      const row = form.closest('tr');
      if (row) toggleRow(row);
      return;
    }

    const busy = form.querySelector('[data-busy]');
    if (busy) {
      // Deferred so the submission is already in flight before we disable it.
      window.setTimeout(() => {
        busy.disabled = true;
        busy.textContent = busy.dataset.busy;
      }, 0);
    }
  });

  document.addEventListener('click', (event) => {
    const dismiss = event.target.closest('[data-dismiss]');
    if (dismiss) {
      const flash = dismiss.closest('.flash');
      if (flash) flash.remove();
      return;
    }

    if (event.target.closest('[data-help-open]')) {
      if (helpDialog && typeof helpDialog.showModal === 'function') helpDialog.showModal();
      return;
    }

    if (event.target.closest('[data-drawer-close]')) {
      closeDrawer();
      return;
    }

    const trigger = event.target.closest('.preview-trigger');
    if (trigger) {
      const row = trigger.closest('tr');
      if (row) {
        focusRow(row);
        openPreview(row);
      }
      return;
    }

    const bulkButton = event.target.closest('[data-bulk]');
    if (bulkButton) {
      bulkSet(bulkButton.dataset.bulk === '1');
      return;
    }

    if (event.target.closest('[data-bulk-clear]')) {
      clearSelection();
      return;
    }

    const row = event.target.closest('tbody tr[data-file-id]');
    if (row && !event.target.closest('a, button, input, label')) {
      focusRow(row);
    }
  });

  document.addEventListener('change', (event) => {
    const element = event.target;

    if (element.matches('[data-autosubmit]') && element.form) {
      element.form.submit();
      return;
    }

    if (element.classList.contains('row-select')) {
      refreshBulkBar();
      return;
    }

    if (selectAll && element === selectAll) {
      const checked = selectAll.checked;
      rows().forEach((row) => {
        const box = row.querySelector('.row-select');
        if (box) box.checked = checked;
      });
      refreshBulkBar();
      return;
    }

    if (previewChecked && element === previewChecked && previewRow) {
      toggleRow(previewRow, previewChecked.checked);
    }
  });

  document.getElementById('preview-next-match')
    ?.addEventListener('click', () => gotoMatch(1));
  document.getElementById('preview-prev-match')
    ?.addEventListener('click', () => gotoMatch(-1));

  document.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    if (isTyping(event.target)) {
      if (event.key === 'Escape') event.target.blur();
      return;
    }
    if (helpDialog && helpDialog.open) return;

    switch (event.key) {
      case 'j':
        event.preventDefault();
        moveFocus(1);
        break;
      case 'k':
        event.preventDefault();
        moveFocus(-1);
        break;
      case 'x':
        if (focusedRow) {
          event.preventDefault();
          toggleRow(focusedRow);
        }
        break;
      case 'p':
        if (focusedRow) {
          event.preventDefault();
          openPreview(focusedRow);
        }
        break;
      case 'Enter':
        if (focusedRow && document.activeElement === document.body) {
          event.preventDefault();
          openPreview(focusedRow);
        }
        break;
      case 'o':
        if (focusedRow && focusedRow.dataset.fileUrl) {
          event.preventDefault();
          window.open(focusedRow.dataset.fileUrl, '_blank', 'noopener');
        }
        break;
      case 's':
        if (focusedRow) {
          const box = focusedRow.querySelector('.row-select');
          if (box) {
            event.preventDefault();
            box.checked = !box.checked;
            refreshBulkBar();
          }
        }
        break;
      case 'n':
        if (matches.length) {
          event.preventDefault();
          gotoMatch(event.shiftKey ? -1 : 1);
        }
        break;
      case 'N':
        if (matches.length) {
          event.preventDefault();
          gotoMatch(-1);
        }
        break;
      case '/': {
        const filter = document.getElementById('q');
        if (filter) {
          event.preventDefault();
          filter.focus();
          filter.select();
        }
        break;
      }
      case '?':
        if (helpDialog && typeof helpDialog.showModal === 'function') {
          event.preventDefault();
          helpDialog.showModal();
        }
        break;
      case 'Escape':
        closeDrawer();
        break;
      default:
        break;
    }
  });

  refreshBulkBar();
})();
