/* GitHub: compare, pull request, CI checks, merge. */
App.apps.github = (() => {
  let root, tab = 'conversation', git = null, ciRunning = false, editingBody = false;
  const REPO = ['acme-insurance', 'atlas-conversion'];
  const TEMPLATE = `## What
<!-- What does this change do? -->

## Why
ATL-101: <!-- link the ticket and say why -->

## How I tested
- [ ] \`dbt run --select tag:views\` with no vars: row counts unchanged
- [ ] with \`--vars '{"policy_numbers": [...]}'\`: <!-- what you checked in Snowflake -->
- [ ] Airflow dev: triggered the DAG with config: <!-- what landed in S3 -->
`;

  async function load() {
    await refreshState();
    git = await api('/api/git');
    render();
    const pr = App.state.git.pr;
    const head = App.state.git.commits.at(-1)?.sha;
    if (pr && pr.state === 'open' && !ciRunning && (!pr.checks || pr.checks.sha !== head)) runCI();
  }

  function header() {
    return h('header.gh-top',
      h('span.gh-mark', '◉'),
      h('span.gh-repo', h('a', REPO[0]), ' / ', h('b', REPO[1])), h('span.gh-private', 'Private'),
      h('input.gh-search', { placeholder: 'Type / to search' }),
      h('span.gh-avatar', 'ME'),
    );
  }

  function repoTabs() {
    const pr = App.state?.git?.pr;
    return h('nav.gh-tabs', ['<> Code', '⊙ Issues', `⇄ Pull requests${pr && pr.state === 'open' ? ' 1' : ''}`, '▷ Actions', '⊞ Projects', '⚙ Settings']
      .map((t, i) => h(`span${i === 2 ? '.on' : ''}`, t)));
  }

  function render() {
    const g = App.state?.git || {};
    let body;
    if (!g.commits?.length && !g.pr) body = emptyPage(g);
    else if (!g.pr) body = comparePage(g);
    else body = prPage(g);
    root.replaceChildren(header(), repoTabs(), h('div.gh-body', body));
  }

  function emptyPage(g) {
    return h('div.gh-empty',
      h('div.gh-empty-icon', '⇄'),
      h('h3', 'There aren’t any open pull requests.'),
      h('p.muted', g.branch === 'main'
        ? 'Create a branch in the dbt IDE, make your change, then Commit and sync. Your branch will show up here.'
        : `You're on ${g.branch}. Commit and sync your changes in the dbt IDE, then come back to open a pull request.`),
    );
  }

  function comparePage(g) {
    const title = h('input.gh-input', { value: g.commits.at(-1).message.split('\n')[0] });
    const bodyBox = h('div.gh-md-editor');
    const ed = makeEditor(bodyBox, { value: store.get('gh.draft', TEMPLATE), mode: 'text/plain' });
    ed.on('change', () => store.set('gh.draft', ed.getValue()));
    setTimeout(() => ed.refresh(), 0);
    return h('div',
      h('h2.gh-h', 'Comparing changes'),
      h('p.muted', 'Choose two branches to see what’s changed or to start a new pull request.'),
      h('div.gh-compare', h('span.gh-ref', 'base: main'), '←', h('span.gh-ref', `compare: ${g.branch}`), h('span.gh-ok', '✓ Able to merge.'), h('span.muted', ' These branches can be automatically merged.')),
      h('div.gh-form',
        h('span.gh-avatar.big', 'ME'),
        h('div.gh-form-box',
          h('label', 'Add a title'), title,
          h('label', 'Add a description'), bodyBox,
          h('div.gh-form-foot', h('span.muted', 'Markdown is supported'),
            h('button.gh-green', { onclick: async () => {
              const res = await api('/api/git/pr', { title: title.value, body: ed.getValue() });
              if (!res.ok) { toast(res.error, 'error'); return; }
              tab = 'conversation';
              toast('Pull request opened');
              await load();
            } }, 'Create pull request')),
        ),
        h('aside.gh-sidebar', side('Reviewers', 'conversion-lead'), side('Assignees', 'You'), side('Labels', 'ATL-101'), side('Development', 'ATL-101 (Jira)')),
      ),
      h('div.gh-summary', `${g.commits.length} commit${g.commits.length > 1 ? 's' : ''} · ${git.branch_changes.length} file${git.branch_changes.length === 1 ? '' : 's'} changed`),
      filesChanged(git.branch_changes),
    );
  }

  function side(label, value) { return h('div.gh-side', h('div.gh-side-h', label, ' ⚙'), h('div', value)); }

  function prPage(g) {
    const pr = g.pr;
    const merged = pr.state === 'merged';
    const t = (id, label, n) => h(`span${tab === id ? '.on' : ''}`, { onclick: () => { tab = id; render(); } }, label, n != null ? h('span.gh-count', n) : null);
    return h('div',
      h('h1.gh-prtitle', pr.title, h('span.muted', ` #${pr.number}`)),
      h('div.gh-prmeta', h(`span.gh-state.${merged ? 'merged' : 'open'}`, merged ? '⇄ Merged' : '⇄ Open'),
        h('span', h('b', 'you'), merged ? ` merged ${g.commits.length} commit${g.commits.length > 1 ? 's' : ''} into ` : ` wants to merge ${g.commits.length} commit${g.commits.length > 1 ? 's' : ''} into `,
          h('code.gh-ref', 'main'), ' from ', h('code.gh-ref', g.branch))),
      h('div.gh-prtabs', t('conversation', '💬 Conversation', 0), t('commits', '◉ Commits', g.commits.length), t('checks', '☑ Checks', 1), t('files', '± Files changed', git.branch_changes.length)),
      tab === 'conversation' ? conversation(g) : tab === 'commits' ? commitsTab(g) : tab === 'checks' ? checksTab(pr) : filesChanged(git.branch_changes),
    );
  }

  function conversation(g) {
    const pr = g.pr;
    let bodyEl;
    if (editingBody) {
      const box = h('div.gh-md-editor');
      const ed = makeEditor(box, { value: pr.body || '', mode: 'text/plain' });
      setTimeout(() => ed.refresh(), 0);
      bodyEl = h('div', box, h('div.gh-form-foot',
        h('button.gh-btn', { onclick: () => { editingBody = false; render(); } }, 'Cancel'),
        h('button.gh-green', { onclick: async () => { await api('/api/git/pr/update', { fields: { body: ed.getValue() } }); editingBody = false; await load(); } }, 'Update comment')));
    } else {
      bodyEl = h('div.gh-mdbody', { html: md(pr.body) || '<p class="muted">No description provided.</p>' });
    }
    return h('div.gh-conv',
      h('div.gh-timeline',
        h('div.gh-comment',
          h('div.gh-comment-h', h('b', 'you'), h('span.muted', ` commented ${pr.opened}`), h('button.gh-link', { onclick: () => { editingBody = true; render(); } }, 'Edit')),
          h('div.gh-comment-b', bodyEl)),
        g.commits.map(c => h('div.gh-event', h('span.gh-event-dot', '◉'), h('span', c.message), h('code.gh-sha', c.sha))),
        checksBox(pr),
        mergeBox(pr),
      ),
      h('aside.gh-sidebar', side('Reviewers', 'conversion-lead'), side('Assignees', 'You'), side('Labels', 'ATL-101'), side('Development', 'ATL-101 (Jira)')),
    );
  }

  function checksBox(pr) {
    const c = pr.checks;
    const head = App.state.git.commits.at(-1)?.sha;
    const stale = c && c.sha !== head;
    const state = ciRunning ? 'running' : !c ? 'pending' : stale ? 'pending' : c.passed ? 'success' : 'failure';
    const label = {
      running: 'Some checks haven’t completed yet', pending: 'Checks haven’t run on the latest commit',
      success: 'All checks have passed', failure: 'Some checks were not successful',
    }[state];
    return h(`div.gh-checks.${state}`,
      h('div.gh-checks-h', h(`span.gh-ci-icon.${state}`, state === 'running' ? h('span.spin') : state === 'success' ? '✓' : state === 'failure' ? '✕' : '●'),
        h('div', h('b', label), h('div.muted', '1 required check'))),
      h('div.gh-check-row',
        h(`span.gh-ci-icon.small.${state}`, state === 'running' ? h('span.spin') : state === 'success' ? '✓' : state === 'failure' ? '✕' : '●'),
        h('span', h('b', 'atlas-ci / dbt build + ATL-101 acceptance tests'), ' ', h('span.muted', state === 'running' ? 'In progress (about a minute)' : c && !stale ? `${c.result?.score || ''} passed` : 'Waiting')),
        h('span.gh-check-actions', h('span.gh-required', 'Required'),
          h('button.gh-link', { onclick: () => { tab = 'checks'; render(); } }, 'Details'),
          h('button.gh-link', { onclick: runCI, disabled: ciRunning }, 'Re-run'))),
    );
  }

  function mergeBox(pr) {
    if (pr.state === 'merged') return h('div.gh-merged', h('span.gh-state.merged', '⇄'), h('div', h('b', 'Pull request successfully merged and closed'), h('div.muted', 'ATL-101 moved to Done. The branch can be deleted.')));
    const c = pr.checks;
    const head = App.state.git.commits.at(-1)?.sha;
    const ok = c?.passed && c.sha === head && !ciRunning;
    return h(`div.gh-merge.${ok ? 'ok' : 'blocked'}`,
      h('div', h('b', ok ? 'This branch has no conflicts with the base branch' : 'Merging is blocked'),
        h('div.muted', ok ? 'Merging can be performed automatically.' : 'The required check atlas-ci must pass on the latest commit.')),
      h('button.gh-green', { disabled: !ok, onclick: async () => {
        const res = await api('/api/git/merge', {});
        if (!res.ok) { toast(res.error, 'error'); return; }
        toast('Merged. ATL-101 is Done.');
        await load();
      } }, 'Merge pull request'));
  }

  function checksTab(pr) {
    const c = pr.checks;
    if (ciRunning) return h('div.gh-ci-log', spinner('atlas-ci is running: dbt run of EXPORT_VIEWS against a copy of ATLAS_DEV with each test\'s vars...'));
    if (!c) return h('div.gh-ci-log', h('p.muted', 'No checks yet.'), h('button.gh-btn', { onclick: runCI }, 'Run checks'));
    const r = c.result || {};
    return h('div.gh-ci-log',
      h('h3', `atlas-ci · ${r.score || ''} acceptance criteria · commit ${c.sha}`),
      r.error ? h('p.gh-fail', r.error) : null,
      (r.results || []).map(x => h(`div.gh-test.${x.ok ? 'pass' : x.bonus ? 'miss' : 'fail'}`,
        h('span.gh-test-icon', x.ok ? '✓' : x.bonus ? '○' : '✕'),
        h('div', h('b', `${x.id}  ${x.label}`), h('div.muted', x.detail)))),
      h('p.muted', 'CI runs the tests against what is committed and pushed, so commit and sync your latest changes before re-running.'),
    );
  }

  async function runCI() {
    if (ciRunning) return;
    ciRunning = true;
    render();
    await api('/api/ci', {});
    ciRunning = false;
    await refreshState();
    git = await api('/api/git');
    render();
  }

  function commitsTab(g) {
    return h('div.gh-commits', h('div.gh-commits-h', `Commits on ${g.branch}`),
      [...g.commits].reverse().map(c => h('div.gh-commit', h('div', h('b', c.message), h('div.muted', `you committed ${c.at} · ${c.files.length} file(s)`)), h('code.gh-sha', c.sha))));
  }

  function filesChanged(files) {
    if (!files?.length) return h('p.muted', 'No changes.');
    return h('div.gh-files', files.map(f => h('div.gh-file',
      h('div.gh-file-h', h('span.gh-adds', `+${f.additions}`), h('span.gh-dels', `−${f.deletions}`), h('b', f.path), h('span.muted', ` ${f.status}`)),
      renderDiff(f.diff))));
  }

  return {
    mount(el) { root = el; root.classList.add('gh'); load(); },
    show: load,
    refresh: load,
  };
})();
