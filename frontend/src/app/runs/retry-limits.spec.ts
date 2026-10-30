import { describe, expect, it } from 'vitest';
import { retryLimitError, requiresInternalRecovery } from './retry-limits';
import { makeRun } from '../testing/fixtures';

describe('retry exhausted budget', () => {
  it('does not accept increasing tokens when calls are exhausted', () => {
    const run = makeRun(); run.metrics.model_calls = 10;
    expect(retryLimitError(run, { max_calls: 10, max_output_tokens: 40000 })).toContain('вызовов');
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 30000 })).toBeNull();
  });
  it('does not accept increasing calls when tokens are exhausted', () => {
    const run = makeRun(); run.metrics.tokens_out = 30000;
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 30000 })).toContain('токенов');
    expect(retryLimitError(run, { max_calls: 10, max_output_tokens: 40000 })).toBeNull();
  });
  it('requires increases for both exhausted limits and never reduces another limit', () => {
    const run = makeRun(); run.metrics.model_calls = 10; run.metrics.tokens_out = 30000;
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 30000 })).not.toBeNull();
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 40000 })).toBeNull();
    expect(retryLimitError(run, { max_calls: 9, max_output_tokens: 40000 })).toContain('уменьшать');
  });
  it('allows either limit increase when the server must identify a reservation shortfall', () => {
    const run = makeRun();
    expect(retryLimitError(run, { max_calls: 10, max_output_tokens: 30000 })).not.toBeNull();
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 30000 })).toBeNull();
    expect(retryLimitError(run, { max_calls: 10, max_output_tokens: 40000 })).toBeNull();
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 40000 })).toBeNull();
  });
});

it.each(['MAX_INPUT_TOKENS','RUN_TIMEOUT','unknown',undefined])('rejects unsupported budget cause %s even with both limits raised', stop_reason => {
  const run = makeRun({status:'budget_exceeded',stop_reason});
  expect(retryLimitError(run,{max_calls:100,max_output_tokens:999999})).toContain('RETRY_NOT_SUPPORTED');
});
it('requires the cause-specific limit increase even below visible reservation totals', () => {
  const run = makeRun({status:'budget_exceeded',stop_reason:'MAX_CALLS'});
  expect(retryLimitError(run,{max_calls:10,max_output_tokens:40000})).toContain('вызовов');
});

it('routes succeeded results to internal recovery using latest attempts only', () => {
  const stage = {stage_id:'analysis',attempt_no:1,contract_version:'1',status:'failed' as const};
  const run = makeRun({status:'failed',stages:[stage,{...stage,attempt_no:2,status:'succeeded'}]});
  expect(requiresInternalRecovery(run)).toBe(true);
  expect(requiresInternalRecovery({...run,stages:[...run.stages,{...stage,stage_id:'backend'}]})).toBe(false);
  expect(requiresInternalRecovery({...run,stages:[]})).toBe(false);
  expect(requiresInternalRecovery({...run,status:'budget_exceeded'})).toBe(false);
});
