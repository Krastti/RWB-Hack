import { afterEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { ApiClient } from '../api/client';
import { makeRun } from '../testing/fixtures';
import { RunDetail } from './detail';
import { RunMonitor, RunSnapshot } from './monitor';

afterEach(() => TestBed.resetTestingModule());
describe('budget form lifecycle', () => {
  it('keeps edits during polling but resets to newly accepted limits for the next exhaustion', () => {
    const updates = new Subject<RunSnapshot>();
    TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: {} }] });
    TestBed.overrideComponent(RunDetail, { set: { imports: [], template: '', providers: [{ provide: RunMonitor, useValue: { watch: vi.fn(() => updates) } }] } });
    const fixture = TestBed.createComponent(RunDetail); fixture.componentRef.setInput('id', 'run'); fixture.detectChanges();
    const first = makeRun({ status: 'budget_exceeded' });
    updates.next({ run: first, events: [], error: null });
    fixture.componentInstance.retryForm.controls.max_calls.setValue(15);
    updates.next({ run: first, events: [], error: null });
    expect(fixture.componentInstance.retryForm.controls.max_calls.value).toBe(15);
    const next = makeRun({ status: 'queued', limits: { max_calls: 15, max_output_tokens: 40000 } });
    updates.next({ run: next, events: [], error: null });
    updates.next({ run: { ...next, status: 'budget_exceeded' }, events: [], error: null });
    expect(fixture.componentInstance.retryForm.getRawValue()).toEqual(next.limits);
  });
});
