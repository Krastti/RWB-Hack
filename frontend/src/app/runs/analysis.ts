import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { Subscription, finalize, forkJoin, map, of, switchMap } from 'rxjs';
import { marked } from 'marked';
import { ApiClient } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-analysis', imports: [ErrorBanner, JsonPipe],
  template: `<section class="card mt-3"><div class="card-header d-flex align-items-center justify-content-between">Анализ требований <button class="btn btn-outline-secondary btn-sm" type="button" (click)="load()" [disabled]="loading()">Обновить</button></div><div class="card-body"><rwb-error [error]="error()" />
    @if (loading()) { <p role="status">Загрузка анализа…</p> }
    @if (!loading() && !error() && !markdown() && !structured()) { <p class="text-secondary">Анализ ещё не подготовлен.</p> }
    @if (markdown()) { <article class="markdown" [innerHTML]="markdown()"></article> }
    @if (structured(); as data) { @for (section of sections; track section.key) { <section class="mt-4"><h3 class="h6">{{ section.title }}</h3><pre class="artifact-content small bg-light p-3 mb-0">{{ data[section.key] ?? [] | json }}</pre></section> } }
  </div></section>`,
})
export class Analysis {
  readonly runId = input.required<string>();
  readonly refreshKey = input('');
  private readonly api = inject(ApiClient);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly markdown = signal('');
  readonly structured = signal<Record<string, unknown> | null>(null);
  readonly sections = [
    { key: 'functional_requirements', title: 'Функциональные требования' },
    { key: 'nonfunctional_requirements', title: 'Нефункциональные требования' },
    { key: 'roles', title: 'Роли' }, { key: 'constraints', title: 'Ограничения' },
    { key: 'assumptions', title: 'Допущения' }, { key: 'questions', title: 'Открытые вопросы' },
  ];
  private request?: Subscription;
  private loadedRunId = '';
  constructor() { effect(onCleanup => {
    this.runId();
    this.refreshKey();
    untracked(() => this.load());
    onCleanup(() => this.request?.unsubscribe());
  }); }
  load() {
    const id = this.runId();
    this.request?.unsubscribe();
    if (this.loadedRunId !== id) {
      this.loadedRunId = id;
      this.markdown.set(''); this.structured.set(null);
    }
    this.loading.set(true); this.error.set(null);
    this.request = this.api.acceptedArtifacts(id).pipe(
      map(page => page.items),
      switchMap(items => {
        const markdown = items.find(item => item.path === 'docs/system-analysis.md');
        const requirements = items.find(item => item.path === 'requirements.json');
        const questions = items.find(item => item.path === 'questions.json');
        return forkJoin({
          markdown: markdown ? this.api.artifact(id, markdown.artifact_id) : of(''),
          requirements: requirements ? this.api.artifact(id, requirements.artifact_id) : of(''),
          questions: questions ? this.api.artifact(id, questions.artifact_id) : of(''),
        });
      }),
      map(result => {
        const requirements: unknown = result.requirements ? JSON.parse(result.requirements) : null;
        const questions: unknown = result.questions ? JSON.parse(result.questions) : null;
        const data = requirements && typeof requirements === 'object' && !Array.isArray(requirements) ? requirements as Record<string, unknown> : null;
        const q = questions && typeof questions === 'object' && 'questions' in questions ? questions.questions : data?.['questions'] ?? [];
        return { html: marked.parse(result.markdown, { async: false }), data: data || result.questions ? { ...data, questions: q } : null };
      }),
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: value => { this.markdown.set(value.html); this.structured.set(value.data); },
      error: error => this.error.set(normalizeError(error)),
    });
  }
}
