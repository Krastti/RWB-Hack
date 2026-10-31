import { Injectable, InjectionToken, inject } from '@angular/core';
import { HttpClient, HttpContext, HttpContextToken, HttpHeaders, HttpParams } from '@angular/common/http';
import { components } from './schema';

export type DTO = components['schemas'];
export type Project = DTO['Project'];
export type Run = DTO['Run'];
export type RunEvent = DTO['Event'];
export const PROFILE_ID = 'web-fastapi-angular' as const;
export const API_BASE = new InjectionToken<string>('API_BASE', { factory: () => '/api/v1' });
export const API_TRANSPORT = new InjectionToken<HttpClient>('API_TRANSPORT', { factory: () => inject(HttpClient) });
export const PUBLIC_REQUEST = new HttpContextToken(() => false);
export const TERMINAL = new Set<Run['status']>(['completed', 'failed', 'cancelled', 'budget_exceeded']);

@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(API_TRANSPORT);
  private readonly base = inject(API_BASE).replace(/\/$/, '');
  private path(value: string) { return `${this.base}${value}`; }
  private id(value: string) { return encodeURIComponent(value); }
  private page(cursor?: string) { return cursor ? new HttpParams().set('cursor', cursor) : new HttpParams(); }
  private keyed(key: string) { return { headers: new HttpHeaders({ 'Idempotency-Key': key }) }; }

  login(body: DTO['TokenRequest']) {
    return this.http.post<DTO['Token']>(this.path('/auth/token'), body, { context: new HttpContext().set(PUBLIC_REQUEST, true) });
  }
  projects(cursor?: string) { return this.http.get<DTO['ProjectPage']>(this.path('/projects'), { params: this.page(cursor) }); }
  project(id: string) { return this.http.get<Project>(this.path(`/projects/${this.id(id)}`)); }
  createProject(body: DTO['ProjectCreate']) { return this.http.post<Project>(this.path('/projects'), body); }
  runs(id: string, cursor?: string) { return this.http.get<DTO['RunPage']>(this.path(`/projects/${this.id(id)}/runs`), { params: this.page(cursor) }); }
  createRun(id: string, requirements_text: string, key: string) {
    const body: DTO['RunCreate'] = { requirements_text, profile_id: PROFILE_ID };
    return this.http.post<Run>(this.path(`/projects/${this.id(id)}/runs`), body, this.keyed(key));
  }
  run(id: string) { return this.http.get<Run>(this.path(`/runs/${this.id(id)}`)); }
  events(id: string, after: number) {
    return this.http.get<DTO['EventPage']>(this.path(`/runs/${this.id(id)}/events`), { params: { after_seq: after } });
  }
  cancel(id: string) { return this.http.post<Run>(this.path(`/runs/${this.id(id)}/cancel`), {}); }
  retry(id: string, body: DTO['Retry'], key: string) { return this.http.post<Run>(this.path(`/runs/${this.id(id)}/retry`), body, this.keyed(key)); }
  answers(id: string, body: DTO['Answers'], key: string) { return this.http.post<Run>(this.path(`/runs/${this.id(id)}/answers`), body, this.keyed(key)); }
  acceptedArtifacts(id: string) { return this.http.get<DTO['AcceptedArtifacts']>(this.path(`/runs/${this.id(id)}/accepted-artifacts`)); }
  artifacts(id: string, cursor?: string) { return this.http.get<DTO['ManifestPage']>(this.path(`/runs/${this.id(id)}/artifacts`), { params: this.page(cursor) }); }
  artifact(id: string, artifact: string) { return this.http.get(this.path(`/runs/${this.id(id)}/artifacts/${this.id(artifact)}`), { responseType: 'text' }); }
}
