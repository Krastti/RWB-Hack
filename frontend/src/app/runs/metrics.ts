import { RunEvent } from '../api/client';

/** C-03: numeric totals alone do not establish completeness of provider usage. */
export function tokenUsageNote(events: RunEvent[]): string {
  const latest = events.filter(event => event.event_type === 'metrics.updated').reduce<RunEvent | undefined>((latest, event) => !latest || event.seq > latest.seq ? event : latest, undefined);
  const raw = latest?.data['schema_version'] === '1.0' ? latest.data['tokens_complete'] : undefined;
  const completeness = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  const incoming = completeness['tokens_in'];
  const outgoing = completeness['tokens_out'];
  if (incoming === true && outgoing === true) return 'Полный расход токенов';
  if (incoming === false || outgoing === false) return 'Известный расход · расход неполный';
  return 'Известный расход · полнота неизвестна';
}
