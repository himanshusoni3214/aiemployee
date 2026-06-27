'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';

type Option = { value: string; label: string };

export function QuerySelector({
  label,
  param,
  value,
  options,
  allLabel = 'All',
  resetParams = [],
}: {
  label: string;
  param: string;
  value?: string;
  options: Option[];
  allLabel?: string;
  resetParams?: string[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectId = `query-selector-${param}`;

  function change(nextValue: string) {
    const params = new URLSearchParams(searchParams.toString());
    resetParams.forEach((resetParam) => params.delete(resetParam));
    if (nextValue) params.set(param, nextValue);
    else params.delete(param);
    router.push(`${pathname}${params.toString() ? `?${params.toString()}` : ''}`);
  }

  return (
    <div className="grid min-w-64 gap-1 text-sm text-zinc-300">
      <label htmlFor={selectId}>{label}</label>
      <select id={selectId} className="input" value={value || ''} onChange={(event) => change(event.target.value)}>
        <option value="">{allLabel}</option>
        {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
    </div>
  );
}
