import { DTO, Run } from '../api/client';

/** The public DTO has no required_limits; unknown reservation shortfalls need both limits raised. */
export function retryLimitError(run: Run, next: DTO['Limits']): string | null {
  const previous = run.limits;
  if (next.max_calls < previous.max_calls || next.max_output_tokens < previous.max_output_tokens) return 'При повторе нельзя уменьшать лимиты.';
  const callsExhausted = run.metrics.model_calls >= previous.max_calls;
  const tokensExhausted = run.metrics.tokens_out >= previous.max_output_tokens;
  if (callsExhausted && next.max_calls <= previous.max_calls) return 'Увеличьте исчерпанный лимит вызовов.';
  if (tokensExhausted && next.max_output_tokens <= previous.max_output_tokens) return 'Увеличьте исчерпанный лимит выходных токенов.';
  if (!callsExhausted && !tokensExhausted &&
      (next.max_calls <= previous.max_calls || next.max_output_tokens <= previous.max_output_tokens)) {
    return 'Для следующей операции недостаточно бюджета. Увеличьте оба лимита; сервер проверит необходимый запас.';
  }
  return null;
}
