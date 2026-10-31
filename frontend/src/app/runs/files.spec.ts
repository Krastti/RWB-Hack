import { afterEach, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { ApiClient, DTO } from '../api/client';
import { ApiError } from '../api/errors';
import { Files } from './files';

afterEach(()=>TestBed.resetTestingModule());
const artifact = {artifact_id:'one',path:'src/a.ts',size_bytes:15,version:1,media_type:'text/plain'} as DTO['Artifact'];
function setup(api: object) {
  TestBed.configureTestingModule({imports:[Files],providers:[{provide:ApiClient,useValue:api}]});
  const fixture=TestBed.createComponent(Files);
  fixture.componentRef.setInput('runId','run-a'); fixture.componentRef.setInput('refreshKey','running');
  return fixture;
}
it('shows accepted partial files without allowing ZIP',()=>{
  const api={artifacts:()=>throwError(()=>new ApiError(409,'MANIFEST_NOT_READY','Pending','')),
    acceptedArtifacts:()=>of({items:[artifact]}),archive:vi.fn()};
  const fixture=setup(api);fixture.detectChanges();
  expect(fixture.componentInstance.files()).toEqual([artifact]);
  expect(fixture.componentInstance.error()).toBeNull();
  fixture.componentInstance.download();expect(api.archive).not.toHaveBeenCalled();
});
it('pins pages to a snapshot and escapes file content',()=>{
  const api={artifacts:vi.fn().mockReturnValueOnce(of({snapshot_id:'snap',items:[artifact],next_cursor:'snap:1'}))
    .mockReturnValueOnce(of({snapshot_id:'snap',items:[{...artifact,artifact_id:'two',path:'src/b.ts'}],next_cursor:null})),
    artifact:()=>of('<img src=x onerror=alert(1)>')};
  const fixture=setup(api);fixture.detectChanges();
  expect(api.artifacts).toHaveBeenLastCalledWith('run-a','snap:1');
  fixture.componentInstance.select(artifact);fixture.detectChanges();
  expect(fixture.nativeElement.querySelector('img')).toBeNull();
  expect(fixture.nativeElement.querySelector('pre').textContent).toContain('<img');
  expect(fixture.componentInstance.snapshot()).toBe('snap');
});
it('cancels stale reads on run change and clears the selected file',()=>{
  const old=new Subject<string>();
  const api={artifacts:()=>of({snapshot_id:'snap',items:[artifact],next_cursor:null}),artifact:()=>old};
  const fixture=setup(api);fixture.detectChanges();fixture.componentInstance.select(artifact);
  expect(old.observed).toBe(true);
  fixture.componentRef.setInput('runId','run-b');fixture.detectChanges();
  expect(old.observed).toBe(false);expect(fixture.componentInstance.selected()).toBeNull();
  old.next('stale');expect(fixture.componentInstance.content()).toBe('');
});
it('does not render binary or oversized files as text',()=>{
  const api={artifacts:()=>of({snapshot_id:'snap',items:[],next_cursor:null}),artifact:vi.fn()};
  const fixture=setup(api);fixture.detectChanges();
  fixture.componentInstance.select({...artifact,media_type:'application/octet-stream'});
  expect(api.artifact).not.toHaveBeenCalled();
  expect(fixture.componentInstance.previewNote()).toContain('двоичного');
});
it('downloads only the selected snapshot and reports a server readiness conflict',()=>{
  const api={artifacts:()=>of({snapshot_id:'snap',items:[artifact],next_cursor:null}),
    archive:vi.fn(()=>throwError(()=>new ApiError(409,'MANIFEST_NOT_READY','Not ready','')))};
  const fixture=setup(api);fixture.componentRef.setInput('completed',true);fixture.detectChanges();
  fixture.componentInstance.download();
  expect(api.archive).toHaveBeenCalledWith('run-a','snap');
  expect(fixture.componentInstance.error()?.code).toBe('MANIFEST_NOT_READY');
  expect(fixture.componentInstance.downloading()).toBe(false);
});
