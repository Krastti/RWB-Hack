import { Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Subject, catchError, expand, finalize, map, reduce, takeUntil, throwError } from 'rxjs';
import { ApiClient, DTO } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { ErrorBanner } from '../shared/error-banner';

@Component({
  selector: 'rwb-files', imports: [ErrorBanner],
  template: `<section class="card my-4"><div class="card-header d-flex justify-content-between align-items-center gap-3">
    <h2 class="h5 mb-0">Файлы проекта</h2><button type="button" class="btn btn-primary" (click)="download()" [disabled]="!completed() || !snapshot() || downloading()">{{downloading() ? 'Подготовка…' : 'Скачать ZIP'}}</button>
    </div><div class="card-body"><rwb-error [error]="error()" />
    @if (loading()) {<p role="status">Загрузка списка файлов…</p>}
    @if (!completed()) {<p class="text-secondary">Показаны принятые файлы. ZIP станет доступен после завершения сборки.</p>}
    @if (completed()) {<p class="text-secondary">Исходники подготовлены. Сборка, тесты и запуск продукта требуют отдельной проверки.</p>}
    @if (snapshot(); as id) {<p class="small text-secondary text-break">Снимок: {{id}}</p>}
    @if (!loading() && !files().length && !error()) {<p>Файлы ещё не подготовлены.</p>}
    @if (files().length) {<div class="row g-3"><nav class="col-md-4" aria-label="Файлы проекта" style="max-height:38rem;overflow:auto">
      @for (group of groups(); track group.directory) {<details open class="mb-2"><summary class="small fw-semibold text-break">{{group.directory}}</summary>
        @for (file of group.files; track file.artifact_id) {<button type="button" class="btn btn-sm text-start d-block w-100 text-break" [class.btn-light]="selected()?.artifact_id === file.artifact_id" (click)="select(file)">{{file.path}}</button>}
      </details>}
    </nav><div class="col-md-8">
    @if (selected(); as file) {<h3 class="h6 text-break">{{file.path}}</h3><p class="small text-secondary">Версия {{file.version}} · {{file.size_bytes}} байт · {{file.media_type}}</p>
      <details class="small mb-2"><summary>Контрольная сумма SHA-256</summary><code class="text-break">{{file.checksum}}</code></details>
      @if (previewNote()) {<p class="text-secondary" role="status">{{previewNote()}}</p>}
      @if (reading()) {<p role="status">Чтение файла…</p>}
      <pre class="p-3 bg-light border rounded" style="max-height:36rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere">{{content()}}</pre>
    } @else {<p>Выберите файл для просмотра.</p>}
    </div></div>}
    </div></section>`,
})
export class Files {
  readonly runId = input.required<string>();
  readonly refreshKey = input.required<string>();
  readonly completed = input(false);
  private readonly api = inject(ApiClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly scopeChanged = new Subject<void>();
  private readonly selectionChanged = new Subject<void>();
  readonly files = signal<DTO['Artifact'][]>([]);
  readonly groups = computed(() => {
    const groups = new Map<string, DTO['Artifact'][]>();
    for (const file of this.files()) {
      const directory = file.path.includes('/') ? file.path.slice(0,file.path.lastIndexOf('/')) : '/';
      const members = groups.get(directory) ?? []; members.push(file); groups.set(directory,members);
    }
    return Array.from(groups, ([directory,files]) => ({directory,files}));
  });
  readonly snapshot = signal<string | null>(null);
  readonly selected = signal<DTO['Artifact'] | null>(null);
  readonly content = signal('');
  readonly previewNote = signal('');
  readonly error = signal<ApiError | null>(null);
  readonly loading = signal(false);
  readonly reading = signal(false);
  readonly downloading = signal(false);
  constructor() {
    effect(onCleanup => {
      const runId = this.runId(); this.refreshKey();
      this.scopeChanged.next(); this.selectionChanged.next();
      this.files.set([]); this.snapshot.set(null); this.selected.set(null); this.content.set(''); this.previewNote.set(''); this.error.set(null); this.loading.set(true);
      const sub = this.api.artifacts(runId).pipe(
        expand(page => page.next_cursor ? this.api.artifacts(runId, page.next_cursor) : EMPTY),
        reduce((state, page) => {
          if (state.snapshot_id && state.snapshot_id !== page.snapshot_id) throw new Error('Snapshot changed during pagination');
          return { snapshot_id: page.snapshot_id, items: [...state.items, ...page.items] };
        }, { snapshot_id: '', items: [] as DTO['Artifact'][] }),
        catchError(e => normalizeError(e).code === 'MANIFEST_NOT_READY'
          ? this.api.acceptedArtifacts(runId).pipe(map(value => ({snapshot_id:'',items:value.items}))) : throwError(() => e)),
        finalize(() => this.loading.set(false)),
      ).subscribe({next: value => { this.files.set([...value.items].sort((a,b)=>a.path.localeCompare(b.path))); this.snapshot.set(value.snapshot_id || null); },error: e => this.error.set(normalizeError(e))});
      onCleanup(() => sub.unsubscribe());
    });
  }
  select(file: DTO['Artifact']) {
    this.selectionChanged.next(); this.selected.set(file); this.content.set(''); this.previewNote.set(''); this.error.set(null); this.reading.set(false);
    if (file.size_bytes > 1048576 || !(file.media_type.startsWith('text/') || ['application/json','application/yaml','application/javascript'].includes(file.media_type))) {
      this.previewNote.set('Предпросмотр недоступен для большого или двоичного файла. Получите полные байты в ZIP готового проекта.'); return;
    }
    this.reading.set(true);
    this.api.artifact(this.runId(), file.artifact_id).pipe(takeUntil(this.scopeChanged), takeUntil(this.selectionChanged), takeUntilDestroyed(this.destroyRef), finalize(()=>this.reading.set(false))).subscribe({next:text=>{
      this.content.set(text.slice(0,200000));
      if (text.length>200000) this.previewNote.set('Показаны первые 200 000 символов. Полный файл доступен в ZIP.');
    },error:e=>this.error.set(normalizeError(e))});
  }
  download() {
    const snapshot = this.snapshot();
    if (!snapshot || !this.completed() || this.downloading()) return;
    const runId = this.runId(); this.downloading.set(true); this.error.set(null);
    this.api.archive(runId, snapshot).pipe(takeUntil(this.scopeChanged), takeUntilDestroyed(this.destroyRef), finalize(()=>this.downloading.set(false))).subscribe({next: blob => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = `project-${runId}.zip`; link.click();
      setTimeout(()=>URL.revokeObjectURL(url), 1000);
    },error:e=>this.error.set(normalizeError(e))});
  }
}
