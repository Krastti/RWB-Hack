import { afterEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { ApiClient, DTO } from '../api/client';
import { Analysis } from './analysis';

afterEach(() => TestBed.resetTestingModule());
const empty: DTO['ManifestPage'] = { snapshot_id: 'snapshot', items: [], next_cursor: null };
describe('analysis refresh', () => {
  function setup(api: object) {
    TestBed.configureTestingModule({ imports: [Analysis], providers: [{ provide: ApiClient, useValue: api }] });
    const fixture = TestBed.createComponent(Analysis);
    fixture.componentRef.setInput('runId', 'run-a'); fixture.componentRef.setInput('refreshKey', 'running');
    return fixture;
  }
  it('reloads results when the same run completes', () => {
    const api = { artifacts: vi.fn().mockReturnValue(of(empty)), artifact: vi.fn().mockReturnValue(of('# Finished analysis')) };
    const fixture = setup(api); fixture.detectChanges();
    expect(fixture.componentInstance.markdown()).toBe('');
    api.artifacts.mockReturnValue(of({ ...empty, items: [{ artifact_id: 'md', path: 'docs/system-analysis.md' }] }));
    fixture.componentRef.setInput('refreshKey', 'completed'); fixture.detectChanges();
    expect(api.artifacts).toHaveBeenCalledTimes(2);
    expect(api.artifacts).toHaveBeenLastCalledWith('run-a');
    expect(fixture.componentInstance.markdown()).toContain('Finished analysis');
  });
  it('cancels an in-flight request and loads the new run immediately', () => {
    const first = new Subject<DTO['ManifestPage']>(); const second = new Subject<DTO['ManifestPage']>();
    const api = { artifacts: vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second), artifact: vi.fn().mockReturnValue(of('# New run')) };
    const fixture = setup(api); fixture.detectChanges(); expect(first.observed).toBe(true);
    fixture.componentRef.setInput('runId', 'run-b'); fixture.detectChanges();
    expect(first.observed).toBe(false); expect(api.artifacts).toHaveBeenLastCalledWith('run-b');
    second.next({ ...empty, items: [{ artifact_id: 'md', path: 'docs/system-analysis.md' }] } as DTO['ManifestPage']); second.complete();
    first.next(empty); first.complete();
    expect(fixture.componentInstance.markdown()).toContain('New run'); expect(fixture.componentInstance.loading()).toBe(false);
  });
});
