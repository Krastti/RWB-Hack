"""Emitted TS/interpreter conformance using Node's TypeScript support (Node >=24)."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from ...fixtures import bound_example
from ...interpreter import step
from ...profile import behavior_profile
from .emitter import emit,POLICY


def main():
    _,model=bound_example()
    catalog={'contract_version':'demo-v1','entities':{'Ticket':['owner_id','status','priority']},'operations':{'cancelTicket':'Ticket'},'screens':{'ticket_detail':{'cancel_button':'cancelTicket'}}}
    files=emit(model,catalog=catalog,profile=behavior_profile())
    assert files==emit(json.loads(json.dumps(model,sort_keys=True)),catalog=catalog,profile=behavior_profile())
    cases=[({'owner':'alice','status':'draft','priority':p},{'id':actor,'role':'ROLE-001'}) for p,actor in [(0,'alice'),(2,'alice'),(3,'alice'),(True,'alice'),(1,'bob')]]+[
        ({'owner':'alice','status':'cancelled','priority':1},{'id':'alice','role':'ROLE-001'}),
        ({'owner':None,'status':'draft','priority':1},{'id':'alice','role':'ROLE-001'}),
        ({'status':'draft','priority':1},{'id':'alice','role':'ROLE-001'})]
    inputs=[];expected=[]
    for values,actor in cases:
        record={model['bindings']['fields']['ticket.'+k]:v for k,v in values.items()}
        inputs.append([record,actor])
        expected.append(step(model,{'entity':'ticket','values':values},actor,{'id':'cancel','parameters':{}}))
    with TemporaryDirectory() as d:
        p=Path(d);(p/'policy.ts').write_text(files[POLICY]);(p/'package.json').write_text('{"type":"module"}')
        (p/'run.mjs').write_text("import {decide} from './policy.ts';\nconst data="+json.dumps(inputs)+";\nconsole.log(JSON.stringify(data.map(([r,a])=>decide('cancelTicket',r,a,{}))));\n")
        actual=json.loads(subprocess.check_output([os.environ.get('NODE_BINARY','node'),str(p/'run.mjs')],text=True))
        assert actual==expected,(actual,expected)
        # Hide owner in the accepted API/UI projection: no new fetch; decision unknown.
        private=deepcopy(model);private['bindings']['actions']['cancel']['public_fields'].remove('owner')
        (p/'policy.ts').write_text(emit(private,catalog=catalog,profile=behavior_profile())[POLICY])
        actual=json.loads(subprocess.check_output([os.environ.get('NODE_BINARY','node'),str(p/'run.mjs')],text=True))
        assert actual[0]['status']=='unknown'
    print(json.dumps({'conformance_cases':len(cases),'hidden_owner':'unknown','deterministic':True,'angular_app_tested':False}))


if __name__=='__main__': main()
