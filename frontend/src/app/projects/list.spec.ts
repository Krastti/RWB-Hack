import { afterEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { provideRouter } from '@angular/router';
import { ApiClient } from '../api/client';
import { ProjectList } from './list';
import { makeProject } from '../testing/fixtures';

afterEach(() => TestBed.resetTestingModule());
describe('project list request count', () => {
  it('distinguishes a missing summary from an explicit no-run status', () => {
    const { latest_run_status, ...legacy } = makeProject('legacy');
    const empty = { ...makeProject('empty'), latest_run_status: null };
    TestBed.configureTestingModule({ imports: [ProjectList], providers: [
      provideRouter([]), { provide: ApiClient, useValue: { projects: () => of({ items: [legacy,empty], next_cursor: null }) } },
    ] });
    const fixture = TestBed.createComponent(ProjectList); fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Статус недоступен');
    expect(fixture.nativeElement.textContent).toContain('Нет запусков');
  });
  it('loads one page and its statuses with one request, including pagination', () => {
    const api = { projects: vi.fn().mockReturnValueOnce(of({ items: Array.from({ length: 50 }, (_,i) => makeProject('project-'+i)), next_cursor: 'next' })).mockReturnValueOnce(of({ items: [makeProject('last')], next_cursor: null })), runs: vi.fn() };
    TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: api }] });
    TestBed.overrideComponent(ProjectList, { set: { imports: [], template: '' } });
    const fixture = TestBed.createComponent(ProjectList); fixture.detectChanges();
    expect(api.projects).toHaveBeenCalledTimes(1); expect(api.runs).not.toHaveBeenCalled();
    expect(fixture.componentInstance.items()[0].latest_run_status).toBe('completed');
    fixture.componentInstance.load(true);
    expect(api.projects).toHaveBeenLastCalledWith('next'); expect(api.runs).not.toHaveBeenCalled();
    expect(fixture.componentInstance.items()).toHaveLength(51);
  });
});
