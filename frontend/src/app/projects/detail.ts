import { Component, effect, inject, input, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { Subscription, finalize } from 'rxjs';
import { ApiClient, Project, Run } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-project-detail', imports: [RouterLink, DatePipe, ErrorBanner],
  template: `<a routerLink="/projects" class="small">← Все проекты</a><rwb-error [error]="error()" />
    @if (loading()) { <p class="mt-3" role="status">Загрузка проекта…</p> }
    @if (project(); as p) { <div class="d-flex gap-3 justify-content-between align-items-start my-4"><div><h1 class="h2">{{ p.name }}</h1><p class="text-secondary mb-0">Создан {{ p.created_at | date:'medium' }} · Ревизия {{ p.current_revision.number }}</p></div><button class="btn btn-primary" type="button" [disabled]="starting()" (click)="start()">{{ starting() ? 'Запуск…' : 'Начать генерацию' }}</button></div>
      <rwb-error [error]="actionError()" />
      <section class="card mb-3"><div class="card-header">Требования</div><div class="card-body"><p class="requirements mb-0">{{ p.current_revision.requirements_text }}</p></div></section>
      <section class="card"><div class="card-header">История запусков</div><div class="card-body"><rwb-error [error]="historyError()" />
        @if (historyError()) { <button class="btn btn-outline-secondary" type="button" (click)="loadHistory()" [disabled]="historyLoading()">Повторить</button> }
        @if (historyLoading()) { <p role="status">Загрузка истории…</p> }
        @for (run of runs(); track run.run_id) { <div class="d-flex gap-3 justify-content-between border-bottom py-3"><a [routerLink]="['/runs',run.run_id]">{{ run.created_at | date:'medium' }}</a><span class="badge text-bg-light border">{{ run.status }}</span></div> } @empty { @if (!historyError() && !historyLoading()) { <p class="text-secondary">Запусков пока нет.</p> } }
        @if (cursor()) { <button class="btn btn-outline-primary mt-3" type="button" (click)="loadHistory(true)" [disabled]="historyLoading()">Загрузить ещё</button> }
      </div></section>
    }
    @if (error()) { <button class="btn btn-outline-primary mt-3" type="button" (click)="load()">Повторить</button> }`,
})
export class ProjectDetail {
  readonly id = input.required<string>();
  private readonly api = inject(ApiClient);
  private readonly router = inject(Router);
  readonly project = signal<Project | null>(null);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly actionError = signal<ApiError | null>(null);
  readonly historyError = signal<ApiError | null>(null);
  readonly historyLoading = signal(false);
  readonly runs = signal<Run[]>([]);
  readonly cursor = signal<string | null>(null);
  readonly starting = signal(false);
  private startKey = crypto.randomUUID();
  private requests = new Subscription();
  constructor() { effect(onCleanup => {
    this.requests = new Subscription();
    this.startKey = crypto.randomUUID(); this.actionError.set(null); this.historyError.set(null);
    this.project.set(null); this.runs.set([]); this.cursor.set(null); this.loading.set(true); this.error.set(null);
    this.requests.add(this.api.project(this.id()).pipe(finalize(() => this.loading.set(false))).subscribe({
      next: p => { this.project.set(p); this.loadHistory(); }, error: e => this.error.set(normalizeError(e)),
    }));
    onCleanup(() => this.requests.unsubscribe());
  }); }
  load() {
    this.error.set(null); this.loading.set(true);
    this.requests.add(this.api.project(this.id()).pipe(finalize(() => this.loading.set(false))).subscribe({ next: p => { this.project.set(p); this.loadHistory(); }, error: e => this.error.set(normalizeError(e)) }));
  }
  loadHistory(append = false) {
    if (this.historyLoading()) return;
    this.historyLoading.set(true); this.historyError.set(null);
    this.requests.add(this.api.runs(this.id(), append ? this.cursor() ?? undefined : undefined).pipe(finalize(() => this.historyLoading.set(false))).subscribe({
      next: page => { this.runs.update(items => append ? [...items,...page.items] : page.items); this.cursor.set(page.next_cursor); },
      error: e => this.historyError.set(normalizeError(e)),
    }));
  }
  start() {
    const project = this.project();
    if (!project || this.starting()) return;
    this.starting.set(true); this.actionError.set(null);
    this.requests.add(this.api.createRun(project.id, project.current_revision.requirements_text, this.startKey).pipe(finalize(() => this.starting.set(false))).subscribe({
      next: run => { this.startKey = crypto.randomUUID(); void this.router.navigate(['/runs',run.run_id]); },
      error: e => this.actionError.set(normalizeError(e)),
    }));
  }
}
