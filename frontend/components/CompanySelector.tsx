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

  useEffect(() => {
    if (selectedCompanyId) localStorage.setItem('voryx:selectedCompanyId', selectedCompanyId);
    const current = searchParams.get('company_id');
    if (selectedCompanyId && current !== selectedCompanyId) {
      const params = new URLSearchParams(searchParams.toString());
      params.set('company_id', selectedCompanyId);
      router.replace(`${pathname}?${params.toString()}`);
    }
  }, [selectedCompanyId, pathname, router, searchParams]);

  function changeCompany(nextCompanyId: string) {
    const params = new URLSearchParams(searchParams.toString());
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
      <label className="grid min-w-64 gap-1 text-sm text-zinc-300">
        <span>Select company</span>
        <select className="input" value={selectValue} onChange={(event) => changeCompany(event.target.value)}>
          {allowAll ? <option value="__all">All companies</option> : <option value="">Select a company</option>}
          {activeCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
        </select>
      </label>
    </div>
  );
}
