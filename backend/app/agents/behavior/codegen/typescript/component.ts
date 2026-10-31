// A-11 supplies the accepted API gateway and places this component at UI_BINDINGS.
import { ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, SimpleChanges, inject } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Actor, Values, Scalar, decide, explanation, parametersFor, fieldValid } from './policy';
export interface DomainGateway {
  submit(operationId:string,recordId:string,parameters:Values):Promise<void>;
  refresh(recordId:string):Promise<Values>;
}
@Component({
  selector:'app-domain-action', standalone:true, imports:[ReactiveFormsModule],
  template:`
    <form [formGroup]="form" (ngSubmit)="submit()">
      @for (field of fields; track field.id) {
        <label class="form-label">{{ field.id }}
          @if (field.type.kind === 'boolean') {
            <input class="form-check-input" type="checkbox" [formControlName]="field.id">
          } @else if (field.type.kind === 'integer') {
            <input class="form-control" type="number" [formControlName]="field.id">
          } @else {
            <select class="form-select" [formControlName]="field.id">
              @for (value of field.type.values; track value) { <option [ngValue]="value">{{ value }}</option> }
            </select>
          }
        </label>
      }
      <button class="btn btn-primary" type="submit" [disabled]="busy || form.invalid || decision.status !== 'allowed'">{{ label }}</button>
      <p class="form-text" aria-live="polite">{{ reason }}</p>
      @if (error) { <p class="alert alert-warning" role="alert">{{ error }}</p> }
    </form>`
})
export class DomainActionComponent implements OnChanges, OnDestroy {
  @Input({required:true}) operationId='';
  @Input({required:true}) recordId='';
  @Input({required:true}) record:Values={};
  @Input({required:true}) actor:Actor={};
  @Input({required:true}) gateway!:DomainGateway;
  @Input() label='Выполнить';
  form=new FormGroup<Record<string,FormControl<Scalar|null>>>({});
  busy=false;
  error='';
  private preparedOperation='';
  private fresh=true;
  private generation=0;
  private destroyed=false;
  private readonly changeDetector=inject(ChangeDetectorRef);
  get fields() { return parametersFor(this.operationId); }
  get decision() {
    const decision=decide(this.operationId,this.record,this.actor,this.form.getRawValue());
    return this.fresh?decision:{...decision,status:'unknown' as const,reason:'REFRESH_REQUIRED'};
  }
  get reason() { return explanation(this.decision); }
  ngOnChanges(changes:SimpleChanges):void {
    const contextChanged=['operationId','recordId','gateway','actor'].some(key=>key in changes);
    const recordChanged='record' in changes && changes['record'].previousValue!==changes['record'].currentValue;
    if(contextChanged || recordChanged) {
      this.generation++;
      this.busy=false;this.error='';
      this.fresh=recordChanged;
    }
    if(this.preparedOperation!==this.operationId) {
      const controls:Record<string,FormControl<Scalar|null>>={};
      for(const field of this.fields) controls[field.id]=new FormControl<Scalar|null>(null,
        control=>fieldValid(field,control.value)?null:{behaviorConstraint:true});
      this.form=new FormGroup(controls);this.preparedOperation=this.operationId;
    }
  }
  async submit():Promise<void> {
    if(this.destroyed || this.busy || this.form.invalid || this.decision.status!=='allowed') return;
    this.busy=true;this.error='';
    this.changeDetector.markForCheck();
    const recordId=this.recordId, operationId=this.operationId, gateway=this.gateway;
    const generation=++this.generation;
    const current=()=>!this.destroyed && this.generation===generation && this.recordId===recordId
      && this.operationId===operationId && this.gateway===gateway;
    try {
      await gateway.submit(operationId,recordId,this.form.getRawValue());
    } catch {
      if(current()) {
        this.error='Сервер отклонил действие или данные устарели. Обновите данные и проверьте доступность.';
        this.changeDetector.markForCheck();
      }
    }
    if(!current()) return;
    try {
      const record=await gateway.refresh(recordId);
      if(current()) { this.record=record;this.fresh=true; }
    } catch {
      if(current()) { this.fresh=false;this.error='Не удалось обновить данные. Повторите загрузку.'; }
    } finally {
      if(current()) { this.busy=false;this.changeDetector.markForCheck(); }
    }
  }
  ngOnDestroy():void { this.destroyed=true;this.generation++; }
}
