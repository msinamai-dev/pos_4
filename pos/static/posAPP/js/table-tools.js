/* ==========================================================================
   POS table tools
   - Gives every table the same "pos-table" format
   - Adds a filter box (or dropdown) under EVERY column header
   - Click a header to sort, global search, rows-per-page, pagination
   - Opt out of the tools for one table with class="table-no-tools"
   ========================================================================== */
(function () {
  'use strict';

  var PAGE_SIZES = [10, 15, 25, 50, 100, 0];      // 0 = All
  var DEFAULT_SIZE = 15;
  var MAX_SELECT_OPTIONS = 15;                      // <= this many distinct values -> dropdown
  var CURRENCY_RE = /USD|ZWL|ZWG|ZiG|ZAR|EUR|GBP|[\s,$€£%]/gi;
  var NUM_RE = /^[-+]?\d*\.?\d+$/;
  var NO_FILTER_HEAD = /^(actions?|options?|operations?|manage|edit|delete|view|print|select|#|)$/i;
  var NOT_NUMERIC_HEAD = /phone|mobile|tel|barcode|sku|code|receipt|ref|invoice|order|\bid\b|number|no\.?$|date|time|year|month/i;

  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
  function lower(s) { return norm(s).toLocaleLowerCase(); }

  function cellText(cell) {
    if (!cell) return '';
    var t = norm(cell.textContent);
    var fields = cell.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea');
    if (fields.length) {
      var extra = [];
      fields.forEach(function (f) {
        if (f.tagName === 'SELECT') {
          var o = f.options[f.selectedIndex];
          if (o) extra.push(o.text);
        } else {
          extra.push(f.value);
        }
      });
      t = norm(t + ' ' + extra.join(' '));
    }
    return t;
  }

  function parseNum(text) {
    var s = norm(text).replace(CURRENCY_RE, '');
    return NUM_RE.test(s) ? parseFloat(s) : NaN;
  }

  /* ---- Uniform formatting for every table ---- */
  function normalize(table) {
    table.classList.add('pos-table');
    table.classList.remove('table-striped', 'table-bordered', 'table-dark', 'table-secondary',
      'mdc-elevation--z4', 'mdc-elevation--z2', 'align-middle', 'text-center');
    table.style.removeProperty('width');
    table.style.removeProperty('min-width');
    table.style.removeProperty('table-layout');
    table.querySelectorAll('thead, thead tr, thead th').forEach(function (el) {
      el.classList.remove('table-light', 'table-dark', 'table-primary', 'table-secondary', 'bg-dark', 'bg-primary', 'text-white');
    });
    if (!table.closest('.table-responsive')) {
      var wrap = document.createElement('div');
      wrap.className = 'table-responsive';
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    }
  }

  function enhance(table) {
    if (!table || table.dataset.tableToolsReady) return;
    if (table.closest('td, th')) return;                       // nested table
    if (table.closest('[data-no-table-tools]')) return;
    normalize(table);
    if (table.classList.contains('table-no-tools') || !table.tBodies.length) return;

    var body = table.tBodies[0];
    var wrapper = table.closest('.table-responsive');
    var headRow = table.tHead && table.tHead.rows.length ? table.tHead.rows[table.tHead.rows.length - 1] : null;
    var rows = [];
    var hasRowspan = !!body.querySelector('td[rowspan], th[rowspan]');

    function collectRows() {
      rows = Array.from(body.rows).filter(function (r) {
        return !r.classList.contains('pos-no-match') && !r.querySelector('td[colspan], th[colspan]');
      });
      rows.forEach(function (r, i) { if (r.dataset.posIdx === undefined) r.dataset.posIdx = i; });
    }
    collectRows();
    if (rows.length < 1 || hasRowspan) return;                 // nothing to filter

    table.dataset.tableToolsReady = '1';

    /* ---- header analysis ---- */
    var headCells = headRow ? Array.from(headRow.cells) : [];
    var simpleHead = headCells.length > 0 &&
      headCells.every(function (c) { return (c.colSpan || 1) === 1 && (c.rowSpan || 1) === 1; });
    var cols = [];                                            // per-column meta
    var filters = [];                                         // active column filters {index: value}
    var sort = { col: -1, dir: 0 };                           // dir: 1 asc, -1 desc, 0 none
    var page = 1;

    function analyseColumns() {
      cols = headCells.map(function (th, i) {
        var label = norm(th.textContent);
        var values = rows.map(function (r) { return cellText(r.cells[i]); });
        var nonEmpty = values.filter(function (v) { return v !== ''; });
        var hasCheckbox = !!th.querySelector('input[type=checkbox]');
        var isAction = /^(actions?|options?|operations?|manage)$/i.test(label) ||
          (nonEmpty.length === 0 && rows.some(function (r) { return r.cells[i] && r.cells[i].querySelector('a, button'); }));
        var numeric = !isAction && nonEmpty.length > 0 && !NOT_NUMERIC_HEAD.test(label) &&
          nonEmpty.every(function (v) { return !isNaN(parseNum(v)); });
        var distinct = Array.from(new Set(nonEmpty));
        var asSelect = !isAction && !numeric && distinct.length > 1 && distinct.length <= MAX_SELECT_OPTIONS &&
          rows.length >= 4 && distinct.every(function (v) { return v.length <= 40; });
        return {
          label: label, numeric: numeric, isAction: isAction, asSelect: asSelect,
          filterable: !hasCheckbox && !NO_FILTER_HEAD.test(label) && !isAction && nonEmpty.length > 0,
          sortable: !hasCheckbox && !isAction && label !== '',
          distinct: distinct.sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); })
        };
      });
    }

    function applyColumnClasses() {
      cols.forEach(function (c, i) {
        var cls = c.isAction ? 'pos-actions' : (c.numeric ? 'pos-num' : '');
        if (headCells[i]) {
          headCells[i].classList.toggle('pos-actions', c.isAction);
          headCells[i].classList.toggle('pos-num', c.numeric);
        }
        rows.forEach(function (r) {
          var cell = r.cells[i];
          if (!cell) return;
          cell.classList.toggle('pos-actions', c.isAction);
          cell.classList.toggle('pos-num', c.numeric);
        });
      });
    }

    /* ---- toolbar ---- */
    var toolbar = document.createElement('div');
    toolbar.className = 'table-tools';
    toolbar.innerHTML =
      '<label class="table-search"><i class="bi bi-search"></i>' +
      '<input type="search" class="table-global-input" aria-label="Search this table" placeholder="Search all columns…"></label>' +
      '<div class="table-controls">' +
      '<span class="table-count"></span>' +
      '<button type="button" class="table-clear" disabled><i class="bi bi-x-circle"></i> Clear filters</button>' +
      '<label class="d-flex align-items-center gap-2 mb-0 small text-muted">Rows ' +
      '<select class="table-size-select" aria-label="Rows per page"></select></label>' +
      '</div>';
    var globalInput = toolbar.querySelector('.table-global-input');
    var countEl = toolbar.querySelector('.table-count');
    var clearBtn = toolbar.querySelector('.table-clear');
    var sizeSel = toolbar.querySelector('.table-size-select');
    PAGE_SIZES.forEach(function (n) {
      var o = document.createElement('option');
      o.value = n; o.textContent = n === 0 ? 'All' : n;
      if (n === DEFAULT_SIZE) o.selected = true;
      sizeSel.appendChild(o);
    });
    var pager = document.createElement('div');
    pager.className = 'table-pagination';
    wrapper.classList.add('pos-has-tools');
    wrapper.parentNode.insertBefore(toolbar, wrapper);
    wrapper.parentNode.insertBefore(pager, wrapper.nextSibling);

    /* "no matching rows" message */
    var emptyRow = document.createElement('tr');
    emptyRow.className = 'pos-no-match table-empty';
    emptyRow.style.display = 'none';
    var emptyCell = document.createElement('td');
    emptyCell.colSpan = Math.max(1, headCells.length || (rows[0] ? rows[0].cells.length : 1));
    emptyCell.textContent = 'No rows match the current filters.';
    emptyRow.appendChild(emptyCell);
    body.appendChild(emptyRow);

    /* ---- column filter row ---- */
    var filterRow = null;
    var filterControls = [];
    function buildFilterRow() {
      if (!simpleHead) return;
      filterRow = document.createElement('tr');
      filterRow.className = 'pos-filter-row';
      cols.forEach(function (c, i) {
        var th = document.createElement('th');
        if (c.filterable) {
          var ctl;
          if (c.asSelect) {
            ctl = document.createElement('select');
            ctl.className = 'table-filter-select';
            fillSelect(ctl, c, '');
          } else {
            ctl = document.createElement('input');
            ctl.type = 'text';
            ctl.className = 'table-filter-input';
            ctl.placeholder = c.numeric ? 'e.g. >100' : 'Filter…';
            ctl.autocomplete = 'off';
          }
          ctl.setAttribute('aria-label', 'Filter ' + c.label);
          ctl.dataset.col = i;
          var evt = ctl.tagName === 'SELECT' ? 'change' : 'input';
          var timer = null;
          ctl.addEventListener(evt, function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
              var v = ctl.value;
              if (norm(v) === '') delete filters[i]; else filters[i] = v;
              page = 1; draw();
            }, evt === 'input' ? 120 : 0);
          });
          ctl.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
          th.appendChild(ctl);
          filterControls[i] = ctl;
        }
        filterRow.appendChild(th);
      });
      table.tHead.appendChild(filterRow);
    }
    function fillSelect(sel, c, keep) {
      sel.innerHTML = '';
      var all = document.createElement('option');
      all.value = ''; all.textContent = 'All';
      sel.appendChild(all);
      c.distinct.forEach(function (v) {
        var o = document.createElement('option');
        o.value = v; o.textContent = v;
        sel.appendChild(o);
      });
      sel.value = keep || '';
    }

    /* ---- sorting ---- */
    function setupSorting() {
      if (!simpleHead) return;
      headCells.forEach(function (th, i) {
        if (!cols[i].sortable) return;
        th.classList.add('pos-sortable');
        th.title = 'Click to sort';
        th.addEventListener('click', function (e) {
          if (e.target.closest('input, select, button, a')) return;
          if (sort.col === i) sort.dir = sort.dir === 1 ? -1 : (sort.dir === -1 ? 0 : 1);
          else { sort.col = i; sort.dir = 1; }
          if (sort.dir === 0) sort.col = -1;
          applySort(); page = 1; draw();
        });
      });
    }
    function applySort() {
      headCells.forEach(function (th, i) {
        th.classList.toggle('pos-sort-asc', sort.col === i && sort.dir === 1);
        th.classList.toggle('pos-sort-desc', sort.col === i && sort.dir === -1);
      });
      var ordered = rows.slice();
      if (sort.dir === 0) {
        ordered.sort(function (a, b) { return Number(a.dataset.posIdx) - Number(b.dataset.posIdx); });
      } else {
        var i = sort.col, numeric = cols[i] && cols[i].numeric;
        ordered.sort(function (a, b) {
          var x = cellText(a.cells[i]), y = cellText(b.cells[i]);
          var r;
          if (numeric) {
            var nx = parseNum(x), ny = parseNum(y);
            r = (isNaN(nx) ? -Infinity : nx) - (isNaN(ny) ? -Infinity : ny);
            if (!isFinite(r)) r = isNaN(nx) && isNaN(ny) ? 0 : (isNaN(nx) ? -1 : 1);
          } else {
            r = x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' });
          }
          return r * sort.dir;
        });
      }
      ordered.forEach(function (r) { body.insertBefore(r, emptyRow); });
      if (mo) mo.takeRecords();
    }

    /* ---- filtering ---- */
    function matchColumn(text, filterValue, col) {
      var fv = norm(filterValue);
      if (fv === '') return true;
      if (col.asSelect) return lower(text) === lower(fv);
      var m = fv.match(/^(>=|<=|!=|>|<|=)\s*(-?\d+(?:[.,]\d+)?)$/);
      if (m) {
        var n = parseNum(text), t = parseFloat(m[2].replace(',', '.'));
        if (isNaN(n)) return false;
        switch (m[1]) {
          case '>': return n > t; case '<': return n < t;
          case '>=': return n >= t; case '<=': return n <= t;
          case '=': return n === t; case '!=': return n !== t;
        }
      }
      return lower(text).indexOf(lower(fv)) !== -1;
    }
    function filteredRows() {
      var term = lower(globalInput.value);
      var keys = Object.keys(filters);
      return rows.filter(function (r) {
        if (term && lower(cellText(r)).indexOf(term) === -1) return false;
        for (var k = 0; k < keys.length; k++) {
          var i = Number(keys[k]);
          if (!matchColumn(cellText(r.cells[i]), filters[i], cols[i])) return false;
        }
        return true;
      });
    }

    function anyFilter() { return Object.keys(filters).length > 0 || norm(globalInput.value) !== ''; }

    function draw() {
      var list = filteredRows();
      var size = Number(sizeSel.value);
      var pages = size === 0 ? 1 : Math.max(1, Math.ceil(list.length / size));
      page = Math.min(Math.max(1, page), pages);
      var start = size === 0 ? 0 : (page - 1) * size;
      var end = size === 0 ? list.length : Math.min(start + size, list.length);
      var visible = new Set(list.slice(start, end));
      rows.forEach(function (r) { r.style.display = visible.has(r) ? '' : 'none'; });
      emptyRow.style.display = list.length === 0 ? '' : 'none';

      countEl.textContent = list.length === rows.length
        ? rows.length + ' row' + (rows.length === 1 ? '' : 's')
        : list.length + ' of ' + rows.length + ' rows';
      clearBtn.disabled = !anyFilter();
      filterControls.forEach(function (ctl, i) {
        if (ctl) ctl.classList.toggle('is-active', filters[i] !== undefined);
      });

      pager.replaceChildren();
      var info = document.createElement('span');
      info.className = 'pager-info';
      info.textContent = list.length ? 'Showing ' + (start + 1) + '–' + end + ' of ' + list.length : 'No results';
      var btns = document.createElement('div');
      btns.className = 'pager-buttons';
      function btn(label, target, disabled, active) {
        var b = document.createElement('button');
        b.type = 'button'; b.textContent = label; b.disabled = disabled;
        if (active) b.classList.add('active');
        b.addEventListener('click', function () { page = target; draw(); });
        btns.appendChild(b);
      }
      btn('‹', Math.max(1, page - 1), page === 1, false);
      var s = Math.max(1, Math.min(page - 2, pages - 4)), e = Math.min(pages, s + 4);
      if (s > 1) { btn('1', 1, false, false); if (s > 2) btns.appendChild(Object.assign(document.createElement('span'), { textContent: '…', className: 'px-1 text-muted' })); }
      for (var n = s; n <= e; n++) btn(String(n), n, false, n === page);
      if (e < pages) { if (e < pages - 1) btns.appendChild(Object.assign(document.createElement('span'), { textContent: '…', className: 'px-1 text-muted' })); btn(String(pages), pages, false, false); }
      btn('›', Math.min(pages, page + 1), page === pages, false);
      pager.append(info, btns);
      pager.style.display = (size !== 0 && pages > 1) || list.length ? '' : 'none';
    }

    /* ---- events ---- */
    var gTimer = null;
    globalInput.addEventListener('input', function () {
      clearTimeout(gTimer);
      gTimer = setTimeout(function () { page = 1; draw(); }, 120);
    });
    globalInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
    sizeSel.addEventListener('change', function () { page = 1; draw(); });
    clearBtn.addEventListener('click', function () {
      filters = [];
      globalInput.value = '';
      filterControls.forEach(function (ctl) { if (ctl) ctl.value = ''; });
      page = 1; draw();
    });

    /* ---- rebuild when rows are added/removed by other scripts ---- */
    var mo = null, rTimer = null;
    function refresh() {
      collectRows();
      analyseColumns();
      applyColumnClasses();
      filterControls.forEach(function (ctl, i) {
        if (ctl && ctl.tagName === 'SELECT') fillSelect(ctl, cols[i], filters[i] || '');
      });
      if (sort.dir !== 0) applySort();
      draw();
    }
    if (window.MutationObserver) {
      mo = new MutationObserver(function () {
        clearTimeout(rTimer);
        rTimer = setTimeout(function () { refresh(); mo.takeRecords(); }, 60);
      });
      mo.observe(body, { childList: true });
    }
    table._posRefresh = refresh;

    /* ---- go ---- */
    analyseColumns();
    applyColumnClasses();
    buildFilterRow();
    setupSorting();
    draw();
    if (mo) mo.takeRecords();
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll('table').forEach(enhance);
  }

  function init() {
    enhanceAll(document);
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      records.forEach(function (rec) {
        rec.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.matches && node.matches('table')) enhance(node);
          else if (node.querySelectorAll) node.querySelectorAll('table').forEach(enhance);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.PosTables = { enhance: enhance, enhanceAll: enhanceAll };
})();
