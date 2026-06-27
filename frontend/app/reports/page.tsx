import { serverApi } from '../../lib/serverApi';
import { LocalTime } from '../../components/LocalTime';
import { SyncStatus, type SyncInfo } from '../../components/SyncStatus';
import { DailyReportPanel } from '../../components/DailyReportPanel';
import { CompanySelector } from '../../components/CompanySelector';
import { queryString, selectedCompanyId } from '../../lib/companySelection';

type Company = { id: string; name: string; status: string };

export default async function Reports({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const scopedQuery = queryString({ company_id: companyId || undefined });
  const [activity, sync, daily] = await Promise.all([
    serverApi<any[]>(`/activity${scopedQuery}`, []),
    serverApi<SyncInfo>('/sync/status', {}),
    serverApi<any>('/reports/daily?report_date=2026-06-26', null),
  ]);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Activity Logs</h1>
        <div className="flex items-center gap-4"><div className="text-sm text-zinc-400">{activity.length} events</div><SyncStatus sync={sync} /></div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} allowAll label="Reports scope" />
      <DailyReportPanel initialReport={daily?.report || null} initialText={daily?.text || ''} />
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Entity ID</th></tr></thead>
          <tbody>
            {activity.map((event) => <tr key={event.id}><td><LocalTime value={event.created_at} /></td><td className="font-medium text-stone-100">{event.action}</td><td>{event.entity_type}</td><td className="max-w-xs truncate text-zinc-400">{event.entity_id || '-'}</td></tr>)}
            {!activity.length ? <tr><td colSpan={4} className="text-zinc-400">No activity</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
