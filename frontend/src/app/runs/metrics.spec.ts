import { describe, expect, it } from 'vitest';
import { RunEvent } from '../api/client';
import { tokenUsageNote } from './metrics';

const event = (seq: number, data: Record<string, unknown>, event_type = 'metrics.updated'): RunEvent => ({ seq, data, event_type, run_id: 'run', timestamp: '2026-10-30T12:00:00Z' });
describe('C-03 token completeness', () => {
  it('does not infer a complete total without a supported metrics event', () => {
    expect(tokenUsageNote([])).toContain('полнота неизвестна');
    expect(tokenUsageNote([event(1, { schema_version:'2.0',tokens_complete:{ tokens_in:true,tokens_out:true } })])).toContain('полнота неизвестна');
  });
  it('marks partial totals and uses the last supported snapshot', () => {
    const first = event(1, { schema_version:'1.0',tokens_complete:{ tokens_in:true,tokens_out:false } });
    expect(tokenUsageNote([first])).toContain('расход неполный');
    expect(tokenUsageNote([first,event(2,{ schema_version:'1.0',tokens_complete:{ tokens_in:true,tokens_out:true } })])).toBe('Полный расход токенов');
  });
  it('requires both directions and ignores unrelated events', () => {
    expect(tokenUsageNote([event(1,{ schema_version:'1.0',tokens_complete:{ tokens_in:true } })])).toContain('полнота неизвестна');
    expect(tokenUsageNote([event(1,{ schema_version:'1.0',tokens_complete:{ tokens_in:true,tokens_out:true } },'unknown.type')])).toContain('полнота неизвестна');
  });
});

it('latest seq wins before schema filtering, regardless of delivery order', () => {
  const supported = event(1, {schema_version:'1.0', tokens_complete:{tokens_in:true,tokens_out:true}});
  const unknown = event(2, {schema_version:'2.0'});
  expect(tokenUsageNote([unknown, supported, supported])).toContain('полнота неизвестна');
});
