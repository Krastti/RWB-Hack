import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { ApiClient, Run, RunEvent } from '../api/client';
import { ApiError } from '../api/errors';
import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Session } from '../auth/session';
import { RunMonitor, RunSnapshot } from './monitor';

const event = (seq: number): RunEvent => ({ seq, run_id: 'run', event_type: 'stage', timestamp: '2026-10-30T12:00:00Z', data: {} });
const run: Run = { run_id: 'run', project_id: 'project', revision_id: 'revision', profile_id: 'web-fastapi-angular', status: 'running', limits: { max_calls: 10, max_output_tokens: 30000 }, created_at: '2026-10-30T12:00:00Z', updated_at: '2026-10-30T12:00:00Z', stages: [], questions: [], questions_revision: 0, metrics: { tokens_in: 0, tokens_out: 0, model_calls: 0, attempts: 0, manual_clarifications: 0, duration_ms: 0 } };

describe('Run polling', () => {
  const ended = new Subject<void>();
  let api: { run: ReturnType<typeof vi.fn>; events: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    vi.useFakeTimers();
    api = { run: vi.fn().mockReturnValue(of(run)), events: vi.fn().mockReturnValue(of({ items: [], next_after_seq: 0, has_more: false })) };
    TestBed.configureTestingModule({ providers: [RunMonitor, { provide: ApiClient, useValue: api }, { provide: Session, useValue: { ended } }] });
  });
  afterEach(() => { TestBed.resetTestingModule(); vi.useRealTimers(); });
  it('drains paginated events, deduplicates and recovers from a network loss', async () => {
    api.events.mockImplementation((_id: string, after: number) => of(after === 0 ? { items: [event(1)], next_after_seq: 1, has_more: true } : { items: [event(1),event(2)], next_after_seq: 2, has_more: false }));
    const snapshots: RunSnapshot[] = [];
    const sub = TestBed.inject(RunMonitor).watch('run').subscribe(value => snapshots.push(value));
    await vi.advanceTimersByTimeAsync(0);
    expect(snapshots.at(-1)?.events.map(e => e.seq)).toEqual([1,2]);
    api.run.mockReturnValueOnce(throwError(() => new ApiError(0, 'NETWORK_ERROR', 'Offline', '')));
    await vi.advanceTimersByTimeAsync(2000);
    expect(snapshots.at(-1)?.error?.code).toBe('NETWORK_ERROR');
    api.events.mockReturnValue(of({ items: [event(3)], next_after_seq: 3, has_more: false }));
    api.run.mockReturnValue(of({ ...run, status: 'completed' }));
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.events).toHaveBeenLastCalledWith('run', 2);
    expect(snapshots.at(-1)?.events.map(e => e.seq)).toEqual([1,2,3]);
    await vi.advanceTimersByTimeAsync(8000);
    expect(api.run).toHaveBeenCalledTimes(3);
    sub.unsubscribe();
  });
  it('cancels polling on logout and does not loop on a broken cursor', async () => {
    api.events.mockReturnValue(of({ items: [], next_after_seq: 0, has_more: true }));
    const values: RunSnapshot[] = [];
    const sub = TestBed.inject(RunMonitor).watch('run').subscribe(value => values.push(value));
    await vi.advanceTimersByTimeAsync(0);
    expect(values.at(-1)?.error).toBeTruthy(); expect(api.events).toHaveBeenCalledTimes(1);
    ended.next(); await vi.advanceTimersByTimeAsync(6000);
    expect(api.run).toHaveBeenCalledOnce(); sub.unsubscribe();
  });
  it.each(['run', 'events'] as const)('honors Retry-After on a %s response, including manual refresh', async endpoint => {
    api[endpoint].mockReturnValueOnce(throwError(() => new HttpErrorResponse({
      status: 429, headers: new HttpHeaders({ 'Retry-After': '8' }),
    })));
    const monitor = TestBed.inject(RunMonitor);
    const snapshots: RunSnapshot[] = [];
    const sub = monitor.watch('run').subscribe(value => snapshots.push(value));
    await vi.advanceTimersByTimeAsync(0);
    expect(snapshots.at(-1)?.error?.retryAfterMs).toBe(8000);
    await vi.advanceTimersByTimeAsync(2000); monitor.refresh.next();
    await vi.advanceTimersByTimeAsync(5999);
    expect(api.run).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.run).toHaveBeenCalledTimes(2); expect(snapshots.at(-1)?.error).toBeNull();
    sub.unsubscribe();
  });
});
