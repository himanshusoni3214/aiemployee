'use client';

import { useEffect, useMemo, useState } from 'react';

function normalizeUtc(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed)) return trimmed;
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(trimmed)) return `${trimmed.replace(' ', 'T')}Z`;
  return trimmed;
}

function fallbackLabel(value?: string | null) {
  if (!value) return '-';
  const normalized = normalizeUtc(value);
  return normalized.replace('T', ' ').replace(/Z$/, ' UTC');
}

export function formatLocalTime(value?: string | null) {
  if (!value) return '-';
  const normalized = normalizeUtc(value);
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date);
}

export function LocalTime({ value }: { value?: string | null }) {
  const initialLabel = useMemo(() => fallbackLabel(value), [value]);
  const [label, setLabel] = useState(initialLabel);

  useEffect(() => {
    setLabel(formatLocalTime(value));
  }, [value]);

  if (!value) return <span>-</span>;
  return <time suppressHydrationWarning dateTime={normalizeUtc(value)}>{label}</time>;
}
