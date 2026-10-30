import { Project, Run } from '../api/client';

export function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'a42e19f4-80a9-5f04-941c-6d3ae4437755',
    project_id: 'a42e19f4-80a9-5f04-941c-6d3ae4437744',
    revision_id: 'a42e19f4-80a9-5f04-941c-6d3ae4437733',
    profile_id: 'web-fastapi-angular', status: 'running',
    limits: { max_calls: 10, max_output_tokens: 30000 },
    created_at: '2026-10-30T12:00:00Z', updated_at: '2026-10-30T12:00:00Z',
    stages: [], questions: [], questions_revision: 0,
    metrics: { tokens_in: 0, tokens_out: 0, model_calls: 0, attempts: 1, manual_clarifications: 0, duration_ms: 0 },
    ...overrides,
  };
}

export function makeProject(id: string): Project {
  return { id, owner_id: id, name: id, created_at: '2026-10-30T12:00:00Z', updated_at: '2026-10-30T12:00:00Z',
    current_revision: { id, number: 1, requirements_text: 'Test requirements', hash: 'a'.repeat(64) },
    latest_run_status: 'completed' };
}
