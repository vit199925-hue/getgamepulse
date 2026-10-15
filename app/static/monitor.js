const $ = (id) => document.getElementById(id);
const labels = {queued:'В очереди',running:'Обработка',completed:'Завершён',partial:'С ошибками',failed:'Ошибка',interrupted:'Прерван'};
const source = new EventSource('/api/events');
source.onopen = () => { $('connection').textContent = '● LIVE'; };
source.onerror = () => { $('connection').textContent = 'Переподключение…'; };
source.onmessage = (event) => {
  const state = JSON.parse(event.data);
  $('total').textContent = state.total_games;
  const active = state.runs.find(r => r.status === 'running');
  $('progress').textContent = active ? `${active.processed + active.failed} / ${active.selected}` : '—';
  $('worker').textContent = state.worker.owner ? 'Работает' : 'Нет обхода';
  $('next').textContent = 'Следующий обход: ' + state.worker.next_run.slice(0,19).replace('T',' ') + ' UTC';
  $('runs').replaceChildren(...state.runs.map(r => {
    const row = document.createElement('tr');
    [ `#${r.id} · ${r.started_at.slice(0,19).replace('T',' ')}`, labels[r.status] || r.status,
      `${r.processed} / ${r.selected}`, r.failed, r.error || r.current || '—' ].forEach(value => {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    });
    return row;
  }));
};
$('run-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('run-button').disabled = true;
  try {
    const result = await fetch('/api/runs', {method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}});
    const data = await result.json();
    $('notice').textContent = result.ok ? `Запуск #${data.run_id} поставлен в очередь.` : data.detail;
  } catch { $('notice').textContent = 'Не удалось связаться с сервером.'; }
  finally { $('run-button').disabled = false; }
});
