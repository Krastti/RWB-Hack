import { HttpErrorResponse } from '@angular/common/http';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string,
    readonly details: Record<string, unknown> = {},
  ) { super(message); }
}

export function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (!(error instanceof HttpErrorResponse)) return new ApiError(0, 'UNKNOWN_ERROR', 'Не удалось выполнить запрос.', '');
  const envelope: unknown = error.error;
  const detail = envelope && typeof envelope === 'object' && 'error' in envelope ? envelope.error : undefined;
  const obj = detail && typeof detail === 'object' ? detail as Record<string, unknown> : {};
  const fallback: Record<number, string> = { 0: 'Нет соединения с сервером.', 401: 'Войдите в систему.', 404: 'Данные недоступны.', 409: 'Данные изменились. Обновите страницу.', 422: 'Проверьте заполнение полей.', 429: 'Превышен лимит запросов. Повторите позже.' };
  return new ApiError(error.status, typeof obj['code'] === 'string' ? obj['code'] : error.status === 0 ? 'NETWORK_ERROR' : 'HTTP_ERROR',
    typeof obj['message'] === 'string' ? obj['message'] : fallback[error.status] ?? 'Ошибка сервера.',
    typeof obj['request_id'] === 'string' ? obj['request_id'] : error.headers.get('X-Request-ID') ?? '',
    obj['details'] && typeof obj['details'] === 'object' && !Array.isArray(obj['details']) ? obj['details'] as Record<string, unknown> : {});
}
