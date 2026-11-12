/* Snowflake: a Snowsight-style worksheet over the local warehouse. */
App.apps.snowflake = (() => {
  let root, editor, tree = null, view = 'worksheet', leftTab = 'databases';
  let last = null;          // last result {columns, rows, elapsed, query_id, sql, error}
  let selectedCol = null, running = false, filter = '';
  const expanded = store.get('sf.expanded', { ATLAS_DEV: true, EXPORT: true });

  const STARTER = `-- ATL-101 exploration
-- Ctrl+Enter runs the statement under the cursor, Ctrl+Shift+Enter runs them all.
use schema export;

-- Which EXPORT tables carry policy_number, and how do the others link back to a policy?
select * from policyperiod;
`;
  let sheets = store.get('sf.worksheets', null) || [{ id: 1, name: 'ATL-101 exploration', sql: STARTER }];
  let activeId = store.get('sf.active', sheets[0].id);
  const active = () => sheets.find(s => s.id === activeId) || sheets[0];
  const saveSheets = () => { store.set('sf.worksheets', sheets); store.set('sf.active', activeId); };

  function ctx() {
    return App.state?.context || { role: 'DEVELOPER', warehouse: 'DEV_WH', database: 'ATLAS_DEV', schema: 'EXPORT' };
  }

  async function loadTree() {
    tree = await api('/api/tree');
    renderLeft();
    renderContextBar();
  }

  function hintTables() {
    const out = {};
    for (const [schema, objs] of Object.entries(tree?.schemas || {})) {
      for (const o of objs) {
        const cols = o.columns.map(c => c.name.toLowerCase());
        out[`${schema.toLowerCase()}.${o.name.toLowerCase()}`] = cols;
        if (schema === ctx().schema) out[o.name.toLowerCase()] = cols;
      }
    }
    return out;
  }

  /* ---------------- layout ---------------- */
  function render() {
    root.replaceChildren(
      h('div.sf-rail',
        h('div.sf-logo', { title: 'Snowflake' }, '❄'),
        h(`button${view === 'worksheet' ? '.on' : ''}`, { title: 'Worksheets', onclick: () => { view = 'worksheet'; render(); } }, '⌨'),
        h(`button${view === 'history' ? '.on' : ''}`, { title: 'Query History', onclick: () => { view = 'history'; render(); } }, '⟲'),
        h('div.sf-rail-spacer'),
        h('div.sf-user', { title: 'DEVELOPER' }, 'ME'),
      ),
      view === 'history' ? renderHistory() : renderWorksheet(),
    );
    if (view === 'worksheet') setTimeout(() => editor?.refresh(), 0);
  }

  function renderWorksheet() {
    const page = h('div.sf-page',
      h('header.sf-top',
        h('div.sf-tabs',
          sheets.map(s => h(`div.sf-tab${s.id === activeId ? '.on' : ''}`, {
            onclick: () => { saveCurrent(); activeId = s.id; saveSheets(); render(); },
            ondblclick: () => rename(s),
          }, s.name, sheets.length > 1 ? h('span.sf-tab-x', { onclick: e => { e.stopPropagation(); closeSheet(s); } }, '×') : null)),
          h('button.sf-tab-add', { title: 'New worksheet', onclick: newSheet }, '+'),
        ),
        h('div.sf-top-right',
          h('div.sf-ctx-chip', { title: 'Role and warehouse' },
            h('span.sf-role', '👤 ', ctx().role), h('span.sf-sep', '·'), h('span.sf-wh', h('span.sf-dot'), ctx().warehouse)),
          h('button.sf-share', 'Share'),
          h('div.sf-run',
            h('button.sf-run-btn', { title: 'Run (Ctrl+Enter)', onclick: () => run(false), disabled: running }, running ? h('span.spin.white') : '▶'),
            h('button.sf-run-more', { title: 'Run options', onclick: runMenu }, '▾'),
          ),
        ),
      ),
      h('div.sf-main',
        h('div.sf-left#sf-left'),
        h('div.sf-center',
          h('div.sf-ctxbar#sf-ctxbar'),
          h('div.sf-editor#sf-editor'),
          h('div.sf-results#sf-results'),
        ),
      ),
    );
    const edHost = page.querySelector('#sf-editor');
    editor = makeEditor(edHost, {
      value: active().sql, mode: 'text/x-sql', hint: hintTables,
      extraKeys: { 'Ctrl-Enter': () => run(false), 'Cmd-Enter': () => run(false), 'Shift-Ctrl-Enter': () => run(true), 'Shift-Cmd-Enter': () => run(true) },
    });
    editor.on('change', () => { active().sql = editor.getValue(); saveSheets(); });
    queueMicrotask(() => { renderLeft(); renderContextBar(); renderResults(); });
    return page;
  }

  function saveCurrent() { if (editor) { active().sql = editor.getValue(); saveSheets(); } }

  function newSheet() {
    saveCurrent();
    const id = Math.max(...sheets.map(s => s.id)) + 1;
    const d = new Date();
    sheets.push({ id, name: `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`, sql: '' });
    activeId = id;
    saveSheets();
    render();
  }

  function closeSheet(s) {
    sheets = sheets.filter(x => x.id !== s.id);
    if (activeId === s.id) activeId = sheets[0].id;
    saveSheets();
    render();
  }

  function rename(s) {
    const input = h('input.input', { value: s.name });
    modal('Rename worksheet', input, [{ label: 'Cancel' }, { label: 'Rename', kind: 'btn.primary', onclick: () => { s.name = input.value || s.name; saveSheets(); render(); } }]);
    setTimeout(() => input.select(), 0);
  }

  function runMenu(e) {
    e.stopPropagation();
    const menu = h('div.sf-menu',
      h('button', { onclick: () => run(false) }, h('span', 'Run'), h('kbd', 'Ctrl ↵')),
      h('button', { onclick: () => run(true) }, h('span', 'Run All'), h('kbd', 'Ctrl ⇧ ↵')),
    );
    e.currentTarget.parentElement.append(menu);
    setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }));
  }

  /* ---------------- left panel ---------------- */
  function renderLeft() {
    const host = root.querySelector('#sf-left');
    if (!host) return;
    const tabs = h('div.sf-left-tabs',
      h(`button${leftTab === 'worksheets' ? '.on' : ''}`, { onclick: () => { leftTab = 'worksheets'; renderLeft(); } }, 'Worksheets'),
      h(`button${leftTab === 'databases' ? '.on' : ''}`, { onclick: () => { leftTab = 'databases'; renderLeft(); } }, 'Databases'),
    );
    if (leftTab === 'worksheets') {
      host.replaceChildren(tabs, h('div.sf-wslist', sheets.map(s => h(`div.sf-wsitem${s.id === activeId ? '.on' : ''}`,
        { onclick: () => { saveCurrent(); activeId = s.id; saveSheets(); render(); } }, '🗎 ', s.name))));
      return;
    }
    const search = h('input.sf-search', { placeholder: 'Search objects', value: filter, oninput: e => { filter = e.target.value; renderTree(treeHost); } });
    const treeHost = h('div.sf-tree');
    host.replaceChildren(tabs, h('div.sf-search-row', search, h('button.sf-icon', { title: 'Refresh', onclick: loadTree }, '↻')), treeHost);
    renderTree(treeHost);
  }

  function toggle(key) { expanded[key] = !expanded[key]; store.set('sf.expanded', expanded); renderLeft(); }

  function renderTree(host) {
    if (!tree) { host.replaceChildren(spinner('Loading objects...')); return; }
    const f = filter.trim().toLowerCase();
    const db = tree.database;
    const items = [h('div.tnode', { onclick: () => toggle(db) }, h('span.caret', expanded[db] ? '▾' : '▸'), h('span.ticon', '🛢'), db)];
    if (expanded[db] || f) {
      for (const [schema, objs] of Object.entries(tree.schemas)) {
        const visible = objs.filter(o => !f || o.name.toLowerCase().includes(f) || schema.toLowerCase().includes(f));
        if (!visible.length) continue;
        const sk = `${schema}`;
        items.push(h('div.tnode.l1', { onclick: () => toggle(sk) }, h('span.caret', expanded[sk] || f ? '▾' : '▸'), h('span.ticon', '⧉'), schema));
        if (!(expanded[sk] || f)) continue;
        for (const kind of ['TABLE', 'VIEW']) {
          const group = visible.filter(o => o.kind === kind);
          if (!group.length) continue;
          const gk = `${schema}.${kind}`;
          items.push(h('div.tnode.l2', { onclick: () => toggle(gk) }, h('span.caret', expanded[gk] !== false ? '▾' : '▸'), kind === 'TABLE' ? 'Tables' : 'Views'));
          if (expanded[gk] === false) continue;
          for (const o of group) {
            const ok = `${schema}.${o.name}`;
            items.push(h('div.tnode.l3', { title: `${db}.${schema}.${o.name}` },
              h('span.caret', { onclick: () => toggle(ok) }, expanded[ok] ? '▾' : '▸'),
              h('span.ticon', kind === 'TABLE' ? '▦' : '◫'),
              h('span.tname', { onclick: () => toggle(ok) }, o.name),
              h('button.tinsert', { title: 'Place name in editor', onclick: () => insert(`${schema}.${o.name}`) }, '⇥')));
            if (expanded[ok]) {
              for (const c of o.columns) {
                items.push(h('div.tnode.l4', { onclick: () => insert(c.name) }, h('span.tcol', c.name), h('span.ttype', c.type.replace('(16777216)', ''))));
              }
            }
          }
        }
      }
    }
    host.replaceChildren(...items);
  }

  function insert(text) {
    if (editor?.cm) { editor.cm.replaceSelection(text); editor.cm.focus(); }
    else if (editor) { editor.setValue(editor.getValue() + text); }
  }

  /* ---------------- context bar ---------------- */
  function renderContextBar() {
    const host = root.querySelector('#sf-ctxbar');
    if (!host) return;
    const schemas = Object.keys(tree?.schemas || { [ctx().schema]: [] });
    const sel = h('select.sf-ctx-select', {
      onchange: async e => { await api('/api/sql', { sql: `use schema ${e.target.value}` }); await refreshState(); renderContextBar(); },
    }, schemas.map(s => h('option', { value: s, selected: s === ctx().schema }, `${ctx().database}.${s}`)));
    host.replaceChildren(h('span.sf-ctx-icon', '🛢'), sel, h('span.sf-ctx-hint', 'Ctrl+Space: autocomplete · Ctrl+/: comment'));
  }

  /* ---------------- run ---------------- */
  async function run(all) {
    if (running || !editor) return;
    let sql = editor.getSelection();
    if (!sql.trim()) {
      const text = editor.getValue();
      if (all) sql = text;
      else {
        const stmts = splitStatements(text);
        const pos = editor.cursorIndex();
        const hit = stmts.find(s => pos >= s.start && pos <= s.end) || [...stmts].reverse().find(s => s.start <= pos) || stmts[0];
        sql = hit ? hit.text : '';
        if (hit && editor.cm) flash(hit);
      }
    }
    if (!sql.trim()) { toast('Nothing to run'); return; }
    setRunning(true);
    root.querySelector('#sf-results').replaceChildren(spinner('Running...'));
    const res = await api('/api/sql', { sql });
    const results = res.results || [{ error: res.error || 'Request failed' }];
    last = results[results.length - 1];
    last.count = results.length;
    selectedCol = null;
    await refreshState();
    setRunning(false);
    renderResults();
    root.querySelector('.sf-ctx-chip')?.replaceWith(h('div.sf-ctx-chip', { title: 'Role and warehouse' },
      h('span.sf-role', '👤 ', ctx().role), h('span.sf-sep', '·'), h('span.sf-wh', h('span.sf-dot'), ctx().warehouse)));
    if (/^\s*(create|drop|alter|insert|update|delete|use)/im.test(sql)) loadTree();
    else renderContextBar();
  }

  function setRunning(on) {
    running = on;
    const btn = root.querySelector('.sf-run-btn');
    if (btn) { btn.disabled = on; btn.replaceChildren(on ? h('span.spin.white') : '▶'); }
  }

  function flash(stmt) {
    const cm = editor.cm;
    const mark = cm.markText(cm.posFromIndex(stmt.start), cm.posFromIndex(stmt.end), { className: 'sf-flash' });
    setTimeout(() => mark.clear(), 700);
  }

  /* ---------------- results ---------------- */
  function renderResults() {
    const host = root.querySelector('#sf-results');
    if (!host) return;
    if (!last) {
      host.replaceChildren(h('div.sf-empty', h('div.sf-empty-icon', '▶'), h('div', 'Run a query to see results'), h('small', 'Ctrl+Enter runs the statement under the cursor')));
      return;
    }
    const bar = h('div.sf-resbar',
      h('div.sf-restabs', h('span.on', 'Results'), h('span.disabled', 'Chart')),
      last.error ? null : h('div.sf-resinfo',
        h('button.sf-icon', { title: 'Download results', onclick: () => download(`${last.query_id}.csv`, toCsv(last.columns, last.rows)) }, '⤓'),
        h('span', `${last.rows.length} Row${last.rows.length === 1 ? '' : 's'}`),
      ),
    );
    if (last.error) {
      host.replaceChildren(bar, h('div.sf-error',
        h('div.sf-error-h', '⊗ ', last.error.split('\n')[0]),
        h('pre', last.error.split('\n').slice(1).join('\n')),
        h('div.sf-error-q', `Query ID ${last.query_id || ''}`)));
      return;
    }
    const grid = renderGrid(last.columns, last.rows, { selected: selectedCol, onColumn: i => { selectedCol = selectedCol === i ? null : i; renderResults(); } });
    host.replaceChildren(bar, h('div.sf-resbody', grid, renderDetails()));
  }

  function renderDetails() {
    if (selectedCol != null) {
      const col = last.columns[selectedCol];
      const { stats, top, total } = columnStats(last.columns, last.rows, selectedCol);
      return h('aside.sf-details', h('div.sf-det-h', h('span', col.name), h('button.sf-icon', { onclick: () => { selectedCol = null; renderResults(); } }, '×')),
        stats.map(([k, v]) => h('div.sf-det-row', h('span', k), h('b', String(v)))),
        top.length ? h('div.sf-det-sub', 'Top values') : null,
        top.map(([v, n]) => h('div.sf-bar', h('div.sf-bar-fill', { style: { width: `${Math.round(n / total * 100)}%` } }), h('span', v), h('b', n))));
    }
    return h('aside.sf-details', h('div.sf-det-h', h('span', 'Query Details')),
      h('div.sf-det-row', h('span', 'Query duration'), h('b', `${Math.round(last.elapsed * 1000)}ms`)),
      h('div.sf-det-row', h('span', 'Rows'), h('b', String(last.rows.length))),
      h('div.sf-det-row', h('span', 'Query ID'), h('b.mono', { title: last.query_id }, (last.query_id || '').slice(0, 13) + '…')),
      last.count > 1 ? h('div.sf-det-note', `Showing the last of ${last.count} statements. Earlier ones are in Query History.`) : null,
      h('div.sf-det-note', 'Click a column header to see its stats.'));
  }

  /* ---------------- history ---------------- */
  function renderHistory() {
    const hist = App.state?.query_history || [];
    return h('div.sf-page',
      h('header.sf-top', h('div.sf-h1', 'Query History'), h('div.sf-top-right', h('button.sf-share', { onclick: async () => { await refreshState(); render(); } }, '↻ Refresh'))),
      h('div.sf-hist',
        h('div.sf-hist-filters', h('span.chip', 'User: ME'), h('span.chip', 'Warehouse: DEV_WH'), h('span.chip', `${hist.length} queries`)),
        h('table.sf-hist-table',
          h('thead', h('tr', ['Status', 'SQL Text', 'Query ID', 'Warehouse', 'Duration', 'Started', 'Rows'].map(x => h('th', x)))),
          h('tbody', hist.map(q => h('tr', {
            onclick: () => {
              view = 'worksheet';
              saveCurrent();
              const id = Math.max(...sheets.map(s => s.id)) + 1;
              sheets.push({ id, name: `Query ${q.query_id.slice(0, 8)}`, sql: q.sql + '\n' });
              activeId = id;
              saveSheets();
              render();
            },
          },
          h('td', h(`span.qstatus.${q.status === 'Succeeded' ? 'ok' : 'bad'}`, q.status === 'Succeeded' ? '✓ Succeeded' : '⊗ Failed')),
          h('td.sqltext', q.sql.replace(/\s+/g, ' ').slice(0, 120)),
          h('td.mono', q.query_id.slice(0, 18) + '…'),
          h('td', q.warehouse),
          h('td', `${Math.round(q.elapsed * 1000)}ms`),
          h('td', q.started),
          h('td', q.rows)))),
        ),
      ),
    );
  }

  return {
    mount(el) { root = el; root.classList.add('sf'); render(); loadTree(); },
    show() { render(); if (!tree) loadTree(); },
    refresh() { loadTree(); },
  };
})();
