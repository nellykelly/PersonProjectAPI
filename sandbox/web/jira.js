/* Jira: the ticket. */
App.apps.jira = (() => {
  let root;
  const STATUSES = ['To Do', 'In Progress', 'In Review', 'Done'];

  const AC = [
    'A new dbt var <code>policy_numbers</code> filters every EXPORT_VIEWS view to the selected policies.',
    '<b>POLICYPERIOD</b> returns only those policies, <b>VEHICLE</b> only their vehicles, and <b>ACCOUNT</b> only the accounts that own them.',
    'Works together with the existing <code>record_state</code> and <code>changed_since</code> filters.',
    'With no <code>policy_numbers</code> (or an empty list) the views return the full population, so normal runs don’t change.',
    'An unknown policy number returns no rows, not an error.',
    'The delta sync DAG passes <code>policy_numbers</code> from <code>dag_run.conf</code> through to dbt.',
  ];

  function statusClass(s) {
    return { 'To Do': 'todo', 'In Progress': 'progress', 'In Review': 'review', Done: 'done' }[s];
  }

  function render() {
    const st = App.state || {};
    const status = st.jira_status || 'To Do';
    const git = st.git || {};
    const branch = git.branch && git.branch !== 'main' ? git.branch : null;
    const pr = git.pr;

    const statusBtn = h(`button.jira-status.${statusClass(status)}`, {
      onclick: e => {
        e.stopPropagation();
        const menu = h('div.jira-menu', STATUSES.filter(s => s !== status).map(s =>
          h('button', { onclick: async () => { await api('/api/jira/status', { status: s }); await refreshState(); render(); } },
            h(`span.lozenge.${statusClass(s)}`, s.toUpperCase()))));
        e.currentTarget.after(menu);
        setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }));
      },
    }, status, ' ▾');

    root.replaceChildren(
      h('header.jira-top',
        h('span.jira-grid', '⋮⋮⋮'),
        h('span.jira-logo', h('svg', { viewBox: '0 0 24 24', html: '<path fill="currentColor" d="M12 2 3 11l9 9 9-9-9-9Zm0 5 4 4-4 4-4-4 4-4Z"/>' }), 'Jira'),
        ['Your work', 'Projects', 'Filters', 'Dashboards', 'Teams'].map(x => h('span.jira-nav', x, ' ▾')),
        h('button.jira-create', 'Create'),
        h('div.jira-search', '🔍 Search'),
      ),
      h('div.jira-body',
        h('aside.jira-side',
          h('div.jira-proj', h('span.jira-proj-icon', 'AC'), h('div', h('b', 'Atlas Conversion'), h('small', 'Software project'))),
          h('div.jira-side-h', 'PLANNING'),
          ['Timeline', 'Backlog', 'Board', 'Issues'].map(x => h(`div.jira-side-item${x === 'Board' ? '.on' : ''}`, x)),
          h('div.jira-side-h', 'DEVELOPMENT'),
          h('div.jira-side-item', 'Code'), h('div.jira-side-item', 'Releases'),
        ),
        h('div.jira-issue',
          h('div.jira-crumbs', 'Projects / Atlas Conversion / ', h('span.jira-epic', '⚡ ATL-90 Delta sync'), ' / ', h('span.jira-key', '✓ ATL-101')),
          h('div.jira-cols',
            h('div.jira-main',
              h('h1', 'Let the export run for a chosen list of policies'),
              h('div.jira-actions', h('button.jbtn', '📎 Attach'), h('button.jbtn', '☰ Add a child issue'), h('button.jbtn', '🔗 Link issue')),
              h('h3', 'Description'),
              h('div.jira-desc',
                h('p', 'QA wants to test the delta sync DAG against a few specific policies. Today the only option is a full run, which exports every changed record in the book.'),
                h('p', 'Add a way to pass a list of policy numbers so that only those policies, and the records that travel with them, are exported.'),
              ),
              h('h3', 'Acceptance criteria'),
              h('div.jira-note', 'Tickets often arrive without acceptance criteria, and asking for them is part of the job. These are the ones the PR\'s CI check grades.'),
              h('ol.jira-ac', AC.map(a => h('li', { html: a }))),
              h('h3', 'Activity'),
              h('div.jira-tabs', h('span.on', 'Comments'), h('span', 'History'), h('span', 'Work log')),
              comment('CL', 'Conversion Lead', '2 days ago',
                'QA wants to re-run delta sync for a handful of policies after each legacy fix. Running the DAG for the whole population takes ~2 hours and re-sends records nobody is testing. The filter belongs in the VIEWS layer: that\'s the gate to S3.'),
              comment('QA', 'QA Analyst', 'yesterday',
                'We\'d trigger it from the Airflow UI with "Trigger DAG w/ config". A JSON list like ["PN1001", "IW2002"] is fine, but if a plain "PN1001,IW2002" string worked too that would save us some typos.'),
              comment('CL', 'Conversion Lead', 'yesterday',
                'Heads-up: only POLICYPERIOD has policy_number. VEHICLE hangs off the policy through payload_id, and ACCOUNT through account_number. Also remember account numbers are only unique within a source system.'),
            ),
            h('aside.jira-details',
              h('div.jira-status-row', statusBtn, h('button.jbtn', '⚡ Actions')),
              h('div.jira-panel',
                h('div.jira-panel-h', 'Details'),
                row('Assignee', h('span.avatar-row', h('span.avatar', 'ME'), 'You')),
                row('Reporter', h('span.avatar-row', h('span.avatar.alt', 'CL'), 'Conversion Lead')),
                row('Development', branch || pr ? h('div.jira-dev',
                  branch ? h('a', { onclick: () => App.show('dbt') }, '⎇ 1 branch') : null,
                  (git.commits || []).length ? h('a', { onclick: () => App.show('github') }, `◉ ${git.commits.length} commit${git.commits.length > 1 ? 's' : ''}`) : null,
                  pr ? h('a', { onclick: () => App.show('github') }, '⇄ 1 pull request ', h(`span.lozenge.${pr.state === 'merged' ? 'done' : 'review'}`, pr.state.toUpperCase())) : null,
                ) : h('span.muted', 'Create branch')),
                row('Labels', h('span', h('span.label', 'delta_sync'), h('span.label', 'export_views'))),
                row('Sprint', h('a', 'Atlas Sprint 23')),
                row('Story point estimate', h('span.pill', '3')),
                row('Priority', h('span', '⬆ Medium')),
                row('Fix versions', h('span', 'Policies wave 2')),
              ),
              h('div.jira-meta', 'Created November 9, 2026', h('br'), 'Updated ', timeAgo(new Date().toISOString())),
            ),
          ),
        ),
      ),
    );
  }

  function row(label, value) {
    return h('div.jira-row', h('span.jira-row-l', label), h('span.jira-row-v', value));
  }

  function comment(initials, name, when, text) {
    return h('div.jira-comment', h('span.avatar.alt', initials),
      h('div', h('div', h('b', name), h('span.muted', '  ', when)), h('p', text)));
  }

  return {
    mount(el) { root = el; root.classList.add('jira'); render(); },
    show() { render(); },
    refresh: render,
  };
})();
