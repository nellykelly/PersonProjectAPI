/* dbt Cloud IDE: files, editor, command bar, results, version control. */
App.apps.dbt = (() => {
  let root, editor = null, files = [], git = null;
  const buffers = {};                 // path -> { saved, doc|text }
  let open = store.get('dbt.open', null) || ['models/export_views/export_views__policyperiod.sql', 'macros/views_filter.sql'];
  let current = store.get('dbt.current', null) || open[0];
  const collapsed = store.get('dbt.collapsed', {});
  let panelTab = 'results', preview = null, compiled = null, busy = null;
  let commands = [];
  let cmdHistory = store.get('dbt.cmdHistory', []), cmdIndex = -1;

  const onMain = () => (App.state?.git?.branch || 'main') === 'main';

  async function load() {
    const [f, g] = await Promise.all([api('/api/files'), api('/api/git')]);
    files = f.files || [];
    git = g;
    open = open.filter(p => files.includes(p));
    if (!open.length && files.length) open = [files.find(p => p.includes('export_views__policyperiod')) || files[0]];
    if (!open.includes(current)) current = open[0];
    for (const p of open) await ensureBuffer(p);
    render();
  }

  async function ensureBuffer(path) {
    if (buffers[path]) return buffers[path];
    const res = await api(`/api/file?path=${encodeURIComponent(path)}`);
    const text = res.text ?? '';
    buffers[path] = { saved: text, doc: window.CodeMirror ? CodeMirror.Doc(text, modeFor(path)) : null, text };
    return buffers[path];
  }

  const bufText = p => buffers[p]?.doc ? buffers[p].doc.getValue() : buffers[p]?.text ?? '';
  const dirty = p => buffers[p] && bufText(p) !== buffers[p].saved;

  /* ---------------- layout ---------------- */
  function render() {
    root.replaceChildren(
      h('header.dbt-top',
        h('div.dbt-brand', h('span.dbt-logo', '✕'), h('b', 'dbt'), h('span.dbt-sep', '|'), h('span', 'Develop')),
        h('div.dbt-proj', 'Atlas Conversion', h('span.dbt-env', 'Development')),
        h('div.dbt-top-right', branchChip(), gitButton(), h('span.dbt-avatar', 'ME')),
      ),
      h('div.dbt-main',
        h('aside.dbt-side', explorer(), versionControl()),
        h('div.dbt-work',
          h('div.dbt-tabs', open.map(p => h(`div.dbt-tab${p === current ? '.on' : ''}`, { onclick: () => switchTo(p), title: p },
            h('span', p.split('/').pop()), dirty(p) ? h('span.dbt-dot', '●') : null,
            h('span.dbt-tab-x', { onclick: e => { e.stopPropagation(); closeTab(p); } }, '×')))),
          onMain() ? h('div.dbt-readonly', '🔒 main is protected and read-only. ', h('a', { onclick: createBranch }, 'Create a branch'), ' to start editing.') : null,
          h('div.dbt-toolbar',
            h('span.dbt-path', current || 'No file open'),
            h('div.dbt-tools',
              h('button.dbt-btn', { onclick: save, disabled: onMain() || !current, title: 'Ctrl+S' }, 'Save'),
              h('button.dbt-btn', { onclick: compile, disabled: !isModel(), title: 'Ctrl+Shift+Enter' }, '</> Compile'),
              h('button.dbt-btn.primary', { onclick: doPreview, disabled: !isModel(), title: 'Ctrl+Enter' }, '▶ Preview'),
              h('button.dbt-btn', { onclick: buildMenu, disabled: !isModel() }, '⚒ Build ▾'),
            ),
          ),
          h('div.dbt-editor#dbt-editor'),
          h('div.dbt-panel', commandBar(), panelTabs(), h('div.dbt-panel-body#dbt-panel-body')),
        ),
      ),
    );
    mountEditor();
    renderPanel();
  }

  function isModel() { return !!current && current.startsWith('models/') && current.endsWith('.sql'); }

  function branchChip() {
    return h('span.dbt-branch', { title: 'Current branch' }, '⎇ ', App.state?.git?.branch || 'main');
  }

  function gitButton() {
    const g = App.state?.git || {};
    const uncommitted = (git?.uncommitted || []).length;
    if (onMain()) return h('button.dbt-git', { onclick: createBranch }, 'Create branch');
    if (uncommitted) return h('button.dbt-git', { onclick: commitDialog }, `Commit and sync (${uncommitted})`);
    if (g.pr?.state === 'merged') return h('button.dbt-git.done', { onclick: () => App.show('github') }, '✓ Merged');
    if (g.pr) return h('button.dbt-git.alt', { onclick: () => App.show('github') }, 'View pull request');
    if ((g.commits || []).length) return h('button.dbt-git', { onclick: () => App.show('github') }, 'Create a pull request on GitHub');
    return h('button.dbt-git.alt', { disabled: true }, 'Up to date');
  }

  /* ---------------- explorer ---------------- */
  function explorer() {
    const changed = Object.fromEntries((git?.uncommitted || []).map(c => [c.path, c.status]));
    const treeObj = {};
    for (const f of files) {
      let node = treeObj;
      const parts = f.split('/');
      parts.forEach((part, i) => {
        if (i === parts.length - 1) node[part] = f;
        else node = node[part] = node[part] || {};
      });
    }
    const rows = [];
    const walk = (node, depth, prefix) => {
      const entries = Object.entries(node).sort(([a, av], [b, bv]) => (typeof av === 'string') - (typeof bv === 'string') || a.localeCompare(b));
      for (const [name, val] of entries) {
        const key = prefix + name;
        if (typeof val === 'string') {
          const mark = changed[val];
          rows.push(h(`div.dbt-file${val === current ? '.on' : ''}${mark ? '.changed' : ''}`, { style: { paddingLeft: `${12 + depth * 14}px` }, onclick: () => openFile(val), title: val },
            h('span.dbt-ficon', fileIcon(name)), h('span.dbt-fname', name), mark ? h('span.dbt-mark', mark === 'added' ? 'A' : mark === 'removed' ? 'D' : 'M') : null));
        } else {
          rows.push(h('div.dbt-folder', { style: { paddingLeft: `${12 + depth * 14}px` }, onclick: () => { collapsed[key] = !collapsed[key]; store.set('dbt.collapsed', collapsed); render(); } },
            h('span.caret', collapsed[key] ? '▸' : '▾'), '📁 ', name));
          if (!collapsed[key]) walk(val, depth + 1, key + '/');
        }
      }
    };
    walk(treeObj, 0, '');
    return h('div.dbt-explorer',
      h('div.dbt-side-h', h('span', 'File explorer'), h('span',
        h('button.dbt-icon', { title: 'New file', onclick: newFile, disabled: onMain() }, '+'),
        h('button.dbt-icon', { title: 'Refresh', onclick: load }, '↻'))),
      h('div.dbt-files', rows));
  }

  function fileIcon(name) {
    if (name.endsWith('.sql')) return '◇';
    if (/\.ya?ml$/.test(name)) return '≡';
    if (name.endsWith('.py')) return '🐍';
    if (name.endsWith('.csv')) return '▤';
    return '·';
  }

  function versionControl() {
    const changes = git?.uncommitted || [];
    const commits = App.state?.git?.commits || [];
    return h('div.dbt-vc',
      h('div.dbt-side-h', h('span', 'Version control')),
      h('div.dbt-vc-body',
        changes.length ? changes.map(c => h('div.dbt-vc-file', { onclick: () => c.status !== 'removed' && openFile(c.path) },
          h(`span.dbt-mark.${c.status}`, c.status === 'added' ? 'A' : c.status === 'removed' ? 'D' : 'M'), c.path))
          : h('div.muted', onMain() ? 'On main. Create a branch to make changes.' : 'No uncommitted changes.'),
        commits.length ? h('div.dbt-vc-commits', h('div.dbt-side-sub', 'Commits on this branch'), commits.map(c => h('div.dbt-commit', h('code', c.sha), ' ', c.message))) : null,
      ));
  }

  /* ---------------- editor ---------------- */
  function mountEditor() {
    const host = root.querySelector('#dbt-editor');
    if (!current) { host.append(h('div.dbt-empty', 'Open a file from the explorer.')); return; }
    const keys = {
      'Ctrl-S': save, 'Cmd-S': save,
      'Ctrl-Enter': doPreview, 'Cmd-Enter': doPreview,
      'Shift-Ctrl-Enter': compile, 'Shift-Cmd-Enter': compile,
    };
    editor = makeEditor(host, { value: bufText(current), mode: modeFor(current), readOnly: onMain(), extraKeys: keys });
    if (editor.cm && buffers[current]?.doc) {
      const doc = buffers[current].doc;
      if (doc.cm) doc.cm.swapDoc(CodeMirror.Doc(''));  // detach from the old editor
      editor.cm.swapDoc(doc);
    }
    editor.on('change', () => {
      if (!editor.cm) buffers[current].text = editor.getValue();
      const tab = root.querySelector('.dbt-tab.on');
      const isDirty = dirty(current);
      if (tab && !!tab.querySelector('.dbt-dot') !== isDirty) {
        if (isDirty) tab.children[0].after(h('span.dbt-dot', '●')); else tab.querySelector('.dbt-dot')?.remove();
      }
    });
    setTimeout(() => editor.refresh(), 0);
  }

  async function openFile(path) {
    await ensureBuffer(path);
    if (!open.includes(path)) open.push(path);
    switchTo(path);
  }

  function switchTo(path) {
    current = path;
    store.set('dbt.open', open);
    store.set('dbt.current', current);
    render();
  }

  function closeTab(path) {
    if (dirty(path) && !confirm(`Discard unsaved changes to ${path}?`)) return;
    if (dirty(path)) { delete buffers[path]; }
    open = open.filter(p => p !== path);
    if (current === path) current = open[open.length - 1] || null;
    store.set('dbt.open', open);
    store.set('dbt.current', current);
    render();
  }

  async function save() {
    if (!current) return;
    if (onMain()) { toast('main is protected: create a branch first', 'warn'); return; }
    const text = bufText(current);
    const res = await api('/api/file', { path: current, text });
    if (res.ok === false) { toast(res.error, 'error'); return; }
    buffers[current].saved = text;
    git = await api('/api/git');
    toast(`Saved ${current.split('/').pop()}`);
    render();
  }

  async function newFile() {
    const input = h('input.input', { value: 'models/', placeholder: 'models/my_model.sql' });
    modal('New file', h('div', h('label', 'Path'), input), [{ label: 'Cancel' }, {
      label: 'Create', kind: 'btn.primary', onclick: async () => {
        const path = input.value.trim();
        if (!path || path.endsWith('/')) { toast('Give the file a name', 'warn'); return false; }
        await api('/api/file', { path, text: '' });
        await load();
        await openFile(path);
      },
    }]);
    setTimeout(() => input.focus(), 0);
  }

  /* ---------------- dbt actions ---------------- */
  async function unsavedGuard() {
    const unsaved = open.filter(dirty);
    if (unsaved.length) toast(`Unsaved: ${unsaved.map(p => p.split('/').pop()).join(', ')}. dbt runs what's saved on disk.`, 'warn');
  }

  async function compile() {
    if (!isModel()) return;
    await unsavedGuard();
    panelTab = 'compiled'; busy = 'Compiling...'; renderPanel();
    compiled = await api('/api/compile', { path: current });
    busy = null; renderPanel();
  }

  async function doPreview() {
    if (!isModel()) return;
    await unsavedGuard();
    panelTab = 'results'; busy = 'Running preview...'; renderPanel();
    preview = await api('/api/preview', { path: current });
    if (preview.sql) compiled = { ok: true, sql: preview.sql };
    busy = null; renderPanel();
  }

  function buildMenu(e) {
    e.stopPropagation();
    const name = current.split('/').pop().replace('.sql', '');
    const menu = h('div.dbt-menu',
      ['build', 'run', 'test'].map(c => h('button', { onclick: () => runCommand(`dbt ${c} --select ${name}`) }, `${c[0].toUpperCase() + c.slice(1)} model`)),
      h('button', { onclick: () => runCommand(`dbt build --select +${name}`) }, 'Build model +upstream'),
      h('button', { onclick: () => runCommand(`dbt build --select ${name}+`) }, 'Build model +downstream'),
    );
    e.currentTarget.parentElement.append(menu);
    setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }));
  }

  function commandBar() {
    const input = h('input.dbt-cmd', {
      value: 'dbt ', spellcheck: 'false', placeholder: 'dbt run --select tag:views',
      onkeydown: e => {
        if (e.key === 'Enter') runCommand(e.target.value);
        else if (e.key === 'ArrowUp' && cmdHistory.length) { cmdIndex = Math.min(cmdIndex + 1, cmdHistory.length - 1); e.target.value = cmdHistory[cmdIndex]; e.preventDefault(); }
        else if (e.key === 'ArrowDown') { cmdIndex = Math.max(cmdIndex - 1, -1); e.target.value = cmdIndex < 0 ? 'dbt ' : cmdHistory[cmdIndex]; e.preventDefault(); }
      },
    });
    return h('div.dbt-cmdbar', h('span.dbt-prompt', '›'), input,
      h('button.dbt-btn.primary', { onclick: () => runCommand(input.value) }, 'Run'));
  }

  async function runCommand(command) {
    command = command.trim();
    if (!command || command === 'dbt') return;
    await unsavedGuard();
    cmdHistory = [command, ...cmdHistory.filter(c => c !== command)].slice(0, 30);
    store.set('dbt.cmdHistory', cmdHistory);
    cmdIndex = -1;
    const entry = { command, status: 'running', at: new Date().toLocaleTimeString(), nodes: [], log: '' };
    commands.unshift(entry);
    panelTab = 'commands';
    renderPanel();
    const res = await api('/api/dbt', { command });
    Object.assign(entry, { status: res.code === 0 ? 'success' : 'error', nodes: res.nodes || [], log: res.log || res.error || '', elapsed: res.elapsed, open: true });
    commands.slice(1).forEach(c => (c.open = false));
    renderPanel();
    App.apps.snowflake.refresh?.();
  }

  /* ---------------- bottom panel ---------------- */
  function panelTabs() {
    const tab = (id, label) => h(`button${panelTab === id ? '.on' : ''}`, { onclick: () => { panelTab = id; renderPanel(); render(); } }, label);
    return h('div.dbt-ptabs', tab('results', 'Results'), tab('compiled', 'Compiled code'), tab('commands', `Commands${commands.length ? ` (${commands.length})` : ''}`));
  }

  function renderPanel() {
    const host = root.querySelector('#dbt-panel-body');
    if (!host) return;
    root.querySelectorAll('.dbt-ptabs button').forEach(b => b.classList.toggle('on', b.textContent.toLowerCase().startsWith(panelTab.slice(0, 5))));
    if (busy && panelTab !== 'commands') { host.replaceChildren(spinner(busy)); return; }
    if (panelTab === 'results') {
      if (!preview) host.replaceChildren(h('div.muted.pad', 'Preview a model (Ctrl+Enter) to see up to 500 rows of what it returns.'));
      else if (!preview.ok) host.replaceChildren(h('pre.dbt-err', preview.error));
      else host.replaceChildren(h('div.dbt-res-info', `${preview.rows.length} rows · ${preview.elapsed}s`), renderGrid(preview.columns, preview.rows));
    } else if (panelTab === 'compiled') {
      if (!compiled) host.replaceChildren(h('div.muted.pad', 'Compile a model (Ctrl+Shift+Enter) to see the SQL dbt sends to Snowflake.'));
      else if (!compiled.ok) host.replaceChildren(h('pre.dbt-err', compiled.error));
      else {
        const box = h('div.dbt-compiled');
        host.replaceChildren(box);
        makeEditor(box, { value: compiled.sql, mode: 'text/x-sql', readOnly: true }).refresh();
      }
    } else {
      host.replaceChildren(commands.length ? h('div.dbt-cmds', commands.map(c => h('div.dbt-run',
        h('div.dbt-run-h', { onclick: () => { c.open = !c.open; renderPanel(); } },
          h(`span.dbt-st.${c.status}`, c.status === 'running' ? h('span.spin') : c.status === 'success' ? '✓' : '✕'),
          h('code', c.command), h('span.muted', c.elapsed ? `${c.elapsed}s · ${c.at}` : c.at)),
        c.open ? h('div.dbt-run-body',
          c.nodes.length ? h('div.dbt-nodes', c.nodes.map(n => h('div.dbt-node', h(`span.dbt-badge.${/OK|PASS/.test(n.status) ? 'ok' : n.status === 'WARN' ? 'warn' : n.status === 'SKIP' ? 'skip' : 'bad'}`, n.status), h('span', n.name), h('span.muted', n.detail)))) : null,
          h('details', { open: c.status === 'error' || !c.nodes.length }, h('summary', 'Logs'), h('pre.dbt-log', c.log))) : null,
      ))) : h('div.muted.pad', 'Run a dbt command from the bar above, e.g.  dbt run --select tag:views --vars \'{"record_state": "KY"}\''));
    }
  }

  /* ---------------- git ---------------- */
  function createBranch() {
    const input = h('input.input', { value: 'feature/ATL-101-policy-number-filter' });
    modal('Create new branch', h('div', h('p.muted', 'Branch off main. Name it after the ticket so Jira links it.'), input), [
      { label: 'Cancel' },
      { label: 'Submit', kind: 'btn.primary', onclick: async () => {
        const res = await api('/api/git/branch', { name: input.value });
        if (!res.ok) { toast(res.error, 'error'); return false; }
        await refreshState();
        toast(`Checked out ${input.value}`);
        render();
      } },
    ]);
    setTimeout(() => input.select(), 0);
  }

  async function commitDialog() {
    const unsaved = open.filter(dirty);
    if (unsaved.length) { toast(`Save first: ${unsaved.map(p => p.split('/').pop()).join(', ')}`, 'warn'); return; }
    git = await api('/api/git');
    const input = h('textarea.input', { rows: 3, placeholder: 'ATL-101: add policy_numbers filter to EXPORT_VIEWS' });
    modal('Commit and sync', h('div',
      h('div.muted', 'Changes to commit:'),
      h('ul.dbt-commit-list', git.uncommitted.map(c => h('li', h(`span.dbt-mark.${c.status}`, c.status[0].toUpperCase()), ' ', c.path))),
      h('label', 'Commit message'), input), [
      { label: 'Cancel' },
      { label: 'Commit Changes', kind: 'btn.primary', onclick: async () => {
        const res = await api('/api/git/commit', { message: input.value });
        if (!res.ok) { toast(res.error, 'error'); return false; }
        await refreshState();
        git = await api('/api/git');
        toast(`Committed ${res.sha} and pushed to origin`);
        render();
      } },
    ]);
    setTimeout(() => input.focus(), 0);
  }

  return {
    mount(el) { root = el; root.classList.add('dbtide'); root.replaceChildren(spinner('Starting IDE...')); load(); },
    async show() { await refreshState(); git = await api('/api/git'); render(); },
    refresh: load,
  };
})();
