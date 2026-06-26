'use client';

import { useEffect, useState } from 'react';

function normalizeUtc(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed)) return trimmed;
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(trimmed)) return `${trimmed.replace(' ', 'T')}Z`;
  return trimmed;
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
  const [label, setLabel] = useState('-');

  useEffect(() => {
    setLabel(formatLocalTime(value));
  }, [value]);

  if (!value) return <span>-</span>;
  return <time dateTime={normalizeUtc(value)}>{label}</time>;
}
