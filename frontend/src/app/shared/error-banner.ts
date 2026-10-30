import { Component, input } from '@angular/core';
import { ApiError } from '../api/errors';

@Component({
  selector: 'rwb-error',
  template: `@if (error(); as e) { <div class="alert alert-danger" role="alert">{{ e.message }} @if (e.requestId) { <small class="d-block">Код обращения: {{ e.requestId }}</small> }</div> }`,
})
export class ErrorBanner { readonly error = input<ApiError | null>(null); }
