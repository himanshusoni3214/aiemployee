'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect } from 'react';

type Company = { id: string; name: string; status?: string };

export function CompanySelector({
  companies,
  selectedCompanyId,
  allowAll = false,
  label = 'Company',
}: {
  companies: Company[];
  selectedCompanyId?: string;
  allowAll?: boolean;
  label?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeCompanies = companies.filter((company) => company.status !== 'Archived');
  const selectValue = selectedCompanyId || (allowAll ? '__all' : '');
  const selectId = `company-selector-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'scope'}`;

  useEffect(() => {
    if (selectedCompanyId) localStorage.setItem('voryx:selectedCompanyId', selectedCompanyId);
  }, [selectedCompanyId]);

  function changeCompany(nextCompanyId: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete('campaign_id');
    params.delete('employee_id');
    if (nextCompanyId === '__all') {
      params.set('company_id', 'all');
      localStorage.removeItem('voryx:selectedCompanyId');
    } else if (nextCompanyId) {
      params.set('company_id', nextCompanyId);
      localStorage.setItem('voryx:selectedCompanyId', nextCompanyId);
    } else {
      params.delete('company_id');
      localStorage.removeItem('voryx:selectedCompanyId');
    }
    router.push(`${pathname}${params.toString() ? `?${params.toString()}` : ''}`);
  }

  return (
    <div className="card flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
        <p className="text-lg font-semibold">{selectedCompanyId ? activeCompanies.find((company) => company.id === selectedCompanyId)?.name || 'Unknown company' : 'All companies'}</p>
      </div>
      <div className="grid min-w-64 gap-1 text-sm text-zinc-300">
        <label htmlFor={selectId}>Select company</label>
        <select id={selectId} className="input" value={selectValue} onChange={(event) => changeCompany(event.target.value)}>
          {allowAll ? <option value="__all">All companies</option> : <option value="">Select a company</option>}
          {activeCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
        </select>
      </div>
    </div>
  );
}
