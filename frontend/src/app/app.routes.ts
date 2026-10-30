import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
import { Session } from './auth/session';
import { Layout } from './layout/layout';

const authenticated: CanActivateFn = () => inject(Session).token() ? true : inject(Router).createUrlTree(['/login']);
export const routes: Routes = [
  { path: 'login', loadComponent: () => import('./auth/login').then(m => m.Login) },
  { path: '', component: Layout, canActivate: [authenticated], canActivateChild: [authenticated], children: [
    { path: '', pathMatch: 'full', redirectTo: 'projects' },
    { path: 'projects', loadComponent: () => import('./projects/list').then(m => m.ProjectList) },
    { path: 'projects/new', loadComponent: () => import('./projects/create').then(m => m.ProjectCreate) },
    { path: 'projects/:id', loadComponent: () => import('./projects/detail').then(m => m.ProjectDetail) },
    { path: 'runs/:id', loadComponent: () => import('./runs/detail').then(m => m.RunDetail) },
  ] },
  { path: '**', loadComponent: () => import('./shared/not-found').then(m => m.NotFound) },
];
