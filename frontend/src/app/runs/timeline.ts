import { Component, input } from '@angular/core';
import { DatePipe, JsonPipe } from '@angular/common';
import { Run, RunEvent } from '../api/client';

@Component({
  selector: 'rwb-timeline', imports: [DatePipe, JsonPipe],
  template: `<div class="row g-3"><section class="col-lg-5"><div class="card h-100"><div class="card-header">Этапы</div><div class="card-body">
    @for (stage of run().stages; track stage.stage_id + ':' + stage.attempt_no) { <div class="stage-row" [class.current]="stage.stage_id === run().current_stage"><strong>{{ stage.stage_id }}</strong> <span class="badge text-bg-light border">{{ stage.status }}</span><div class="small text-secondary">Попытка {{ stage.attempt_no }} · {{ stage.started_at ? (stage.started_at | date:'medium') : 'Не начат' }}</div>@if (stage.reason) { <p class="small mb-0">{{ stage.reason }}</p> }</div> } @empty { <p class="text-secondary mb-0">Этапы ещё не начались.</p> }
    </div></div></section><section class="col-lg-7"><div class="card h-100"><div class="card-header">События</div><div class="card-body">
    @for (event of events(); track event.seq) { <div class="border-bottom pb-2 mb-2"><div class="d-flex justify-content-between gap-2"><strong>{{ event.event_type }}</strong><span class="small text-secondary">#{{ event.seq }}</span></div><div class="small text-secondary">{{ event.timestamp | date:'medium' }} · {{ event.stage_id }}</div><details><summary class="small">Данные события</summary><pre class="small artifact-content">{{ event.data | json }}</pre></details></div> } @empty { <p class="text-secondary mb-0">История событий пока пуста.</p> }
    </div></div></section></div>`,
})
export class Timeline {
  readonly run = input.required<Run>();
  readonly events = input<RunEvent[]>([]);
}
