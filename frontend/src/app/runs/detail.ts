import { supportsRunCommands } from './profile-policy';
import { Component, DestroyRef, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, finalize, takeUntil } from 'rxjs';
import { ApiClient, Run, RunEvent } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';
import { RunMonitor } from './monitor';
import { Timeline } from './timeline';
import { Questions } from './questions';
import { Analysis } from './analysis';
import { retryLimitError, supportsBudgetRetry, requiresInternalRecovery } from './retry-limits';

@Component({
  selector: 'rwb-run-detail', imports: [RouterLink, ReactiveFormsModule, ErrorBanner, Timeline, Questions, Analysis], providers: [RunMonitor],
  template: `<rwb-error [error]="error()" />
    @if (!run() && !error()) { <p role="status">Загрузка запуска…</p> }
    @if (run(); as r) {
      <a [routerLink]="['/projects',r.project_id]" class="small">← К проекту</a>
      <div class="d-flex justify-content-between gap-3 align-items-start my-4"><div><h1 class="h2">Запуск проекта</h1><p class="text-secondary mb-0">{{ r.run_id }}</p></div><span class="badge text-bg-light border fs-6">{{ r.status }}</span></div>
      @if (r.stop_reason) { <div class="alert alert-warning">{{ r.stop_reason }}</div> }
      <rwb-error [error]="actionError()" />
      <div class="row g-3 mb-4">@for (metric of metrics(r); track metric.name) { <div class="col-6 col-xl-3"><div class="card"><div class="card-body"><div class="text-secondary small">{{ metric.name }}</div><div class="h4 mb-0 mt-2">{{ metric.value }}</div>@if (metric.note) { <p class="small text-secondary mt-2 mb-0">{{ metric.note }}</p> }</div></div></div> }</div>
      <div class="mb-4">
        @if (!supportsRunCommands(r)) { <p class="text-secondary" role="status">Исторический профиль: запуск доступен только для просмотра. Команды управления не поддерживаются.</p> }
        @if (supportsRunCommands(r) && ['queued','running','waiting_for_input'].includes(r.status)) { <button class="btn btn-outline-danger" type="button" (click)="cancel()" [disabled]="pending()">{{ pending() ? 'Отправка…' : 'Отменить запуск' }}</button> }
        @if (r.status === 'cancelling') { <p role="status">Отмена будет выполнена на границе этапа.</p> }
        @if (r.status === 'cancelled') { <p class="text-secondary">Запуск отменён. Новую генерацию можно начать из карточки проекта.</p> }
        @if (supportsRunCommands(r) && !supportsBudgetRetry(r)) { <p class="text-secondary">Повтор не поддерживается для исчерпанного лимита (RETRY_NOT_SUPPORTED).</p> }
        @if (supportsRunCommands(r) && requiresInternalRecovery(r)) { <p class="text-secondary" role="status">Результаты этапов сохранены. Требуется внутреннее восстановление checkpoint или сборки (RECOVERY_REQUIRED); повторная генерация не требуется.</p> }
        @if (supportsRunCommands(r) && !requiresInternalRecovery(r) && supportsBudgetRetry(r) && (r.status === 'failed' || r.status === 'budget_exceeded')) {
          <form [formGroup]="retryForm" (ngSubmit)="retry()" class="d-flex flex-wrap gap-3 align-items-end">
            @if (r.status === 'budget_exceeded') { <div><label for="max-calls" class="form-label">Лимит вызовов</label><input id="max-calls" class="form-control" type="number" formControlName="max_calls" min="1" required></div><div><label for="max-tokens" class="form-label">Лимит выходных токенов</label><input id="max-tokens" class="form-control" type="number" formControlName="max_output_tokens" min="1" required></div> }
            <button class="btn btn-primary" type="submit" [disabled]="pending() || retryForm.invalid">{{ pending() ? 'Отправка…' : 'Повторить от контрольной точки' }}</button>
          </form>
        }
      </div>
      <rwb-timeline [run]="r" [events]="events()" />
      @if (supportsRunCommands(r)) { <rwb-questions [run]="r" (updated)="update($event)" (stale)="monitor.refresh.next()" /> }
      <rwb-analysis [runId]="r.run_id" [refreshKey]="analysisRefreshKey(r)" />
    }`,
})
export class RunDetail {
  readonly supportsRunCommands = supportsRunCommands;
  readonly supportsBudgetRetry = supportsBudgetRetry;
  readonly requiresInternalRecovery = requiresInternalRecovery;
  readonly id = input.required<string>();
  private readonly api = inject(ApiClient);
  private readonly destroyRef = inject(DestroyRef);
  readonly monitor = inject(RunMonitor);
  readonly run = signal<Run | null>(null);
  readonly events = signal<RunEvent[]>([]);
  readonly error = signal<ApiError | null>(null);
  readonly actionError = signal<ApiError | null>(null);
  readonly pending = signal(false);
  readonly retryForm = inject(FormBuilder).nonNullable.group({ max_calls: [1, [Validators.required, Validators.min(1), Validators.pattern(/^\d+$/)]], max_output_tokens: [1, [Validators.required, Validators.min(1), Validators.pattern(/^\d+$/)]] });
  private retryKey = '';
  private retryBody = '';
  private retryInitialized = '';
  private watchedId = '';
  private readonly changedRun = new Subject<void>();
  private readonly generation = signal(0);
  constructor() {
    effect(onCleanup => {
      const id = this.id(); this.generation();
      if (id !== this.watchedId) {
        this.changedRun.next(); this.watchedId = id;
        this.retryKey = ''; this.retryBody = ''; this.retryInitialized = '';
        this.actionError.set(null); this.pending.set(false);
      }
      this.run.set(null); this.events.set([]); this.error.set(null);
      const sub = this.monitor.watch(id).subscribe(snapshot => {
        this.run.set(snapshot.run); this.events.set(snapshot.events); this.error.set(snapshot.error);
        if (snapshot.run) {
          const current = snapshot.run;
          const budget = current.status === 'budget_exceeded'
            ? `${id}:${current.limits.max_calls}:${current.limits.max_output_tokens}:${current.metrics.attempts}`
            : '';
          if (budget && budget !== this.retryInitialized) this.retryForm.reset(current.limits);
          this.retryInitialized = budget;
        }
      });
      onCleanup(() => sub.unsubscribe());
    });
  }
  update(run: Run) { this.run.set(run); this.generation.update(value => value + 1); }
  analysisRefreshKey(run: Run) {
    const artifacts = this.events().filter(event => event.event_type === 'artifact.accepted' || event.event_type === 'snapshot.prepared');
    return `${run.status}:${run.questions_revision}:${artifacts.at(-1)?.seq ?? 0}`;
  }
  metrics(run: Run) {
    return [{ name: 'Токены вход / выход', value: `${run.metrics.tokens_in} / ${run.metrics.tokens_out}`, note: 'Известный расход · полнота неизвестна' }, { name: 'Вызовы модели', value: run.metrics.model_calls, note: '' }, { name: 'Попытки', value: run.metrics.attempts, note: '' }, { name: 'Время, сек', value: Math.round(run.metrics.duration_ms / 1000), note: '' }];
  }
  cancel() {
    const run = this.run();
    if (!run || !supportsRunCommands(run) || this.pending() || !['queued','running','waiting_for_input'].includes(run.status)) return;
    this.pending.set(true); this.actionError.set(null);
    this.api.cancel(run.run_id).pipe(takeUntil(this.changedRun), takeUntilDestroyed(this.destroyRef), finalize(() => this.pending.set(false))).subscribe({ next: value => this.update(value), error: e => this.actionError.set(normalizeError(e)) });
  }
  retry() {
    const run = this.run();
    if (!run || !supportsRunCommands(run) || !supportsBudgetRetry(run) || requiresInternalRecovery(run) || this.pending() || this.retryForm.invalid || !['failed','budget_exceeded'].includes(run.status)) return;
    const limits = this.retryForm.getRawValue();
    const limitError = run.status === 'budget_exceeded' ? retryLimitError(run, limits) : null;
    if (limitError) {
      this.actionError.set(new ApiError(422, 'LIMIT_NOT_INCREASED', limitError, '')); return;
    }
    const body = run.status === 'budget_exceeded' ? { limits } : {};
    const serialized = JSON.stringify(body);
    if (!this.retryKey || this.retryBody !== serialized) { this.retryKey = crypto.randomUUID(); this.retryBody = serialized; }
    this.pending.set(true); this.actionError.set(null);
    this.api.retry(run.run_id, body, this.retryKey).pipe(takeUntil(this.changedRun), takeUntilDestroyed(this.destroyRef), finalize(() => this.pending.set(false))).subscribe({ next: value => { this.retryKey = ''; this.update(value); }, error: e => this.actionError.set(normalizeError(e)) });
  }
}
