import { describe, expect, it, vi, afterEach } from 'vitest';
import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { normalizeError } from './errors';

afterEach(() => vi.useRealTimers());
describe('artifact errors and Retry-After', () => {
  it('preserves the error envelope returned as text for an artifact request', () => {
    const error = normalizeError(new HttpErrorResponse({ status: 422, error: JSON.stringify({
      error: { code: 'ARTIFACT_NOT_READY', message: 'Artifact not ready', request_id: 'artifact-trace', details: { artifact_id: 'id' } },
    }) }));
    expect(error.code).toBe('ARTIFACT_NOT_READY'); expect(error.message).toBe('Artifact not ready');
    expect(error.requestId).toBe('artifact-trace'); expect(error.details).toEqual({ artifact_id: 'id' });
  });
  it.each(['<html>Gateway failure</html>', 'null', '[]', '42'])('uses a safe fallback for %s', error => {
    const normalized = normalizeError(new HttpErrorResponse({ status: 502, error, headers: new HttpHeaders({ 'X-Request-ID': 'gateway' }) }));
    expect(normalized.code).toBe('HTTP_ERROR'); expect(normalized.requestId).toBe('gateway');
    expect(normalized.message).not.toContain('html');
  });
  it('parses seconds and HTTP dates without losing error details', () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-30T12:00:00Z'));
    const failure = (value: string) => normalizeError(new HttpErrorResponse({ status: 429, headers: new HttpHeaders({ 'Retry-After': value }) }));
    expect(failure('30').retryAfterMs).toBe(30000);
    expect(failure('Fri, 30 Oct 2026 12:00:08 GMT').retryAfterMs).toBe(8000);
    expect(failure('invalid').retryAfterMs).toBeNull();
  });
});
