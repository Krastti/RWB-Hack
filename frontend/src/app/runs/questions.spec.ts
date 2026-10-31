import { afterEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { ApiClient, DTO, Run } from '../api/client';
import { Questions } from './questions';
import { makeRun } from '../testing/fixtures';

afterEach(() => TestBed.resetTestingModule());
it('keeps confirmed answers disabled and excludes them from the next payload',()=>{
  const response=new Subject<Run>(); const api={answers:vi.fn((_id:string,_body:DTO['Answers'],_key:string)=>response)};
  TestBed.configureTestingModule({imports:[Questions],providers:[{provide:ApiClient,useValue:api}]});
  const fixture=TestBed.createComponent(Questions);
  fixture.componentRef.setInput('run',makeRun({status:'waiting_for_input',questions_revision:2,questions:[
    {question_id:'old',question:'Old?',reason:'Reason',related_fr_ids:[],required:true,answer:'Confirmed'},
    {question_id:'new',question:'New?',reason:'Reason',related_fr_ids:[],required:true}]}));
  fixture.detectChanges();
  expect(fixture.componentInstance.form.controls['old'].disabled).toBe(true);
  fixture.componentInstance.form.controls['old'].setValue('Tampered');
  fixture.componentInstance.form.controls['new'].setValue('New answer');
  fixture.componentInstance.submit();
  expect(api.answers.mock.calls[0][1]).toEqual({questions_revision:2,answers:[{question_id:'new',value:'New answer'}]});
});
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

it.each([true, false])('explains whitespace and maxlength errors (required=%s)', async required => {
  const api = {answers: vi.fn()};
  TestBed.configureTestingModule({imports:[Questions],providers:[{provide:ApiClient,useValue:api}]});
  const fixture = TestBed.createComponent(Questions);
  fixture.componentRef.setInput('run', makeRun({status:'waiting_for_input', questions_revision:1,
    questions:[{question_id:'question',question:'Question?',reason:'Reason',related_fr_ids:[],required}]}));
  fixture.detectChanges();
  const textarea = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
  const enter = async (value: string) => { textarea.value = value; textarea.dispatchEvent(new Event('input')); textarea.dispatchEvent(new Event('blur')); await fixture.whenStable(); fixture.detectChanges(); };
  await enter(' \t\u00a0');
  expect(fixture.nativeElement.textContent).toContain('Ответ должен содержать хотя бы один непробельный символ.');
  expect(fixture.nativeElement.querySelector('button').disabled).toBe(true);
  expect(fixture.nativeElement.querySelector('textarea').getAttribute('aria-describedby')).toBe('question-error');
  fixture.componentInstance.submit(); expect(api.answers).not.toHaveBeenCalled();
  await enter('a'.repeat(10001));
  expect(fixture.nativeElement.textContent).toContain('Ответ должен содержать не более 10 000 символов.');
  await enter(required ? 'Valid answer' : '');
  expect(fixture.nativeElement.querySelector('[role="alert"]')).toBeNull();
  expect(fixture.nativeElement.querySelector('button').disabled).toBe(false);
});

it('does not submit answers for a historical profile even if called directly', () => {
  const api = {answers:vi.fn()};
  TestBed.configureTestingModule({imports:[Questions],providers:[{provide:ApiClient,useValue:api}]});
  const fixture = TestBed.createComponent(Questions);
  fixture.componentRef.setInput('run', makeRun({profile_id:'web-fastapi-react',status:'waiting_for_input'}));
  fixture.detectChanges(); fixture.componentInstance.submit();
  expect(fixture.nativeElement.querySelector('form')).toBeNull();
  expect(api.answers).not.toHaveBeenCalled();
});
