import { describe, expect, it } from 'vitest';
import { FormControl } from '@angular/forms';
import { meaningfulText } from './meaningful-text';
it('rejects the same Unicode whitespace as Python without trimming valid text', () => {
  for (const value of ['', ' \t\n', '\u0085\u00a0\u2003\u3000', '\u001c']) {
    expect(meaningfulText(new FormControl(value))).toEqual({whitespace:true});
  }
  const control = new FormControl(' \tТребования\r\n');
  expect(meaningfulText(control)).toBeNull();
  expect(control.value).toBe(' \tТребования\r\n');
});
