import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { finalize } from 'rxjs';
import { ApiClient } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';
import { Session } from './session';

@Component({
  selector: 'rwb-login', imports: [ReactiveFormsModule, ErrorBanner],
  template: `<main class="login-page d-flex align-items-center justify-content-center p-3">
    <div class="login-panel w-100">
      <p class="text-white-50 text-center mb-4">RWB / STUDIO</p>
      <div class="card shadow-lg border-0">
        <div class="card-header bg-white py-4"><h1 class="h3 text-center mb-0">Вход в рабочее пространство</h1></div>
        <div class="card-body p-4">
          <rwb-error [error]="error()" />
          <form [formGroup]="form" (ngSubmit)="submit()">
            <div class="mb-3"><label for="login" class="form-label">Логин</label><input id="login" class="form-control" formControlName="login" autocomplete="username" maxlength="120" required></div>
            <div class="mb-4"><label for="password" class="form-label">Пароль</label><input id="password" class="form-control" formControlName="password" type="password" autocomplete="current-password" maxlength="1024" required></div>
            <button class="btn btn-primary w-100" type="submit" [disabled]="pending() || form.invalid">{{ pending() ? 'Вход…' : 'Войти' }}</button>
          </form>
        </div>
      </div>
    </div>
  </main>`,
})
export class Login {
  private readonly api = inject(ApiClient);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly pending = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({ login: ['', [Validators.required, Validators.maxLength(120)]], password: ['', [Validators.required, Validators.maxLength(1024)]] });
  constructor() { if (this.session.token()) void this.router.navigate(['/projects']); }
  submit() {
    if (this.form.invalid || this.pending()) return;
    this.pending.set(true); this.error.set(null);
    const credentials = this.form.getRawValue();
    this.api.login(credentials).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.pending.set(false))).subscribe({
      next: token => { this.session.save(token, credentials.login); this.form.controls.password.reset(); void this.router.navigate(['/projects']); },
      error: error => this.error.set(normalizeError(error)),
    });
  }
}
