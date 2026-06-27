export type CompanyOption = { id: string; name: string; status?: string };

export function firstParam(value?: string | string[]) {
  return Array.isArray(value) ? value[0] : value;
}

export function selectedCompanyId(
  companies: CompanyOption[],
  requested?: string | string[],
  options: { defaultToSingleActive?: boolean } = {},
) {
  const value = firstParam(requested);
  if (value === 'all') return '';
  if (value && companies.some((company) => company.id === value)) return value;
  if (options.defaultToSingleActive !== true) return '';
  const active = companies.filter((company) => company.status !== 'Archived');
  return active.length === 1 ? active[0].id : '';
}

export function queryString(values: Record<string, string | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const text = params.toString();
  return text ? `?${text}` : '';
}
