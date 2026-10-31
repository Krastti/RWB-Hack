// Pure A-32 semantics. MODEL is supplied by the deterministic compiler.
export type Scalar = string | number | boolean | null;
export type Values = Record<string, Scalar | undefined>;
export interface Actor { id?: string; role?: string; }
export interface State { entity: string; values: Values; }
export interface Action { id: string; parameters: Values; }
export interface ValueType { kind: string; values: string[]; minimum: number | null; maximum: number | null; nullable: boolean; }
export interface Field { id: string; type: ValueType; }
export interface Expr { op: string; value?: Scalar; scope?: string; name?: string; args?: Expr[]; }
export interface Rule { [key:string]:unknown; id: string; kind: string; entity: string; action: string | null; field: string | null; expression: Expr | null; status: string; fr_ids: string[]; }
export interface Model {
  [key:string]:unknown;
  entities: {id: string; fields: Field[]}[];
  actions: {id: string; entity: string; parameters: Field[]; guard_ids: string[]; effect_ids: string[]}[];
  rules: Rule[]; identities: string[]; roles: string[];
  bindings: { [key:string]:unknown; entities: Record<string,string>; fields: Record<string,string>;
    actions: Record<string,{operation_id:string;screen_id:string;element_id:string;public_fields:string[]}> };
}
export interface Decision { status: 'allowed' | 'denied' | 'unknown'; state: State; rule_ids: string[]; fr_ids: string[]; reason: string; }
const MISSING = Symbol('missing');
const UNKNOWN = Symbol('unknown');
type Value = Scalar | typeof MISSING | typeof UNKNOWN;
const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object,key);
export function valid(value: unknown,t:ValueType,identities: string[]): boolean {
  if (value===null) return t.nullable;
  if (t.kind==='boolean') return typeof value==='boolean';
  if (t.kind==='integer') return typeof value==='number' && Number.isSafeInteger(value) && value>=t.minimum! && value<=t.maximum!;
  return typeof value==='string' && (t.kind==='identity'?identities:t.values).includes(value);
}
function evaluate(expr:Expr,env:Record<string,Values>):Value {
  const op=expr.op;
  if(op==='literal') return expr.value!;
  if(op==='ref') {
    const scope=env[expr.scope!] ?? {};
    return own(scope,expr.name!) && scope[expr.name!]!==undefined ? scope[expr.name!]! : MISSING;
  }
  const args=(expr.args ?? []).map(x=>evaluate(x,env));
  if(op==='present') return args[0]!==MISSING && args[0]!==UNKNOWN;
  if(op==='is_null') return args[0]===MISSING || args[0]===UNKNOWN ? UNKNOWN : args[0]===null;
  if(op==='and') return args.some(x=>x===false)?false:args.every(x=>x===true)?true:UNKNOWN;
  if(op==='or') return args.some(x=>x===true)?true:args.every(x=>x===false)?false:UNKNOWN;
  if(op==='not') return typeof args[0]==='boolean'?!args[0]:UNKNOWN;
  if(args.some(x=>x===MISSING || x===UNKNOWN || x===null) || args.length!==2) return UNKNOWN;
  const [a,b]=args;
  if(typeof a!==typeof b) return UNKNOWN;
  if(op==='eq') return a===b;
  if(op==='ne') return a!==b;
  if(typeof a!=='number' || typeof b!=='number') return UNKNOWN;
  if(op==='lt') return a<b;
  if(op==='le') return a<=b;
  if(op==='gt') return a>b;
  if(op==='ge') return a>=b;
  return UNKNOWN;
}
function result(status:Decision['status'],state:State,rules:Rule[]=[],reason=''):Decision {
  return {status,state:{entity:state.entity,values:{...state.values}},rule_ids:[...new Set(rules.map(r=>r.id))].sort(),fr_ids:[...new Set(rules.flatMap(r=>r.fr_ids))].sort(),reason};
}
export function step(state:State,actor:Actor,action:Action):Decision {
  const entity=MODEL.entities.find(e=>e.id===state.entity);
  const a=MODEL.actions.find(a=>a.id===action.id);
  if(!entity || !a || a.entity!==state.entity) return result('unknown',state,[],'UNKNOWN_ACTION_OR_ENTITY');
  if(Object.entries(state.values).some(([k,v])=>{const f=entity.fields.find(f=>f.id===k);return !f || !valid(v,f.type,MODEL.identities);})) return result('unknown',state,[],'INVALID_STATE_VALUE');
  if(Object.entries(action.parameters).some(([k,v])=>{const p=a.parameters.find(p=>p.id===k);return !p || !valid(v,p.type,MODEL.identities);})) return result('unknown',state,[],'INVALID_PARAMETER_VALUE');
  if(Object.keys(actor).some(k=>k!=='id' && k!=='role') || (own(actor,'id') && !MODEL.identities.includes(actor.id!)) || (own(actor,'role') && !MODEL.roles.includes(actor.role!))) return result('unknown',state,[],'INVALID_ACTOR');
  const env:Record<string,Values>={state:state.values,previous:state.values,actor:{...actor},param:action.parameters};
  const applied:Rule[]=[], unknown:Rule[]=[], denied:Rule[]=[];
  for(const id of a.guard_ids) {
    const r=MODEL.rules.find(r=>r.id===id)!;
    const v=r.status==='supported' && r.expression?evaluate(r.expression,env):UNKNOWN;
    if(v===false) denied.push(r); else if(v!==true) unknown.push(r); else applied.push(r);
  }
  if(denied.length) return result('denied',state,denied,'GUARD_DENIED');
  if(unknown.length) return result('unknown',state,unknown,'RULE_UNRESOLVED');
  const updated:Values={...state.values};
  for(const id of a.effect_ids) {
    const r=MODEL.rules.find(r=>r.id===id)!;
    const v=r.status==='supported' && r.expression?evaluate(r.expression,env):UNKNOWN;
    const f=entity.fields.find(f=>f.id===r.field)!;
    if(v===MISSING || v===UNKNOWN || !valid(v,f.type,MODEL.identities)) return result('unknown',state,[r],'EFFECT_UNRESOLVED');
    updated[r.field!]=v; applied.push(r);
  }
  return result('allowed',{entity:state.entity,values:updated},applied,'ALLOWED');
}
export function parametersFor(operationId:string):Field[] {
  const id=Object.keys(MODEL.bindings.actions).find(id=>MODEL.bindings.actions[id].operation_id===operationId);
  return MODEL.actions.find(a=>a.id===id)?.parameters ?? [];
}
export function fieldValid(field:Field,value:unknown):boolean { return valid(value,field.type,MODEL.identities); }
export function decide(operationId:string,record:Values,actor:Actor,parameters:Values):Decision {
  const id=Object.keys(MODEL.bindings.actions).find(id=>MODEL.bindings.actions[id].operation_id===operationId);
  const a=MODEL.actions.find(a=>a.id===id);
  if(!a) return result('unknown',{entity:'',values:{}},[],'UNBOUND_OPERATION');
  const binding=MODEL.bindings.actions[a.id];
  const values:Values={};
  for(const f of MODEL.entities.find(e=>e.id===a.entity)!.fields) {
    const key=MODEL.bindings.fields[a.entity+'.'+f.id];
    // Never request or read fields that the accepted UI/API binding does not expose.
    if(binding.public_fields.includes(f.id) && own(record,key)) values[f.id]=record[key];
  }
  return step({entity:a.entity,values},actor,{id:a.id,parameters});
}
export function explanation(decision:Decision):string {
  if(decision.status==='allowed') return 'Действие доступно; окончательное решение принимает сервер.';
  if(decision.status==='unknown') return 'Недостаточно данных для проверки действия. '+decision.rule_ids.join(', ');
  return 'Действие запрещено правилом: '+decision.rule_ids.join(', ');
}
