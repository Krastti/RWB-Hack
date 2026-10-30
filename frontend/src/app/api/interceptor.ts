import { inject } from '@angular/core';
import { HttpInterceptorFn } from '@angular/common/http';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { Session } from '../auth/session';
import { API_BASE, PUBLIC_REQUEST } from './client';
import { normalizeError } from './errors';

export const apiInterceptor: HttpInterceptorFn = (request, next) => {
  const base = new URL(inject(API_BASE).replace(/\/$/, '') + '/', location.origin);
  const target = new URL(request.url, location.origin);
  if (target.origin !== base.origin || !target.pathname.startsWith(base.pathname)) return next(request);
  const session = inject(Session);
  const router = inject(Router);
  const token = request.context.get(PUBLIC_REQUEST) ? null : session.token();
  const authorized = token ? request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : request;
  return next(authorized).pipe(catchError(error => {
    const normalized = normalizeError(error);
    if (normalized.status === 401 && !request.context.get(PUBLIC_REQUEST)) {
      session.clear();
      void router.navigate(['/login']);
    }
    return throwError(() => normalized);
  }));
};
