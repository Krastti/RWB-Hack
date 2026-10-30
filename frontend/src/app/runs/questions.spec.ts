import { afterEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { ApiClient, DTO, Run } from '../api/client';
import { Questions } from './questions';
import { makeRun } from '../testing/fixtures';

afterEach(() => TestBed.resetTestingModule());
describe('clarification request scope', () => {
  it.each(['run', 'revision'])('cancels pending answers when the %s changes', scope => {
    const requests: Subject<Run>[] = [];
    const api = { answers: vi.fn((_id: string, _body: DTO['Answers'], _key: string) => { const request = new Subject<Run>(); requests.push(request); return request; }) };
    TestBed.configureTestingModule({ imports: [Questions], providers: [{ provide: ApiClient, useValue: api }] });
    const fixture = TestBed.createComponent(Questions);
    const run = makeRun({ status: 'waiting_for_input', questions_revision: 1, questions: [{ question_id: 'question', question: 'Question?', reason: 'Reason', related_fr_ids: [], required: true }] });
    fixture.componentRef.setInput('run', run); fixture.detectChanges();
    const updated = vi.fn(); fixture.componentInstance.updated.subscribe(updated);
    fixture.componentInstance.form.controls['question'].setValue('Old answer'); fixture.componentInstance.submit();
    const oldKey = api.answers.mock.calls[0][2]; expect(requests[0].observed).toBe(true);
    const next = { ...run, run_id: scope === 'run' ? 'new-run' : run.run_id, questions_revision: 2 };
    fixture.componentRef.setInput('run', next); fixture.detectChanges();
    expect(requests[0].observed).toBe(false); expect(fixture.componentInstance.pending()).toBe(false);
    requests[0].next(run); expect(updated).not.toHaveBeenCalled();
    fixture.componentInstance.form.controls['question'].setValue('New answer'); fixture.componentInstance.submit();
    expect(api.answers.mock.calls[1][0]).toBe(next.run_id); expect(api.answers.mock.calls[1][2]).not.toBe(oldKey);
  });
});
