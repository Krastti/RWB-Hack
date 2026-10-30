import { Run } from '../api/client';

/** Historical profiles are readable only; commands require the active profile. */
export function supportsRunCommands(run: Run): boolean {
  return run.profile_id === 'web-fastapi-angular';
}
