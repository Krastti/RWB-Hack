import { NO_ERRORS_SCHEMA } from '@angular/core';
import { provideRouter } from '@angular/router';
import { RouterLink } from '@angular/router';
import { ReactiveFormsModule } from '@angular/forms';
import { ErrorBanner } from '../shared/error-banner';
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

it('does not label GET metrics complete after complete events, new snapshots', () => {
  const updates = new Subject<RunSnapshot>();
  TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: {} }] });
  TestBed.overrideComponent(RunDetail, { set: { imports: [], template: '', providers: [{ provide: RunMonitor, useValue: { watch: vi.fn(() => updates) } }] } });
  const fixture = TestBed.createComponent(RunDetail);
  fixture.componentRef.setInput('id','run'); fixture.detectChanges();
  const events = [{seq:1,run_id:'run',timestamp:'2026-10-30T12:00:00Z',event_type:'metrics.updated',data:{schema_version:'1.0',tokens_complete:{tokens_in:true,tokens_out:true}}}];
  for (const total of [0, 12, 50]) {
    const run = makeRun(); run.metrics.tokens_in = total;
    updates.next({run,events,error:null});
    expect(fixture.componentInstance.metrics(fixture.componentInstance.run()!)[0]).toMatchObject({value:`${total} / ${run.metrics.tokens_out}`,note:'Известный расход · полнота неизвестна'});
  }
});

it.each(['queued','running','waiting_for_input','failed','budget_exceeded'] as const)('blocks historical %s commands even on direct handler calls', status => {
  const updates = new Subject<RunSnapshot>();
  const api = {cancel:vi.fn(),retry:vi.fn()};
  TestBed.configureTestingModule({providers:[{provide:ApiClient,useValue:api}]});
  TestBed.overrideComponent(RunDetail, {set:{imports:[],template:'',providers:[{provide:RunMonitor,useValue:{watch:()=>updates}}]}});
  const fixture = TestBed.createComponent(RunDetail);
  fixture.componentRef.setInput('id','run');fixture.detectChanges();
  const run = makeRun({profile_id:'web-fastapi-react',status});
  updates.next({run,events:[],error:null});
  expect(fixture.componentInstance.supportsRunCommands(run)).toBe(false);
  fixture.componentInstance.cancel();fixture.componentInstance.retry();
  expect(api.cancel).not.toHaveBeenCalled();expect(api.retry).not.toHaveBeenCalled();
  expect(fixture.componentInstance.supportsRunCommands({...run,profile_id:'web-fastapi-angular'})).toBe(true);
});

it.each(['running','waiting_for_input','failed','budget_exceeded'] as const)('renders historical %s runs as read-only', status => {
  const updates = new Subject<RunSnapshot>();
  TestBed.configureTestingModule({providers:[provideRouter([]),{provide:ApiClient,useValue:{}}]});
  TestBed.overrideComponent(RunDetail, {
    set:{imports:[RouterLink,ReactiveFormsModule,ErrorBanner],schemas:[NO_ERRORS_SCHEMA],providers:[{provide:RunMonitor,useValue:{watch:()=>updates}}]},
  });
  const fixture = TestBed.createComponent(RunDetail);
  fixture.componentRef.setInput('id','run');fixture.detectChanges();
  updates.next({run:makeRun({profile_id:'web-fastapi-react',status}),events:[],error:null});
  fixture.detectChanges();
  expect(fixture.nativeElement.textContent).toContain('запуск доступен только для просмотра');
  expect(fixture.nativeElement.querySelector('button')).toBeNull();
  expect(fixture.nativeElement.querySelector('rwb-questions')).toBeNull();
});
