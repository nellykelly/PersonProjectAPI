/* Practice coach: the thought process, hints, self-check and answer key. */
App.coach = (() => {
  let root, checking = false, checkResult = store.get('coach.check', null), answer = null;
  const ticks = store.get('coach.ticks', {});
  const hintsShown = store.get('coach.hints', {});
  let openStep = store.get('coach.open', 'read');

  const git = () => App.state?.git || {};
  const changedPaths = () => new Set([...(App.coach.gitInfo?.uncommitted || []), ...(App.coach.gitInfo?.branch_changes || [])].map(f => f.path));

  const STEPS = [
    { id: 'read', app: 'jira', title: 'Read the ticket and restate it',
      why: 'Before touching code, say what "done" means in one sentence and find where in the pipeline the change belongs.',
      hints: [
        'One-sentence version: "When the DAG is given a list of policy numbers, only those policies, and everything that migrates with them, reach S3."',
        'What decides what reaches S3? The EXPORT_VIEWS layer: the DAG unloads every view in it. So the filter goes in the views, not in STG/INT/EXPORT (those must stay complete for delta sync to work).',
        'Filters already exist there: macros/views_filter.sql handles record_state and changed_since with dbt vars. The DAG fills those vars from dag_run.conf. Your change follows the same path.',
        'The real ticket had no acceptance criteria. In real life you\'d ask the reporter before starting. Here they\'re in the ticket.',
      ] },
    { id: 'branch', app: 'dbt', title: 'Create a feature branch', auto: () => git().branch && git().branch !== 'main',
      why: 'main is protected. Work happens on a branch named after the ticket, so Jira links it automatically.',
      hints: ['dbt IDE → "Create branch" (top right). Something like feature/ATL-101-policy-number-filter.'] },
    { id: 'explore', app: 'snowflake', title: 'Explore the EXPORT tables in Snowflake',
      why: 'Learn what the views look like before changing them. The tricky part of this ticket is in the data, not the Jinja.',
      hints: [
        'Run  show views in schema export_views;  then  desc table export.vehicle;  and  desc table export.account;  Which of them has POLICY_NUMBER?',
        'Only POLICYPERIOD has POLICY_NUMBER. VEHICLE links to its policy term through PAYLOAD_ID (policy number | expiration date). ACCOUNT links through SOURCE_SYSTEM + ACCOUNT_NUMBER, which POLICYPERIOD also has.',
        'Compare  select account_number, count(*) from export.account group by 1 having count(*) > 1;  with the same query grouped by source_system, account_number. What happens to 50012?',
      ] },
    { id: 'prototype', app: 'snowflake', title: 'Prototype the filter as plain SQL',
      why: 'The team prototypes in a worksheet first, then moves it into dbt. Get the SQL right with hard-coded values, then make it dynamic.',
      hints: [
        'Write three SELECTs that keep only PN1001 and IW2002, one per view. POLICYPERIOD is easy:  where pp.policy_number in (\'PN1001\', \'IW2002\')',
        'VEHICLE: keep rows whose payload_id belongs to a selected policy. Use a subquery or EXISTS against export.policyperiod. Expect 5 vehicles for PN1001 + IW2002.',
        'ACCOUNT: match BOTH source_system and account_number to the selected policies. Test with PN1003: you should get 1 account (Priya Raman), not 2.',
      ] },
    { id: 'implement', app: 'dbt', title: 'Implement it in dbt', auto: () => changedPaths().has('macros/views_filter.sql'),
      why: 'Turn the prototype into Jinja: a var with a safe default, applied in the shared filter macro, so every view gets it.',
      hints: [
        'Copy the record_state pattern in views_filter():  {%- set policy_numbers = var(\'policy_numbers\', none) %}  then  {%- if policy_numbers %} ... {%- endif %}. quoted_list() already turns a list or a "a,b" string into SQL.',
        'Each view filters differently, so the macro has to know which view it\'s in. One way: add an entity argument, views_filter(\'veh\', \'vehicle\'), and update the three export_views models.',
        'Declare  policy_numbers: []  under vars: in dbt_project.yml. An empty list is falsy in Jinja, so the filter switches off by default.',
        'If dbt says it couldn\'t infer a dependency for a view, call ref(\'export_policyperiod\') outside the if block, or add  -- depends_on: {{ ref(\'export_policyperiod\') }}  to the view.',
        'Use Compile (Ctrl+Shift+Enter) on a view to see the SQL your Jinja produces. Compile uses the default vars, so to see the filtered SQL run  dbt compile --select export_views__vehicle --vars \'{"policy_numbers": ["PN1001"]}\'  and open Compiled code.',
      ] },
    { id: 'verify', app: 'dbt', title: 'Run it and verify in Snowflake',
      why: 'Prove it filters correctly AND that normal runs are unchanged. The regression check matters as much as the new feature.',
      hints: [
        'dbt run --select tag:views --vars \'{"policy_numbers": ["PN1001", "IW2002"]}\'   then in Snowflake:  select count(*) from export_views.policyperiod;  (and vehicle, account). Expect 2 / 5 / 2.',
        'Now  dbt run --select tag:views  with no vars. Counts must be back to 9 / 16 / 10.',
        'Edge cases: add "record_state": "KY", try an unknown number like ZZ9999, an empty list [], and the string "PN1001,IW2002".',
      ] },
    { id: 'dag', app: 'dbt', title: 'Pass it through the DAG', auto: () => changedPaths().has('dags/delta_sync_dag.py'),
      why: 'QA triggers from Airflow, so the conf has to reach dbt as a var.',
      hints: ['Open dags/delta_sync_dag.py. build_dbt_vars() copies keys from the conf into --vars. Add policy_numbers the same way, only when the conf has it.'] },
    { id: 'e2e', app: 'airflow', title: 'Test delta sync end to end in Airflow',
      auto: () => (App.state?.dag_runs || []).some(r => r.state === 'success' && r.conf?.policy_numbers),
      why: 'This is what the ticket is for: change something in legacy, run the DAG for just that policy, and see only it land in S3.',
      hints: [
        'Simulate agents editing legacy. In Snowflake:  update raw.pn_vehicle set make_nm = \'Honda Civic\' where pol_nbr = \'PN1001\' and veh_seq = 1;  and  update raw.pn_vehicle set make_nm = \'Mazda3\' where pol_nbr = \'PN1006\';',
        'Airflow → Trigger DAG w/ config:  {"policy_numbers": ["PN1001"]}. The incremental build picks up BOTH changes in EXPORT (check export.vehicle where record_action = \'UPDATE\'), but S3 should only get PN1001\'s vehicle.',
        'Open the run_dbt and export_views_to_s3 logs, then look at the CSVs in S3. Then run it again with {} and see PN1006\'s change go out on the next normal run.',
      ] },
    { id: 'check', title: 'Self-check against the acceptance criteria', auto: () => checkResult?.passed,
      why: 'Runs the same tests CI will, against your working copy (saved files, committed or not).', hints: [] },
    { id: 'pr', app: 'github', title: 'Commit, open the PR, get CI green, merge', auto: () => git().pr?.state === 'merged',
      why: 'Commit and sync in dbt, then open the PR on GitHub with a real description (what, why, how you tested). The required check must pass on the latest commit. Merging moves ATL-101 to Done.',
      hints: ['A good "How I tested" lists the commands you ran and the counts you saw, including the no-vars regression run and the Airflow run.'] },
  ];

  const done = s => !!(s.auto?.() || ticks[s.id]);

  function render() {
    if (!root) return;
    const completed = STEPS.filter(done).length;
    root.replaceChildren(
      h('div.coach-h', h('b', 'Practice coach'), h('span.muted', ` ${completed}/${STEPS.length}`), h('button.coach-x', { onclick: () => App.toggleCoach(false) }, '×')),
      h('div.coach-progress', h('div', { style: { width: `${completed / STEPS.length * 100}%` } })),
      h('p.coach-intro', 'Work through it the way you would on a real data team. Open a step for why it matters; reveal hints one at a time only when you\'re stuck.'),
      h('div.coach-steps', STEPS.map((s, i) => step(s, i))),
      h('div.coach-answer',
        h('button.btn', { onclick: showAnswer }, answer ? 'Hide answer key' : 'Show answer key…'),
        answer ? h('div', h('p.muted', 'One correct solution. Yours can differ: CI only checks behaviour.'), answer.files.map(f => h('div.gh-file', h('div.gh-file-h', h('b', f.path)), renderDiff(f.diff)))) : null),
    );
  }

  function step(s, i) {
    const isDone = done(s);
    const isOpen = openStep === s.id;
    const shown = hintsShown[s.id] || 0;
    return h(`div.coach-step${isDone ? '.done' : ''}${isOpen ? '.open' : ''}`,
      h('div.coach-step-h', { onclick: () => { openStep = isOpen ? null : s.id; store.set('coach.open', openStep); render(); } },
        h('span.coach-num', isDone ? '✓' : i + 1), h('span', s.title)),
      isOpen ? h('div.coach-step-b',
        h('p', s.why),
        s.id === 'check' ? checkBlock() : null,
        s.hints.slice(0, shown).map((t, k) => h('div.coach-hint', h('b', `Hint ${k + 1}`), h('div', t))),
        h('div.coach-step-actions',
          shown < s.hints.length ? h('button.btn', { onclick: () => { hintsShown[s.id] = shown + 1; store.set('coach.hints', hintsShown); render(); } }, shown ? 'Next hint' : 'Show a hint') : null,
          s.app ? h('button.btn', { onclick: () => App.show(s.app) }, `Open ${{ jira: 'Jira', dbt: 'dbt', snowflake: 'Snowflake', airflow: 'Airflow', github: 'GitHub' }[s.app]}`) : null,
          s.auto ? h('span.muted.small', isDone ? 'Detected automatically' : 'Ticks itself when done') :
            h('button.btn', { onclick: () => { ticks[s.id] = !ticks[s.id]; store.set('coach.ticks', ticks); render(); } }, ticks[s.id] ? 'Mark not done' : 'Mark done'),
        )) : null,
    );
  }

  function checkBlock() {
    return h('div.coach-check',
      h('button.btn.primary', { onclick: runCheck, disabled: checking }, checking ? h('span', h('span.spin.white'), ' Checking (about a minute)...') : 'Check my work'),
      checkResult ? h('div.coach-results',
        h('div', h('b', `Score ${checkResult.score || '-'}`), checkResult.error ? h('span.bad', ` ${checkResult.error}`) : null),
        (checkResult.results || []).map(r => h(`div.coach-res.${r.ok ? 'pass' : r.bonus ? 'miss' : 'fail'}`, h('span', r.ok ? '✓' : r.bonus ? '○' : '✕'), h('div', h('b', `${r.id} `), r.label, h('div.muted', r.detail))))) : null);
  }

  async function runCheck() {
    const unsaved = document.querySelectorAll('.dbt-tab .dbt-dot').length;
    if (unsaved) toast('You have unsaved files in the dbt IDE: the check uses saved files only.', 'warn');
    checking = true; render();
    checkResult = await api('/api/check', {});
    checking = false;
    store.set('coach.check', checkResult);
    render();
  }

  async function showAnswer() {
    if (answer) { answer = null; render(); return; }
    if (!(await confirmModal('Show the answer key?', 'Try the hints first: you learn more from getting stuck for a bit. Show the answer key anyway?', 'Show it'))) return;
    answer = await api('/api/answer');
    render();
  }

  return {
    gitInfo: null,
    mount(el) { root = el; },
    async refresh() { App.coach.gitInfo = await api('/api/git'); render(); },
    render,
    reset() { checkResult = null; answer = null; for (const k of Object.keys(ticks)) delete ticks[k]; for (const k of Object.keys(hintsShown)) delete hintsShown[k]; store.set('coach.ticks', {}); store.set('coach.hints', {}); store.set('coach.check', null); },
  };
})();
