import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router } from '@angular/router';
import { firstValueFrom, of } from 'rxjs';
import { ApiClient, API_TRANSPORT, PROFILE_ID } from './client';
import { apiInterceptor } from './interceptor';
import { ApiError } from './errors';
import { Session } from '../auth/session';

describe('REST client and session boundary', () => {
  let http: HttpTestingController;
  let api: ApiClient;
  let session: Session;
  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({ providers: [provideHttpClient(withInterceptors([apiInterceptor])), provideHttpClientTesting(), provideRouter([])] });
    http = TestBed.inject(HttpTestingController); api = TestBed.inject(ApiClient); session = TestBed.inject(Session);
    vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
  });
  afterEach(() => { http.verify(); TestBed.resetTestingModule(); vi.useRealTimers(); });
  it('attaches token only to platform API and leaves login public', () => {
    session.save({ access_token: 'secret', token_type: 'bearer', expires_in: 600 }, 'alice');
    api.projects().subscribe(); expect(http.expectOne('/api/v1/projects').request.headers.get('Authorization')).toBe('Bearer secret');
    TestBed.inject(HttpClient).get('https://other.example/api/v1/projects').subscribe();
    expect(http.expectOne('https://other.example/api/v1/projects').request.headers.has('Authorization')).toBe(false);
    TestBed.inject(HttpClient).get('/api/v10/projects').subscribe();
    expect(http.expectOne('/api/v10/projects').request.headers.has('Authorization')).toBe(false);
    api.login({ login: 'alice', password: 'pw' }).subscribe();
    expect(http.expectOne('/api/v1/auth/token').request.headers.has('Authorization')).toBe(false);
  });
  it.each([401, 422, 429, 0])('normalizes %s with request_id and clears session only on protected 401', async status => {
    session.save({ access_token: 'secret', token_type: 'bearer', expires_in: 600 }, 'alice');
    const pending = firstValueFrom(api.projects()).catch(error => error);
    const request = http.expectOne('/api/v1/projects');
    if (status) request.flush({ error: { code: 'CODE', message: 'Message', request_id: 'trace-1', details: { field: 'name' } } }, { status, statusText: 'Failure' });
    else request.error(new ProgressEvent('error'));
    const error = await pending;
    expect(error).toBeInstanceOf(ApiError); expect(error.status).toBe(status);
    expect(error.requestId).toBe(status ? 'trace-1' : '');
    expect(session.token()).toBe(status === 401 ? null : 'secret');
  });
  it('uses the new profile and preserves a caller-provided idempotency key', () => {
    api.createRun('project', 'requirements', 'same-key').subscribe();
    const req = http.expectOne('/api/v1/projects/project/runs');
    expect(req.request.body.profile_id).toBe(PROFILE_ID);
    expect(req.request.headers.get('Idempotency-Key')).toBe('same-key');
  });
  it('expires a session, clears storage and signals cancellation', () => {
    vi.useFakeTimers();
    const ended = vi.fn(); session.ended.subscribe(ended);
    session.save({ access_token: 'secret', token_type: 'bearer', expires_in: 1 }, 'alice');
    vi.advanceTimersByTime(1001);
    expect(session.token()).toBeNull(); expect(sessionStorage.length).toBe(0); expect(ended).toHaveBeenCalledOnce();
  });
  it('allows a replacement transport for contract examples', () => {
    TestBed.resetTestingModule();
    const get = vi.fn().mockReturnValue(of({ items: [], next_cursor: null }));
    TestBed.configureTestingModule({ providers: [{ provide: API_TRANSPORT, useValue: { get } }] });
    TestBed.inject(ApiClient).projects().subscribe(page => expect(page.items).toEqual([]));
    expect(get).toHaveBeenCalledOnce();
  });
});
