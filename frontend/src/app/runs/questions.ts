import { Component, DestroyRef, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormRecord, ReactiveFormsModule, Validators } from '@angular/forms';
import { finalize } from 'rxjs';
import { ApiClient, Run } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-questions', imports: [ReactiveFormsModule, ErrorBanner],
  template: `@if (run().status === 'waiting_for_input') { <section class="card mt-3"><div class="card-header">Нужно уточнение</div><div class="card-body"><rwb-error [error]="error()" />
    <form [formGroup]="form" (ngSubmit)="submit()">
      @for (question of run().questions; track question.question_id) { <div class="mb-4"><label [for]="question.question_id" class="form-label fw-semibold">{{ question.question }} @if (question.required) { <span class="text-danger">*</span> }</label><p class="small text-secondary mb-2">{{ question.reason }} · {{ question.related_fr_ids.join(', ') }}</p><textarea class="form-control" [id]="question.question_id" [formControlName]="question.question_id" [required]="question.required" maxlength="10000" rows="3"></textarea></div> }
      <button class="btn btn-primary" type="submit" [disabled]="form.invalid || pending()">{{ pending() ? 'Отправка…' : 'Отправить ответы' }}</button>
    </form>
    </div></section> }`,
})
export class Questions {
  readonly run = input.required<Run>();
  readonly updated = output<Run>();
  readonly stale = output<void>();
  private readonly api = inject(ApiClient);
  private readonly destroyRef = inject(DestroyRef);
  readonly pending = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly form = new FormRecord<FormControl<string>>({});
  private revision = '';
  private submitted = '';
  private key = '';
  constructor() {
    effect(() => {
      const run = this.run();
      const revision = `${run.run_id}:${run.questions_revision}`;
      if (revision === this.revision) return;
      this.revision = revision;
      for (const key of Object.keys(this.form.controls)) this.form.removeControl(key);
      for (const question of run.questions) this.form.addControl(question.question_id, new FormControl(question.answer ?? '', { nonNullable: true, validators: [Validators.maxLength(10000), ...(question.required ? [Validators.required] : [])] }));
    });
  }
  submit() {
    if (this.pending() || this.form.invalid || this.run().status !== 'waiting_for_input') return;
    const body = { questions_revision: this.run().questions_revision, answers: Object.entries(this.form.getRawValue()).filter(([,value]) => !!value).map(([question_id,value]) => ({ question_id,value })) };
    if (!body.answers.length) return;
    const content = JSON.stringify(body);
    if (content !== this.submitted) { this.submitted = content; this.key = crypto.randomUUID(); }
    this.pending.set(true); this.error.set(null);
    this.api.answers(this.run().run_id, body, this.key).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.pending.set(false))).subscribe({
      next: run => { this.key = ''; this.submitted = ''; this.updated.emit(run); },
      error: error => { const value = normalizeError(error); this.error.set(value); if (value.status === 409) this.stale.emit(); },
    });
  }
}
