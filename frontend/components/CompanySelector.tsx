'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

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
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeCompanies = companies.filter((company) => company.status !== 'Archived');
  const externalSelectValue =
    selectedCompanyId || (allowAll ? '__all' : '');
  const [selectValue, setSelectValue] =
    useState(externalSelectValue);
  const selectId = `company-selector-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'scope'}`;

  useEffect(() => {
    setSelectValue(externalSelectValue);
  }, [externalSelectValue]);

  useEffect(() => {
    if (selectedCompanyId) localStorage.setItem('voryx:selectedCompanyId', selectedCompanyId);
  }, [selectedCompanyId]);

  function changeCompany(nextCompanyId: string, select?: HTMLSelectElement) {
    setSelectValue(nextCompanyId);
    const params = new URLSearchParams(searchParams.toString());
    params.delete('campaign_id');
    params.delete('employee_id');
    if (nextCompanyId === '__all') {
      params.delete('company_id');
      localStorage.removeItem('voryx:selectedCompanyId');
    } else if (nextCompanyId) {
      params.set('company_id', nextCompanyId);
      localStorage.setItem('voryx:selectedCompanyId', nextCompanyId);
    } else {
      params.delete('company_id');
      localStorage.removeItem('voryx:selectedCompanyId');
    }
    const nextPath = `${pathname}${params.toString() ? `?${params.toString()}` : ''}`;

    if (typeof window !== 'undefined') {
      const targetHref = new URL(
        nextPath,
        window.location.href,
      ).toString();

      if (select) {
        select.dataset.voryxReactNavigationHref =
          targetHref;
      }

      if (window.location.href !== targetHref) {
        window.location.assign(targetHref);
      }
    }
  }

  return (
    <div className="card flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
        <p className="text-lg font-semibold">{selectedCompanyId ? activeCompanies.find((company) => company.id === selectedCompanyId)?.name || 'Unknown company' : 'All companies'}</p>
      </div>
      <div className="grid min-w-64 gap-1 text-sm text-zinc-300">
        <label htmlFor={selectId}>Select company</label>
        <select
          id={selectId}
          className="input"
          value={selectValue}
          onChange={(event) => changeCompany(event.target.value, event.currentTarget)}
          data-voryx-company-selector="true"
          data-voryx-company-param="company_id"
          data-voryx-allow-all={allowAll ? 'true' : 'false'}
        >
          {allowAll ? <option value="__all">All companies</option> : <option value="">Select a company</option>}
          {activeCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
        </select>
      </div>
    </div>
  );
}
