import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { Subject } from 'rxjs';
import { DTO } from '../api/client';

type StoredSession = { token: string; expiresAt: number; login: string };
const STORAGE_KEY = 'rwb.session.v1';

@Injectable({ providedIn: 'root' })
export class Session {
  private readonly current = signal<StoredSession | null>(null);
  private expiryTimer?: ReturnType<typeof setTimeout>;
  readonly state = this.current.asReadonly();
  readonly ended = new Subject<void>();

  constructor() {
    inject(DestroyRef).onDestroy(() => { clearTimeout(this.expiryTimer); this.ended.complete(); });
    try {
      const raw: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null');
      if (raw && typeof raw === 'object' && 'token' in raw && typeof raw.token === 'string' && raw.token &&
          'expiresAt' in raw && typeof raw.expiresAt === 'number' && raw.expiresAt > Date.now() &&
          'login' in raw && typeof raw.login === 'string') {
        this.current.set({ token: raw.token, expiresAt: raw.expiresAt, login: raw.login });
        this.scheduleExpiry();
      } else this.clear();
    } catch { this.clear(); }
  }
  token() {
    const value = this.current();
    if (value && value.expiresAt <= Date.now()) { this.clear(); return null; }
    return value?.token ?? null;
  }
  save(token: DTO['Token'], login: string) {
    const session = { token: token.access_token, expiresAt: Date.now() + token.expires_in * 1000, login };
    this.current.set(session);
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session)); } catch { /* Session still works in memory. */ }
    this.scheduleExpiry();
  }
  clear() {
    clearTimeout(this.expiryTimer);
    this.current.set(null);
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Storage may be disabled. */ }
    this.ended.next();
  }
  private scheduleExpiry() {
    clearTimeout(this.expiryTimer);
    const delay = Math.max(0, (this.current()?.expiresAt ?? 0) - Date.now());
    this.expiryTimer = setTimeout(() => {
      if ((this.current()?.expiresAt ?? 0) <= Date.now()) this.clear();
      else this.scheduleExpiry();
    }, Math.min(delay, 2_147_483_647));
  }
}
