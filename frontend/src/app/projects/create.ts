import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { finalize } from 'rxjs';
import { ApiClient } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-project-create', imports: [ReactiveFormsModule, RouterLink, ErrorBanner],
  template: `<a routerLink="/projects" class="small">← Все проекты</a><h1 class="h2 mt-3">Новый проект</h1><p class="text-secondary">Опишите задачу, которую должно решать приложение.</p>
    <div class="card"><div class="card-body p-3 p-md-4">
      <rwb-error [error]="error()" />
      <form [formGroup]="form" (ngSubmit)="submit()">
        <div class="mb-3"><label for="name" class="form-label">Название</label><input id="name" formControlName="name" class="form-control" maxlength="120" required aria-describedby="name-help"><div id="name-help" class="form-text">От 1 до 120 символов.</div>
          @if (form.controls.name.touched && form.controls.name.invalid) { <div class="text-danger small" role="alert">Введите название до 120 символов.</div> }
        </div>
        <div class="mb-4"><label for="requirements" class="form-label">Требования</label><textarea id="requirements" formControlName="requirements_text" class="form-control" rows="12" maxlength="100000" required aria-describedby="requirements-help"></textarea><div id="requirements-help" class="form-text">От 1 до 100 000 символов. Укажите пользователей, сценарии и ограничения.</div>
          @if (form.controls.requirements_text.touched && form.controls.requirements_text.invalid) { <div class="text-danger small" role="alert">Заполните требования в пределах 100 000 символов.</div> }
        </div>
        <button class="btn btn-primary" type="submit" [disabled]="form.invalid || pending()">{{ pending() ? 'Сохранение…' : 'Создать проект' }}</button>
      </form>
    </div></div>`,
})
export class ProjectCreate {
  private readonly api = inject(ApiClient);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly pending = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({ name: ['', [Validators.required, Validators.maxLength(120)]], requirements_text: ['', [Validators.required, Validators.maxLength(100000)]] });
  submit() {
    if (this.form.invalid || this.pending()) return;
    this.error.set(null); this.pending.set(true);
    this.api.createProject(this.form.getRawValue()).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.pending.set(false))).subscribe({
      next: project => { void this.router.navigate(['/projects', project.id]); },
      error: error => this.error.set(normalizeError(error)),
    });
  }
}
