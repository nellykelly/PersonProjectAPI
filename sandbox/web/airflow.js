/* Airflow 2 UI: DAG list, grid, graph, code, trigger with config. */
App.apps.airflow = (() => {
  let root, page = 'list', tab = 'grid', selected = null, running = false;
  const DAG_ID = 'atlas_delta_sync';
  const TASKS = ['plan_commands', 'run_dbt', 'export_views_to_s3'];
  let confText = store.get('af.conf', '{\n  "policy_numbers": ["PN1001"]\n}');

  const runs = () => App.state?.dag_runs || [];

  function nav() {
    return h('header.af-top',
      h('span.af-logo', h('span.af-pin', '✺'), 'Airflow'),
      ['DAGs', 'Cluster Activity', 'Datasets', 'Security', 'Browse', 'Admin', 'Docs'].map(x =>
        h(`span.af-nav${x === 'DAGs' ? '.on' : ''}`, { onclick: () => x === 'DAGs' && go('list') }, x)),
      h('span.af-right', h('span.af-env', 'dev'), '  ', new Date().toUTCString().slice(17, 22), ' UTC  ', h('span.af-user', 'ME')),
    );
  }

  function go(p, t) { page = p; if (t) tab = t; render(); }

  function render() {
    root.replaceChildren(nav(), page === 'list' ? listPage() : page === 'trigger' ? triggerPage() : dagPage());
  }

  function stateDot(state, title) {
    return h(`span.af-dot.${state || 'none'}`, { title: title || state || 'no status' });
  }

  /* ---------------- DAG list ---------------- */
  function listPage() {
    const last = runs()[0];
    const counts = s => runs().filter(r => r.state === s).length;
    const row = (id, owner, schedule, tags, isOurs) => h('tr',
      h('td', h('span.af-toggle.on', { title: 'Pause/Unpause' })),
      h('td', h('a.af-dagid', { onclick: () => isOurs && go('dag', 'grid') }, id), h('div', tags.map(t => h('span.af-tag', t)))),
      h('td', owner),
      h('td', isOurs ? h('span.af-runs', h('span.af-count.success', counts('success')), h('span.af-count.failed', counts('failed'))) : h('span.af-runs', h('span.af-count.success', 61))),
      h('td', h('code', schedule)),
      h('td', isOurs && last ? h('span', last.started.slice(0, 16)) : isOurs ? h('span.muted', 'never') : '2026-11-11 05:00'),
      h('td', isOurs ? '2026-11-12, 05:00:00' : '2026-11-12, 05:00:00'),
      h('td', isOurs && last ? last.tasks.map(t => stateDot(t.state, `${t.task_id}: ${t.state}`)) : isOurs ? null : TASKS.map(() => stateDot('success'))),
      h('td.af-actions', isOurs ? h('button.af-icon', { title: 'Trigger DAG w/ config', onclick: () => go('trigger') }, '▶') : h('button.af-icon', { disabled: true }, '▶'),
        h('button.af-icon', { disabled: true }, '↻'), h('button.af-icon', { disabled: true }, '🗑')),
    );
    return h('div.af-page',
      h('h2', 'DAGs'),
      h('div.af-filters', h('span.af-pill.on', 'All 3'), h('span.af-pill', 'Active 3'), h('span.af-pill', 'Paused 0'), h('input.af-search', { placeholder: 'Search DAGs' })),
      h('div.af-note', 'Dev Airflow: DAG files sync from your dbt IDE branch, so DAG edits show up here as soon as they\'re saved.'),
      h('table.af-table',
        h('thead', h('tr', ['', 'DAG', 'Owner', 'Runs', 'Schedule', 'Last Run', 'Next Run', 'Recent Tasks', 'Actions'].map(x => h('th', x)))),
        h('tbody',
          row(DAG_ID, 'atlas', '0 5 * * *', ['atlas', 'delta_sync'], true),
          row('atlas_initial_load', 'atlas', 'None', ['atlas'], false),
          row('atlas_reporting_refresh', 'data_platform', '@hourly', ['reporting'], false),
        ),
      ),
    );
  }

  /* ---------------- DAG page ---------------- */
  function dagPage() {
    const t = (id, label) => h(`button${tab === id ? '.on' : ''}`, { onclick: () => go('dag', id) }, label);
    return h('div.af-page',
      h('div.af-daghead',
        h('div', h('span.af-toggle.on'), h('span.af-dagtitle', 'DAG: ', h('b', DAG_ID)), h('span.af-sub', ' Delta sync: incremental EXPORT + filtered views to S3')),
        h('div.af-daghead-r',
          h('span.af-meta', 'Schedule: ', h('code', '0 5 * * *')),
          h('button.af-trigger', { onclick: () => go('trigger'), disabled: running }, running ? h('span.spin.white') : '▶', ' Trigger DAG w/ config'),
        ),
      ),
      h('div.af-tabs', t('grid', '▦ Grid'), t('graph', '⬡ Graph'), t('code', '</> Code'), t('details', 'ℹ Details')),
      tab === 'grid' ? gridView() : tab === 'graph' ? graphView() : tab === 'code' ? codeView() : detailsView(),
    );
  }

  function gridView() {
    const list = [...runs()].reverse().slice(-20);
    if (running) list.push({ run_id: 'running', state: 'running', tasks: TASKS.map(id => ({ task_id: id, state: 'running' })), started: '' });
    const sel = selected && runs().find(r => r.run_id === selected.run);
    const ti = sel?.tasks.find(t => t.task_id === selected.task);
    return h('div.af-grid-wrap',
      h('div.af-grid',
        h('div.af-grid-rows',
          h('div.af-grid-label.head', ''),
          TASKS.map(id => h('div.af-grid-label', h('span.af-op', id === 'run_dbt' ? 'BashOperator' : 'PythonOperator'), id))),
        h('div.af-grid-cols', list.length ? list.map(r => h('div.af-col',
          h(`div.af-runbar.${r.state}`, { title: `${r.run_id}\n${r.state}` }),
          TASKS.map(id => {
            const t = r.tasks.find(x => x.task_id === id) || { state: 'none' };
            const isSel = selected && selected.run === r.run_id && selected.task === id;
            return h(`div.af-cell.${t.state}${isSel ? '.sel' : ''}`, { title: `${id}\n${t.state}`, onclick: () => { if (r.run_id !== 'running') { selected = { run: r.run_id, task: id }; render(); } } });
          }),
        )) : h('div.af-empty', 'No DAG runs yet. Use "Trigger DAG w/ config".')),
      ),
      h('aside.af-detail', sel && ti ? [
        h('div.af-detail-h', h('b', ti.task_id), ' ', h(`span.af-state.${ti.state}`, ti.state)),
        h('div.af-detail-meta', 'Run: ', h('code', sel.run_id), h('br'), 'Conf: ', h('code', JSON.stringify(sel.conf))),
        h('div.af-tabs.small', h('button.on', 'Logs'), h('button', { disabled: true }, 'XCom'), h('button', { disabled: true }, 'Details')),
        h('pre.af-log', ti.log || '(no log: task did not run)'),
      ] : h('div.af-empty', 'Select a task instance to see its logs.')),
    );
  }

  function graphView() {
    const last = runs()[0];
    const st = id => last?.tasks.find(t => t.task_id === id)?.state || 'none';
    return h('div.af-graph',
      TASKS.map((id, i) => [i ? h('span.af-arrow', '──▶') : null, h(`div.af-node.${st(id)}`, h('small', id === 'run_dbt' ? 'BashOperator' : 'PythonOperator'), h('b', id))]),
      h('div.af-graph-note', last ? `Showing the latest run: ${last.run_id} (${last.state})` : 'No runs yet.'),
    );
  }

  function codeView() {
    const box = h('div.af-code');
    api('/api/dag/source').then(res => {
      box.replaceChildren(h('div.af-code-h', 'Parsed at: ', new Date().toLocaleString(), ' · dags/delta_sync_dag.py (edit it in the dbt IDE)'));
      makeEditor(box, { value: res.text || res.error, mode: 'python', readOnly: true }).refresh();
    });
    box.append(spinner('Loading DAG code...'));
    return box;
  }

  function detailsView() {
    const rows = [['dag_id', DAG_ID], ['owner', 'atlas'], ['schedule_interval', '0 5 * * *'], ['catchup', 'False'],
      ['tags', 'atlas, delta_sync'], ['fileloc', '/opt/airflow/dags/delta_sync_dag.py'],
      ['total runs', runs().length], ['last run', runs()[0]?.run_id || 'never']];
    return h('table.af-table.af-kv', h('tbody', rows.map(([k, v]) => h('tr', h('th', k), h('td', String(v))))));
  }

  /* ---------------- trigger ---------------- */
  function triggerPage() {
    const box = h('div.af-conf');
    const ed = makeEditor(box, { value: confText, mode: { name: 'javascript', json: true } });
    ed.on('change', () => { confText = ed.getValue(); store.set('af.conf', confText); });
    setTimeout(() => ed.refresh(), 0);
    const examples = [
      ['Targeted: two policies', '{\n  "policy_numbers": ["PN1001", "IW2002"]\n}'],
      ['Targeted + everything (ignore changed_since)', '{\n  "policy_numbers": ["PN1001"],\n  "changed_since": "1900-01-01"\n}'],
      ['String form', '{\n  "policy_numbers": "PN1001,IW2002"\n}'],
      ['One state', '{\n  "record_state": "KY"\n}'],
      ['Normal daily run', '{}'],
    ];
    return h('div.af-page',
      h('h2', 'Trigger DAG: ', DAG_ID),
      h('div.af-form',
        h('label', 'Logical date'), h('input.input', { value: new Date().toISOString().slice(0, 19).replace('T', ' ') + '+00:00', disabled: true }),
        h('label', 'Run id (optional)'), h('input.input', { placeholder: 'manual__<timestamp>', disabled: true }),
        h('label', 'Configuration JSON (Optional, must be a dict object)'), box,
        h('div.af-examples', h('span.muted', 'Examples: '), examples.map(([label, v]) => h('button.af-chip', { onclick: () => { ed.setValue(v); } }, label))),
        h('div.af-help', 'The scheduler fills ', h('code', 'changed_since'), ' with the previous successful run\'s start, so a run only exports what changed since then (that is delta sync). To see something exported, change it in legacy first (an UPDATE on a RAW table in Snowflake), or pass changed_since yourself.'),
        h('div.af-form-actions',
          h('button.af-trigger', { onclick: trigger }, 'Trigger'),
          h('button.af-btn', { onclick: () => go('dag', 'grid') }, 'Cancel')),
      ),
    );
  }

  async function trigger() {
    let conf;
    try { conf = JSON.parse(confText || '{}'); } catch (e) { toast(`Invalid JSON: ${e.message}`, 'error'); return; }
    if (typeof conf !== 'object' || Array.isArray(conf) || conf === null) { toast('The conf must be a JSON object', 'error'); return; }
    running = true;
    go('dag', 'grid');
    toast(`Triggered ${DAG_ID}`);
    const run = await api('/api/dag/trigger', { conf });
    running = false;
    await refreshState();
    if (run.run_id) selected = { run: run.run_id, task: run.state === 'success' ? 'export_views_to_s3' : (run.tasks.find(t => t.state === 'failed')?.task_id || 'plan_commands') };
    toast(run.state === 'success' ? 'DAG run succeeded' : `DAG run ${run.state || 'failed'}`, run.state === 'success' ? 'info' : 'error');
    render();
    App.apps.snowflake.refresh?.();
  }

  return {
    mount(el) { root = el; root.classList.add('af'); render(); },
    async show() { await refreshState(); render(); },
    refresh: render,
  };
})();
