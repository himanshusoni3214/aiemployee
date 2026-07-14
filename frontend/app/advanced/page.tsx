const advancedLinks = [
  ['Raw Campaign Records', '/campaigns'],
  ['Raw Employees', '/employees'],
  ['Schedules', '/scheduler'],
  ['Jobs', '/jobs'],
  ['Logs', '/reports'],
  ['Health', '/system'],
  ['Model Policy', '/campaigns'],
  ['Data Files', '/leads'],
  ['Hermes Sync', '/system'],
];

export default function AdvancedPage() {
  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-zinc-500">Advanced</p>
        <h1 className="text-2xl font-semibold">Technical Operations</h1>
        <p className="text-sm text-zinc-400">Raw records, schedules, jobs, logs, health, model policy, data files and Hermes sync live here for operators.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {advancedLinks.map(([label, href]) => <a className="card hover:border-zinc-700" href={href} key={label}><h2 className="text-sm font-semibold">{label}</h2><p className="mt-1 text-xs text-zinc-500">Open {label.toLowerCase()}</p></a>)}
      </div>
    </div>
  );
}
