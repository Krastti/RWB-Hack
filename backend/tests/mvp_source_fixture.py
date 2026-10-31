"""Explicit provider responses for integration tests; never selected in production."""
FILES = {
'backend/app/schemas/dto.py': '''from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
Status = Literal["new", "in_progress", "done"]
class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, pattern=r"\\S")
class TicketStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
class Ticket(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    description: str
    status: Status
''',
'backend/app/services/domain.py': '''from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from app.models.entities import Tickets, Employees
def require(actor, role):
    if role not in actor.roles:
        raise HTTPException(403, "Forbidden")
def subject(actor):
    try:
        return UUID(actor.subject)
    except ValueError:
        raise HTTPException(401, "Invalid subject") from None
def create_ticket(session, actor, data):
    require(actor, "ROLE-001")
    owner = subject(actor)
    with session.begin():
        from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(Employees).values(id=owner).on_conflict_do_nothing())
        ticket = Tickets(owner_id=owner, description=data.description, status="new")
        session.add(ticket)
        session.flush()
    return ticket
def list_own(session, actor):
    require(actor, "ROLE-001")
    return session.scalars(select(Tickets).where(Tickets.owner_id == subject(actor))).all()
def list_all(session, actor):
    require(actor, "ROLE-002")
    return session.scalars(select(Tickets)).all()
def change_status(session, actor, identity, data):
    require(actor, "ROLE-002")
    with session.begin():
        ticket = session.get(Tickets, identity, with_for_update=True)
        if ticket is None:
            raise HTTPException(404, "Ticket not found")
        ticket.status = data.status
    return ticket
''',
'backend/app/api/router.py': '''from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.security import Actor, current_actor
from app.db.session import get_session
from app.schemas.dto import Ticket, TicketCreate, TicketStatusUpdate
from app.services import domain
router = APIRouter()
@router.post("/tickets", operation_id="createTicket", status_code=201, response_model=Ticket)
def create(data: TicketCreate, actor: Annotated[Actor, Depends(current_actor)], session: Annotated[Session, Depends(get_session)]):
    return domain.create_ticket(session, actor, data)
@router.get("/tickets", operation_id="listOwnTickets", response_model=list[Ticket])
def own(actor: Annotated[Actor, Depends(current_actor)], session: Annotated[Session, Depends(get_session)]):
    return domain.list_own(session, actor)
@router.get("/dispatch/tickets", operation_id="listTickets", response_model=list[Ticket])
def all_tickets(actor: Annotated[Actor, Depends(current_actor)], session: Annotated[Session, Depends(get_session)]):
    return domain.list_all(session, actor)
@router.patch("/tickets/{id}/status", operation_id="changeTicketStatus", response_model=Ticket)
def update(id: UUID, data: TicketStatusUpdate, actor: Annotated[Actor, Depends(current_actor)], session: Annotated[Session, Depends(get_session)]):
    return domain.change_status(session, actor, id, data)
''',
'backend/app/main.py': '''from fastapi import FastAPI
from app.api.router import router
app = FastAPI(title="Repair tickets", version="1")
app.include_router(router, prefix="/api/v1")
''',
'frontend/src/app/api/client.ts': '''import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
export type Status = 'new' | 'in_progress' | 'done';
export interface Ticket { id: string; owner_id: string; description: string; status: Status; }
@Injectable({providedIn: 'root'})
export class Api {
  private http = inject(HttpClient);
  token = signal('');
  private options() { return {headers: new HttpHeaders({Authorization: 'Bearer ' + this.token()})}; }
  list(dispatch: boolean) { return this.http.get<Ticket[]>('/api/v1/' + (dispatch ? 'dispatch/' : '') + 'tickets', this.options()); }
  create(description: string) { return this.http.post<Ticket>('/api/v1/tickets', {description}, this.options()); }
  update(id: string, status: Status) { return this.http.patch<Ticket>('/api/v1/tickets/' + encodeURIComponent(id) + '/status', {status}, this.options()); }
}
''',
'frontend/src/app/screens.ts': '''import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { finalize } from 'rxjs';
import { Api, Ticket, Status } from './api/client';
@Component({standalone:true, imports:[FormsModule], template: `
<h1>{{dispatch ? 'Обработка заявок' : 'Мои заявки'}}</h1>
<button class="btn btn-outline-primary" (click)="load()" [disabled]="busy()">Обновить</button>
@if (busy()) {<p role="status">Загрузка…</p>}
@if (error()) {<p role="alert" class="alert alert-danger">{{error()}}</p>}
@if (success()) {<p role="status" class="alert alert-success">{{success()}}</p>}
@if (!dispatch) {<form (ngSubmit)="create()" class="my-3">
<label for="description">Описание неисправности</label><textarea id="description" class="form-control" name="description" [(ngModel)]="description" required></textarea>
<button class="btn btn-primary mt-2" [disabled]="busy() || !description.trim()">Создать</button></form>}
@if (!busy() && !items().length) {<p>Заявок пока нет.</p>}
@for (ticket of items(); track ticket.id) {<article class="card my-3"><div class="card-body">
<p>{{ticket.description}}</p><p>Статус: {{ticket.status}}</p>
@if (dispatch) {<label>Новый статус<select class="form-select" [ngModel]="ticket.status" (ngModelChange)="update(ticket, $event)" [disabled]="busy()"><option value="new">Новая</option><option value="in_progress">В работе</option><option value="done">Готово</option></select></label>}
</div></article>}`})
export class TicketsScreen {
  private api = inject(Api);
  dispatch = inject(ActivatedRoute).snapshot.data['dispatch'] === true;
  items = signal<Ticket[]>([]); busy = signal(false); error = signal(''); success = signal(''); description = '';
  private fail(e: {status:number}) { this.error.set(e.status === 403 ? 'Доступ запрещён' : e.status === 401 ? 'Нужен действующий токен' : 'Не удалось выполнить запрос'); }
  load() { if(this.busy()) return; this.busy.set(true); this.error.set(''); this.api.list(this.dispatch).pipe(finalize(()=>this.busy.set(false))).subscribe({next: v=>this.items.set(v),error:e=>this.fail(e)}); }
  create() { if(this.busy() || !this.description.trim()) return; this.busy.set(true); this.error.set(''); this.api.create(this.description).pipe(finalize(()=>this.busy.set(false))).subscribe({next:v=>{this.items.update(xs=>[v,...xs]);this.description='';this.success.set('Заявка создана');},error:e=>this.fail(e)}); }
  update(ticket:Ticket,status:Status) { if(this.busy()) return; this.busy.set(true); this.error.set(''); this.api.update(ticket.id,status).pipe(finalize(()=>this.busy.set(false))).subscribe({next:v=>{this.items.update(xs=>xs.map(x=>x.id===v.id?v:x));this.success.set('Статус обновлён');},error:e=>this.fail(e)}); }
}
''',
'frontend/src/app/app.routes.ts': '''import { Routes } from '@angular/router';
import { TicketsScreen } from './screens';
export const routes: Routes = [{path:'tickets',component:TicketsScreen},{path:'dispatch/tickets',component:TicketsScreen,data:{dispatch:true}},{path:'',pathMatch:'full',redirectTo:'tickets'}];
''',
'frontend/src/app/app.ts': '''import { Component, inject } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { Api } from './api/client';
@Component({selector:'app-root',standalone:true,imports:[RouterLink,RouterOutlet,FormsModule],template:`<main>
<nav class="d-flex gap-3 mb-4"><a routerLink="/tickets">Мои заявки</a><a routerLink="/dispatch/tickets">Диспетчер</a></nav>
<label for="token">Токен от доверенного поставщика</label><input id="token" class="form-control mb-4" type="password" autocomplete="off" [ngModel]="api.token()" (ngModelChange)="api.token.set($event)">
<router-outlet /></main>`})
export class App { api = inject(Api); }
''',
}
