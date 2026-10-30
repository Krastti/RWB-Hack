import { test, expect, Page } from '@playwright/test';

const projectId = 'a42e19f4-80a9-5f04-941c-6d3ae4437744';
const runId = 'a42e19f4-80a9-5f04-941c-6d3ae4437755';
const date = '2026-10-30T12:00:00Z';
const project = { id: projectId, owner_id: projectId, name: 'Сервис заявок', created_at: date, updated_at: date, current_revision: { id: projectId, number: 1, requirements_text: 'Сотрудник подаёт заявку.', hash: 'a'.repeat(64) } };
const run = { run_id: runId, project_id: projectId, revision_id: projectId, profile_id: 'web-fastapi-angular', status: 'waiting_for_input', limits: { max_calls: 10, max_output_tokens: 30000 }, created_at: date, updated_at: date, stages: [{ stage_id: 'analysis', attempt_no: 1, contract_version: '1', status: 'waiting_for_input', started_at: date }], questions_revision: 1, questions: [{ question_id: projectId, question: 'Кто видит заявки?', reason: 'Нужны права доступа.', related_fr_ids: ['FR-001'], required: true }], metrics: { tokens_in: 10, tokens_out: 20, model_calls: 1, attempts: 1, manual_clarifications: 0, duration_ms: 1234 } };

async function fixtures(page: Page) {
  let created = false;
  let completed = false;
  const calls = { create: 0, answers: 0, artifactLists: 0 };
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    const json = (data: unknown, status = 200) => route.fulfill({ status, json: data });
    if (path === '/auth/token') return json({ access_token: 'test-token', token_type: 'bearer', expires_in: 600 });
    expect(request.headers()['authorization']).toBe('Bearer test-token');
    if (path === '/projects' && request.method() === 'POST') { calls.create++; created = true; return json(project,201); }
    if (path === '/projects') return json({ items: created ? [project] : [], next_cursor: null });
    if (path === `/projects/${projectId}`) return json(project);
    if (path === `/projects/${projectId}/runs` && request.method() === 'POST') {
      expect(request.postDataJSON().profile_id).toBe('web-fastapi-angular');
      expect(request.headers()['idempotency-key']).toBeTruthy();
      return json(run,202);
    }
    if (path === `/projects/${projectId}/runs`) return json({ items: [run], next_cursor: null });
    if (path === `/runs/${runId}`) return json({ ...run, status: completed ? 'completed' : 'waiting_for_input' });
    if (path.endsWith('/events')) return json({ items: [], next_after_seq: 0, has_more: false });
    if (path.endsWith('/answers')) { calls.answers++; completed = true; expect(request.postDataJSON().questions_revision).toBe(1); return json({ ...run,status:'completed' },202); }
    if (path.endsWith('/artifacts')) {
      calls.artifactLists++;
      return json({ snapshot_id: projectId, next_cursor: null, items: [{ artifact_id: 'md', path: 'docs/system-analysis.md' },{ artifact_id: 'json', path: 'requirements.json' }] });
    }
    if (path.endsWith('/artifacts/md')) return route.fulfill({ contentType: 'text/markdown', body: '# Анализ\n<img src=x onerror="window.pwned=true"><script>window.pwned=true</script>' });
    if (path.endsWith('/artifacts/json')) return json({ functional_requirements: [{ id:'FR-001',text:'Подать заявку' }],nonfunctional_requirements:[{text:'Быстро'}],roles:[],constraints:[],assumptions:[{text:'Первый прототип'}],questions:run.questions });
    return json({ error: { code: 'NOT_FOUND', message: 'Unavailable', request_id:'test',details:{} } },404);
  });
  return calls;
}

async function login(page: Page) {
  await page.goto('/projects');
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel('Логин', { exact: true }).fill('alice');
  await page.getByLabel('Пароль', { exact: true }).fill('secret');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/projects$/);
}

test('login, project creation, generation, clarification, safe analysis and logout', async ({ page }) => {
  const calls = await fixtures(page);
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await login(page);
  await expect(page.getByText('Пока нет проектов')).toBeVisible();
  await page.getByRole('link', { name: 'Новый проект',exact:true }).click();
  await page.getByLabel('Название',{exact:true}).fill(project.name);
  await page.getByLabel('Требования',{exact:true}).fill(project.current_revision.requirements_text);
  await page.getByRole('button', { name:'Создать проект',exact:true }).click();
  await expect(page.getByRole('heading', { name: project.name })).toBeVisible();
  expect(calls.create).toBe(1);
  await page.getByRole('button', { name: 'Начать генерацию' }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await expect(page.getByRole('heading', { name:'Анализ',exact:true })).toBeVisible();
  await expect(page.getByRole('heading', { name:'Допущения',exact:true })).toBeVisible();
  await expect(page.getByRole('heading', { name:'Нефункциональные требования',exact:true })).toBeVisible();
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
  const count = calls.artifactLists;
  await page.getByRole('button', { name:'Обновить',exact:true }).click();
  await expect.poll(() => calls.artifactLists).toBe(count+1);
  await page.getByLabel('Кто видит заявки?',{exact:false}).fill('Только владелец.');
  await page.getByRole('button', { name:'Отправить ответы' }).click();
  await expect(page.getByText('completed',{exact:true})).toBeVisible();
  expect(calls.answers).toBe(1);
  await page.getByRole('button', { name:'Выйти',exact:true }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
  expect(errors).toEqual([]);
});

test('responsive layout and navigation at 360, 768 and 1280 pixels', async ({ page }) => {
  await fixtures(page); await login(page);
  for (const width of [360,768,1280]) {
    await page.setViewportSize({ width,height:900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (width < 768) {
      await page.getByRole('button', { name:'Переключить меню' }).click();
      await expect(page.getByRole('navigation', { name:'Основная навигация' })).toBeVisible();
      await page.getByRole('button', { name:'Закрыть меню',exact:true }).click();
    } else await expect(page.getByRole('navigation', { name:'Основная навигация' })).toBeVisible();
    await page.screenshot({ path: `/tmp/rwb-angular-${width}.png`,fullPage:true });
  }
});

test('protected 401 clears session and returns to login', async ({ page }) => {
  await fixtures(page); await login(page);
  await page.route('**/api/v1/projects', route => route.fulfill({ status:401,json:{ error:{ code:'UNAUTHORIZED',message:'Session expired',details:{},request_id:'expired' } } }));
  await page.reload();
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
});

test('budget retry requires increased limits and blocks duplicate submissions', async ({ page }) => {
  await fixtures(page); await login(page);
  let retried = false;
  let requests = 0;
  await page.route(`**/api/v1/runs/${runId}`, route => route.fulfill({ json: { ...run,status:retried ? 'completed' : 'budget_exceeded',metrics:{ ...run.metrics,model_calls:10 },stop_reason:'MODEL_CALL_LIMIT' } }));
  await page.route(`**/api/v1/runs/${runId}/retry`, async route => {
    requests++;
    expect(route.request().headers()['idempotency-key']).toBeTruthy();
    expect(route.request().postDataJSON()).toEqual({ limits:{ max_calls:11,max_output_tokens:30000 } });
    // Keep the request pending long enough to verify the UI lock.
    await new Promise(resolve => setTimeout(resolve,300));
    retried=true; await route.fulfill({ status:202,json:{ ...run,status:'completed' } });
  });
  await page.goto(`/runs/${runId}`);
  const retry = page.getByRole('button',{ name:'Повторить от контрольной точки' });
  await retry.click();
  await expect(page.getByText('Увеличьте исчерпанный лимит вызовов.')).toBeVisible();
  expect(requests).toBe(0);
  await page.getByLabel('Лимит вызовов').fill('11');
  await retry.click();
  await expect(page.getByRole('button',{name:'Отправка…',exact:true})).toBeDisabled();
  await expect(page.getByText('completed',{exact:true})).toBeVisible();
  expect(requests).toBe(1);
});

test('analysis refreshes automatically when an unchanged run id completes', async ({ page }) => {
  await fixtures(page); await login(page);
  let polls = 0;
  let completed = false;
  await page.route(`**/api/v1/runs/${runId}`, route => {
    completed = ++polls > 1;
    return route.fulfill({ json:{ ...run,status:completed ? 'completed' : 'running' } });
  });
  await page.route(`**/api/v1/runs/${runId}/artifacts`, route => route.fulfill({ json:{
    snapshot_id:projectId,next_cursor:null,items:completed ? [{ artifact_id:'finished',path:'docs/system-analysis.md' }] : [],
  } }));
  await page.route(`**/api/v1/runs/${runId}/artifacts/finished`, route => route.fulfill({ contentType:'text/markdown',body:'# Финальный анализ' }));
  await page.goto(`/runs/${runId}`);
  await expect(page.getByText('Анализ ещё не подготовлен.')).toBeVisible();
  await expect(page.getByRole('heading',{name:'Финальный анализ',exact:true})).toBeVisible({timeout:8000});
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test('project statuses load in one list response without per-project requests', async ({ page }) => {
  await fixtures(page); await login(page);
  let histories = 0;
  await page.route('**/api/v1/projects/*/runs', route => { histories++; return route.fulfill({ json:{items:[],next_cursor:null} }); });
  await page.route('**/api/v1/projects', route => route.fulfill({ json:{items:[
    {...project,id:projectId,name:'Первый',latest_run_status:'completed'},
    {...project,id:runId,name:'Второй',latest_run_status:'failed'},
  ],next_cursor:null} }));
  await page.reload();
  await expect(page.getByRole('cell',{name:'completed',exact:true})).toBeVisible();
  await expect(page.getByRole('cell',{name:'failed',exact:true})).toBeVisible();
  expect(histories).toBe(0);
});

test('stale clarification revision is refreshed before the next answer package', async ({ page }) => {
  await fixtures(page); await login(page);
  let revision = 1;
  let completed = false;
  await page.route(`**/api/v1/runs/${runId}`, route => route.fulfill({ json:{ ...run,questions_revision:revision,status:completed ? 'completed' : 'waiting_for_input' } }));
  await page.route(`**/api/v1/runs/${runId}/answers`, async route => {
    const body = route.request().postDataJSON();
    if (revision === 1) {
      expect(body.questions_revision).toBe(1); revision=2;
      return route.fulfill({ status:409,json:{ error:{ code:'STALE_REVISION',message:'Вопросы изменились.',details:{},request_id:'stale' } } });
    }
    expect(body.questions_revision).toBe(2); completed=true;
    return route.fulfill({ status:202,json:{ ...run,questions_revision:2,status:'completed' } });
  });
  await page.goto(`/runs/${runId}`);
  const answer = page.getByLabel('Кто видит заявки?',{exact:false});
  await answer.fill('Владелец');
  await page.getByRole('button',{name:'Отправить ответы'}).click();
  await expect(page.getByText('Вопросы изменились.')).toBeVisible();
  await expect(answer).toHaveValue('');
  await answer.fill('Только владелец');
  await page.getByRole('button',{name:'Отправить ответы'}).click();
  await expect(page.getByText('completed',{exact:true})).toBeVisible();
});
