import { Injectable, inject } from '@angular/core';
import { EMPTY, Subject, catchError, defer, exhaustMap, expand, map, merge, of, reduce, switchMap, takeUntil, takeWhile, timer } from 'rxjs';
import { ApiClient, DTO, Run, RunEvent, TERMINAL } from '../api/client';
import { ApiError, normalizeError } from '../api/errors';
import { Session } from '../auth/session';

export interface RunSnapshot { run: Run | null; events: RunEvent[]; error: ApiError | null }

@Injectable()
export class RunMonitor {
  private readonly api = inject(ApiClient);
  private readonly session = inject(Session);
  readonly refresh = new Subject<void>();

  watch(id: string) {
    return defer(() => {
      let run: Run | null = null;
      let events: RunEvent[] = [];
      let cursor = 0;
      return merge(timer(0, 2000), this.refresh).pipe(
        exhaustMap(() => this.api.run(id).pipe(
          switchMap(value => {
            run = value;
            let after = cursor;
            return this.api.events(id, cursor).pipe(
              expand(page => {
                if (page.next_after_seq < after || (page.has_more && page.next_after_seq <= after)) throw new Error('Event cursor did not advance');
                after = page.next_after_seq;
                if (!page.has_more) return EMPTY;
                return this.api.events(id, after);
              }),
              reduce((pages: DTO['EventPage'][], page) => [...pages, page], []),
              map(pages => {
                const merged = new Map(events.map(e => [e.seq, e]));
                for (const page of pages) for (const event of page.items) if (event.run_id === id) merged.set(event.seq, event);
                events = [...merged.values()].sort((a,b) => a.seq - b.seq);
                cursor = Math.max(cursor, ...pages.map(p => p.next_after_seq));
                return { run, events, error: null } as RunSnapshot;
              }),
            );
          }),
          catchError(error => of({ run, events, error: normalizeError(error) } as RunSnapshot)),
        )),
        takeWhile(snapshot => !!snapshot.error || !snapshot.run || !TERMINAL.has(snapshot.run.status), true),
        takeUntil(this.session.ended),
      );
    });
  }
}
