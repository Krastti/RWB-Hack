import { Component, HostListener, effect, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Session } from '../auth/session';

// Navigation structure adapted from SB Admin Angular. See THIRD_PARTY_NOTICES.md.
@Component({
  selector: 'rwb-layout', imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <a class="visually-hidden-focusable skip-link" href="#main-content">К содержимому</a>
    <header class="sb-topnav navbar navbar-dark bg-dark px-3">
      <a class="navbar-brand fw-semibold" routerLink="/projects">RWB <span class="text-white-50 fw-normal">/ Studio</span></a>
      <button class="btn btn-outline-light btn-sm me-auto ms-3 d-md-none" type="button" (click)="menuOpen.set(!menuOpen())" [attr.aria-expanded]="menuOpen()" aria-controls="sidebar" aria-label="Переключить меню">☰</button>
      <span class="text-white-50 ms-auto me-3 session-login">{{ session.state()?.login }}</span>
      <button class="btn btn-outline-light btn-sm" type="button" (click)="logout()">Выйти</button>
    </header>
    <div class="sb-layout" [class.menu-open]="menuOpen()">
      @if (menuOpen()) { <button class="sidebar-scrim" type="button" aria-label="Закрыть меню" (click)="menuOpen.set(false)"></button> }
      <aside id="sidebar" class="sb-sidenav bg-dark text-white" [class.mobile-open]="menuOpen()">
        <nav aria-label="Основная навигация" class="p-3">
          <div class="small text-uppercase text-white-50 mb-3">Рабочее пространство</div>
          <a routerLink="/projects" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }" class="nav-link" (click)="menuOpen.set(false)">Проекты</a>
          <a routerLink="/projects/new" routerLinkActive="active" class="nav-link" (click)="menuOpen.set(false)">Создать проект</a>
        </nav>
        <div class="p-3 small text-white-50 mt-auto border-top border-secondary">Генерация приложений<br><span class="text-white">Angular + Bootstrap</span></div>
      </aside>
      <div class="sb-content">
        <main id="main-content" tabindex="-1" class="container-fluid p-3 p-md-4"><router-outlet /></main>
        <footer class="px-4 py-3 small text-secondary border-top">RWB Studio · Подготовка исходников проекта</footer>
      </div>
    </div>`,
})
export class Layout {
  readonly session = inject(Session);
  private readonly router = inject(Router);
  readonly menuOpen = signal(false);
  constructor() { effect(() => { if (!this.session.state()) void this.router.navigate(['/login']); }); }
  @HostListener('document:keydown.escape') closeMenu() { this.menuOpen.set(false); }
  logout() { this.session.clear(); void this.router.navigate(['/login']); }
}
