import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const name of ['window', 'document', 'Node', 'HTMLElement', 'Event']) {
  globalThis[name] = name === 'window' ? dom.window : dom.window[name];
}
await import('@angular/compiler');
const { provideZonelessChangeDetection } = await import('@angular/core');
const { TestBed } = await import('@angular/core/testing');
const { BrowserTestingModule, platformBrowserTesting } = await import('@angular/platform-browser/testing');
const { DomainActionComponent } = await import('./out/domain-action.js');
TestBed.initTestEnvironment(BrowserTestingModule, platformBrowserTesting());
afterEach(() => TestBed.resetTestingModule());
after(() => dom.window.close());

const ticket = () => ({ owner_id: 'alice', status: 'draft', priority: 1 });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle(fixture) {
  await new Promise(setImmediate);
  await fixture.whenStable();
}
async function inputs(fixture, values) {
  for (const [name, value] of Object.entries(values)) fixture.componentRef.setInput(name, value);
  await settle(fixture);
}
async function mount(gateway) {
  TestBed.configureTestingModule({ imports: [DomainActionComponent], providers: [provideZonelessChangeDetection()] });
  const fixture = TestBed.createComponent(DomainActionComponent);
  await inputs(fixture, { operationId: 'cancelTicket', recordId: '1', record: ticket(),
    actor: { id: 'alice', role: 'ROLE-001' }, gateway });
  return fixture;
}
function button(fixture) { return fixture.nativeElement.querySelector('button'); }

test('server rejection and refresh update the zoneless DOM without detectChanges', async () => {
  const submitted = deferred(), refreshed = deferred();
  const fixture = await mount({ submit: () => submitted.promise, refresh: () => refreshed.promise });
  assert.equal(button(fixture).disabled, false);
  fixture.nativeElement.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(fixture);
  assert.equal(button(fixture).disabled, true);
  submitted.reject(new Error('rejected'));
  await settle(fixture);
  refreshed.resolve(ticket());
  await settle(fixture);
  assert.equal(fixture.componentInstance.busy, false);
  assert.equal(fixture.componentInstance.decision.status, 'allowed');
  assert.equal(button(fixture).disabled, false);
  assert.match(fixture.nativeElement.querySelector('[role="alert"]').textContent, /Сервер отклонил/);
});

test('successful submit renders refreshed policy state', async () => {
  const fixture = await mount({ submit: async () => {}, refresh: async () => ({ ...ticket(), status: 'cancelled' }) });
  await fixture.componentInstance.submit();
  await settle(fixture);
  assert.equal(fixture.componentInstance.busy, false);
  assert.equal(fixture.componentInstance.decision.status, 'denied');
  assert.equal(button(fixture).disabled, true);
  assert.match(fixture.nativeElement.querySelector('.form-text').textContent, /Действие запрещено/);
});

test('label changes preserve a failed refresh until a new record arrives', async () => {
  let submissions = 0;
  const fixture = await mount({ submit: async () => { submissions++; }, refresh: async () => { throw new Error('offline'); } });
  const component = fixture.componentInstance;
  await component.submit();
  await settle(fixture);
  assert.equal(component.decision.reason, 'REFRESH_REQUIRED');
  const record = component.record, error = component.error;
  await inputs(fixture, { label: 'Повторить', record });
  assert.equal(component.decision.reason, 'REFRESH_REQUIRED');
  assert.equal(component.error, error);
  assert.equal(button(fixture).disabled, true);
  await component.submit();
  assert.equal(submissions, 1);
  await inputs(fixture, { record: ticket() });
  assert.equal(component.decision.status, 'allowed');
  assert.equal(button(fixture).disabled, false);
});

for (const change of ['operationId', 'recordId', 'gateway', 'actor']) {
  test(`${change} without replacement data does not clear REFRESH_REQUIRED`, async () => {
    const fixture = await mount({ submit: async () => {}, refresh: async () => { throw new Error('offline'); } });
    await fixture.componentInstance.submit();
    const values = { operationId: 'approveOrder', recordId: '2',
      gateway: { submit: async () => {}, refresh: async () => ticket() }, actor: { id: 'bob', role: 'ROLE-001' } };
    await inputs(fixture, { [change]: values[change] });
    assert.equal(fixture.componentInstance.decision.reason, 'REFRESH_REQUIRED');
    assert.equal(button(fixture).disabled, true);
  });
}

for (const outcome of ['resolve', 'reject']) {
  test(`old refresh ${outcome} cannot overwrite another operation with the same record ID`, async () => {
    const old = deferred(), started = deferred();
    const fixture = await mount({ submit: async () => {}, refresh: () => { started.resolve(); return old.promise; } });
    const component = fixture.componentInstance;
    const pending = component.submit();
    await started.promise;
    const order = { order_status: 'new' };
    await inputs(fixture, { operationId: 'approveOrder', record: order,
      gateway: { submit: async () => {}, refresh: async () => order } });
    const before = { record: component.record, error: component.error, decision: component.decision, busy: component.busy };
    old[outcome](outcome === 'resolve' ? { ...ticket(), status: 'cancelled' } : new Error('stale failure'));
    await pending;
    assert.equal(component.record, before.record);
    assert.equal(component.error, before.error);
    assert.deepEqual(component.decision, before.decision);
    assert.equal(component.busy, before.busy);
  });
}

test('gateway change invalidates refresh even when operation and record ID are unchanged', async () => {
  const old = deferred(), started = deferred();
  const fixture = await mount({ submit: async () => {}, refresh: () => { started.resolve(); return old.promise; } });
  const component = fixture.componentInstance;
  const pending = component.submit();
  await started.promise;
  const record = component.record;
  await inputs(fixture, { gateway: { submit: async () => {}, refresh: async () => ticket() } });
  old.resolve({ ...ticket(), status: 'cancelled' });
  await pending;
  assert.equal(component.record, record);
  assert.equal(component.decision.reason, 'REFRESH_REQUIRED');
});

test('A to B to A ignores old refresh and does not clear a newer request busy state', async () => {
  const first = deferred(), second = deferred(), started = deferred();
  let refreshes = 0;
  const gateway = { submit: async () => {}, refresh: () => {
    refreshes++;
    if (refreshes === 1) { started.resolve(); return first.promise; }
    return second.promise;
  } };
  const fixture = await mount(gateway), component = fixture.componentInstance;
  const old = component.submit();
  await started.promise;
  await inputs(fixture, { recordId: '2', record: ticket() });
  const current = ticket();
  await inputs(fixture, { recordId: '1', record: current });
  const active = component.submit();
  await settle(fixture);
  assert.equal(refreshes, 2);
  first.resolve({ ...ticket(), status: 'cancelled' });
  await old;
  assert.equal(component.record, current);
  assert.equal(component.busy, true);
  second.resolve(ticket());
  await active;
  await settle(fixture);
  assert.equal(component.busy, false);
  assert.equal(button(fixture).disabled, false);
});

test('replacement record invalidates an in-flight refresh in the same context', async () => {
  const old = deferred(), started = deferred();
  const fixture = await mount({ submit: async () => {}, refresh: () => { started.resolve(); return old.promise; } });
  const component = fixture.componentInstance, pending = component.submit();
  await started.promise;
  const current = { ...ticket(), priority: 2 };
  await inputs(fixture, { record: current });
  old.resolve({ ...ticket(), status: 'cancelled' });
  await pending;
  assert.equal(component.record, current);
  assert.equal(component.decision.status, 'allowed');
});

test('late submit rejection does not set error or refresh the new gateway', async () => {
  const old = deferred();
  let oldRefreshes = 0, newRefreshes = 0;
  const fixture = await mount({ submit: () => old.promise, refresh: async () => { oldRefreshes++; return ticket(); } });
  const component = fixture.componentInstance, pending = component.submit();
  await inputs(fixture, { record: ticket(), gateway: {
    submit: async () => {}, refresh: async () => { newRefreshes++; return ticket(); },
  } });
  old.reject(new Error('old request'));
  await pending;
  assert.equal(component.error, '');
  assert.equal(oldRefreshes, 0);
  assert.equal(newRefreshes, 0);
});

test('label updates do not cancel an active request', async () => {
  const old = deferred();
  const fixture = await mount({ submit: async () => {}, refresh: () => old.promise });
  const component = fixture.componentInstance, pending = component.submit();
  await inputs(fixture, { label: 'Другая подпись' });
  assert.equal(component.busy, true);
  old.resolve({ ...ticket(), status: 'cancelled' });
  await pending;
  await settle(fixture);
  assert.equal(component.decision.status, 'denied');
});

test('destroyed component ignores a pending refresh', async () => {
  const old = deferred(), started = deferred();
  const fixture = await mount({ submit: async () => {}, refresh: () => { started.resolve(); return old.promise; } });
  const component = fixture.componentInstance, pending = component.submit();
  await started.promise;
  const record = component.record;
  fixture.destroy();
  old.resolve({ ...ticket(), status: 'cancelled' });
  await pending;
  assert.equal(component.record, record);
});
