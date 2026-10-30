import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({ selector: 'rwb-not-found', imports: [RouterLink], template: '<main class="container py-5"><h1>Страница не найдена</h1><a routerLink="/projects">К проектам</a></main>' })
export class NotFound {}
