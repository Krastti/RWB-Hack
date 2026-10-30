import { HttpErrorResponse } from '@angular/common/http';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string,
    readonly details: Record<string, unknown> = {},
    readonly retryAfterMs: number | null = null,
  ) { super(message); }
}

export function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (!(error instanceof HttpErrorResponse)) return new ApiError(0, 'UNKNOWN_ERROR', 'Не удалось выполнить запрос.', '');
  let envelope: unknown = error.error;
  if (typeof envelope === 'string') {
    try { envelope = JSON.parse(envelope); } catch { envelope = null; }
  }
  const detail = envelope && typeof envelope === 'object' && 'error' in envelope ? envelope.error : undefined;
  const obj = detail && typeof detail === 'object' ? detail as Record<string, unknown> : {};
  const fallback: Record<number, string> = { 0: 'Нет соединения с сервером.', 401: 'Войдите в систему.', 404: 'Данные недоступны.', 409: 'Данные изменились. Обновите страницу.', 422: 'Проверьте заполнение полей.', 429: 'Превышен лимит запросов. Повторите позже.' };
  return new ApiError(error.status, typeof obj['code'] === 'string' ? obj['code'] : error.status === 0 ? 'NETWORK_ERROR' : 'HTTP_ERROR',
    typeof obj['message'] === 'string' ? obj['message'] : fallback[error.status] ?? 'Ошибка сервера.',
    typeof obj['request_id'] === 'string' ? obj['request_id'] : error.headers.get('X-Request-ID') ?? '',
    obj['details'] && typeof obj['details'] === 'object' && !Array.isArray(obj['details']) ? obj['details'] as Record<string, unknown> : {},
    retryAfter(error.headers.get('Retry-After')));
}

function retryAfter(header: string | null): number | null {
  if (header === null || !header.trim()) return null;
  const value = header.trim();
  if (/^\d+$/.test(value)) {
    const milliseconds = Number(value) * 1000;
    return Number.isFinite(milliseconds) ? milliseconds : null;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - Date.now()) : null;
}
