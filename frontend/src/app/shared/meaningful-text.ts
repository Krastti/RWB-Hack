import { AbstractControl, ValidationErrors } from '@angular/forms';

// Python str.isspace / JSON Schema Unicode whitespace, without changing bytes.
export const CONTENT = /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/;
export function meaningfulText(control: AbstractControl): ValidationErrors | null {
  return typeof control.value === 'string' && CONTENT.test(control.value) ? null : { whitespace: true };
}
