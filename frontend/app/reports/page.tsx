import { serverApi } from '../../lib/serverApi';

export default async function Reports() {
  const activity = await serverApi<any[]>('/activity', []);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Activity Logs</h1>
        <div className="text-sm text-zinc-400">{activity.length} events</div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Entity ID</th></tr></thead>
          <tbody>
            {activity.map((event) => <tr key={event.id}><td>{new Date(event.created_at).toLocaleString()}</td><td className="font-medium text-stone-100">{event.action}</td><td>{event.entity_type}</td><td className="max-w-xs truncate text-zinc-400">{event.entity_id || '-'}</td></tr>)}
            {!activity.length ? <tr><td colSpan={4} className="text-zinc-400">No activity</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
