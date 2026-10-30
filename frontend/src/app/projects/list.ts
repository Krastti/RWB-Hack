import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { catchError, finalize, of } from 'rxjs';
import { ApiClient, Project } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-project-list', imports: [DatePipe, RouterLink, ErrorBanner],
  template: `<div class="d-flex gap-3 justify-content-between align-items-start mb-4"><div><h1 class="h2">Мои проекты</h1><p class="text-secondary mb-0">Требования, запуски и подготовленные исходники.</p></div><a routerLink="/projects/new" class="btn btn-primary text-nowrap">Новый проект</a></div>
    <rwb-error [error]="error()" />
    @if (error()) { <button class="btn btn-outline-secondary mb-3" (click)="load()" [disabled]="loading()">Повторить</button> }
    @if (loading() && !items().length) { <p role="status">Загрузка проектов…</p> }
    @else if (!items().length && !error()) { <div class="card"><div class="card-body text-center py-5"><h2 class="h5">Пока нет проектов</h2><p class="text-secondary">Начните с описания вашей задачи.</p><a routerLink="/projects/new" class="btn btn-primary">Создать первый проект</a></div></div> }
    @if (items().length) { <div class="card"><div class="card-header">Ваши проекты</div><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th scope="col">Название</th><th scope="col">Создан</th><th scope="col">Последний запуск</th></tr></thead><tbody>@for (project of items(); track project.id) { <tr><td><a [routerLink]="['/projects', project.id]">{{ project.name }}</a></td><td>{{ project.created_at | date:'medium' }}</td><td>{{ statuses()[project.id] ?? 'Загрузка…' }}</td></tr> }</tbody></table></div></div> }
    @if (cursor()) { <button class="btn btn-outline-primary mt-3" (click)="load(true)" [disabled]="loading()">{{ loading() ? 'Загрузка…' : 'Загрузить ещё' }}</button> }`,
})
export class ProjectList {
  private readonly api = inject(ApiClient);
  private readonly destroyRef = inject(DestroyRef);
  readonly statuses = signal<Record<string,string>>({});
  readonly items = signal<Project[]>([]);
  readonly cursor = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  constructor() { this.load(); }
  load(append = false) {
    if (this.loading()) return;
    this.loading.set(true); this.error.set(null);
    this.api.projects(append ? this.cursor() ?? undefined : undefined).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.loading.set(false))).subscribe({
      next: page => {
        this.items.update(items => append ? [...items, ...page.items] : page.items); this.cursor.set(page.next_cursor);
        for (const project of page.items) this.api.runs(project.id).pipe(takeUntilDestroyed(this.destroyRef), catchError(() => of(null))).subscribe(runs => {
          this.statuses.update(value => ({ ...value, [project.id]: runs ? runs.items[0]?.status ?? 'Нет запусков' : 'Статус недоступен' }));
        });
      },
      error: error => this.error.set(normalizeError(error)),
    });
  }
}
