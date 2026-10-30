import { DTO, Run } from '../api/client';

/** Unknown reservation shortfalls are validated by the server after a limit increase. */
export function retryLimitError(run: Run, next: DTO['Limits']): string | null {
  if (!supportsBudgetRetry(run)) return 'Повтор не поддерживается для исчерпанного лимита (RETRY_NOT_SUPPORTED).';
  const previous = run.limits;
  if (next.max_calls < previous.max_calls || next.max_output_tokens < previous.max_output_tokens) return 'При повторе нельзя уменьшать лимиты.';
  const callsExhausted = run.metrics.model_calls >= previous.max_calls || run.stop_reason === 'MAX_CALLS';
  const tokensExhausted = run.metrics.tokens_out >= previous.max_output_tokens || run.stop_reason === 'MAX_OUTPUT_TOKENS';
  if (callsExhausted && next.max_calls <= previous.max_calls) return 'Увеличьте исчерпанный лимит вызовов.';
  if (tokensExhausted && next.max_output_tokens <= previous.max_output_tokens) return 'Увеличьте исчерпанный лимит выходных токенов.';
  if (!callsExhausted && !tokensExhausted &&
      (next.max_calls <= previous.max_calls && next.max_output_tokens <= previous.max_output_tokens)) {
    return 'Увеличьте хотя бы один лимит; сервер проверит необходимый запас.';
  }
  return null;
}

/** Public DTO cannot raise input-token or active-time budgets. Unknown reasons fail closed. */
export function supportsBudgetRetry(run: Run): boolean {
  return run.status !== 'budget_exceeded' || ['MAX_CALLS', 'MAX_OUTPUT_TOKENS'].includes(run.stop_reason ?? '');
}
