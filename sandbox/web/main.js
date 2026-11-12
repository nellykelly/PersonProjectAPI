/* Dock, app switching, coach drawer, reset. */
(async function main() {
  const mounted = new Set();

  App.show = async name => {
    App.current = name;
    store.set('app', name);
    document.querySelectorAll('#dock [data-app]').forEach(b => b.classList.toggle('on', b.dataset.app === name));
    document.querySelectorAll('.app').forEach(s => s.classList.toggle('on', s.id === `app-${name}`));
    const el = document.getElementById(`app-${name}`);
    if (!mounted.has(name)) { mounted.add(name); App.apps[name].mount(el); }
    else await App.apps[name].show?.();
    if (!document.getElementById('coach').classList.contains('hidden')) App.coach.refresh();
  };

  App.toggleCoach = force => {
    const el = document.getElementById('coach');
    const show = force ?? el.classList.contains('hidden');
    el.classList.toggle('hidden', !show);
    store.set('coach', show);
    document.getElementById('coach-toggle').classList.toggle('on', show);
    if (show) App.coach.refresh();
  };

  document.querySelectorAll('#dock [data-app]').forEach(b => b.addEventListener('click', () => App.show(b.dataset.app)));
  document.getElementById('coach-toggle').addEventListener('click', () => App.toggleCoach());
  document.getElementById('reset-btn').addEventListener('click', async () => {
    if (!(await confirmModal('Reset the sandbox?', 'This rebuilds the warehouse and the repo from scratch: your branch, edits, commits, PR, DAG runs and S3 files are all lost. Worksheets in Snowflake are kept.', 'Reset everything'))) return;
    const close = modal('Resetting', spinner('Restoring a clean ATLAS_DEV warehouse and repo (a few seconds)...'));
    const res = await api('/api/reset', {});
    close();
    if (!res.ok) { modal('Reset failed', h('pre', res.error || res.log)); return; }
    App.coach.reset();
    ['dbt.open', 'dbt.current', 'gh.draft'].forEach(k => store.set(k, null));
    location.reload();
  });

  const notice = document.getElementById('small-screen');
  if (notice && !store.get('smallScreenDismissed', false)) {
    notice.querySelector('button').addEventListener('click', () => { notice.remove(); store.set('smallScreenDismissed', true); });
  } else notice?.remove();

  App.coach.mount(document.getElementById('coach'));
  await refreshState();
  if (!window.CodeMirror) toast('Code editor library could not load (offline?): using plain text boxes.', 'warn');
  if (store.get('coach', window.innerWidth >= 1000)) App.toggleCoach(true);
  App.show(store.get('app', 'jira'));
})();
