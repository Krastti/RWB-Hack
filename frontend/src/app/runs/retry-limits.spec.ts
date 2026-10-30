import { describe, expect, it } from 'vitest';
import { retryLimitError } from './retry-limits';
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
  it('requires both limits when public metrics do not identify a reservation shortfall', () => {
    const run = makeRun();
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 30000 })).toContain('оба');
    expect(retryLimitError(run, { max_calls: 11, max_output_tokens: 40000 })).toBeNull();
  });
});
