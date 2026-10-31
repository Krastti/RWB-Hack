"""Standalone emitted policy conformance, no database/application is started."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from ...fixtures import bound_example
from ...interpreter import step
from ...model import ModelError
from ...profile import behavior_profile
from .emitter import emit,POLICY,SERVICE


def inputs():
    _,model=bound_example()
    routes={'cancelTicket':{'method':'POST','path':'/tickets/{record_id}/cancel','id_field':'id','id_type':'integer'}}
    catalog={'contract_version':'demo-v1','entities':{'Ticket':['id','owner_id','status','priority']},'operations':{'cancelTicket':'Ticket'},'screens':{'ticket_detail':{'cancel_button':'cancelTicket'}},'routes':routes}
    return model,catalog,routes


def cases():
    return [({'owner':'alice','status':'draft','priority':p},{'id':actor,'role':'ROLE-001'}) for p,actor in [(0,'alice'),(2,'alice'),(3,'alice'),(True,'alice'),(1,'bob')]]+[
        ({'owner':'alice','status':'cancelled','priority':1},{'id':'alice','role':'ROLE-001'}),
        ({'owner':None,'status':'draft','priority':1},{'id':'alice','role':'ROLE-001'}),
        ({'status':'draft','priority':1},{'id':'alice','role':'ROLE-001'})]


def main():
    model,catalog,routes=inputs();profile=behavior_profile()
    files=emit(model,catalog=catalog,routes=routes,profile=profile)
    assert files==emit(deepcopy(model),catalog=deepcopy(catalog),routes=deepcopy(routes),profile=profile)
    with TemporaryDirectory() as d:
        path=Path(d)/'policy.py';path.write_text(files[POLICY])
        spec=importlib.util.spec_from_file_location('emitted_policy',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for values,actor in cases():
            expected=step(model,{'entity':'ticket','values':values},actor,{'id':'cancel','parameters':{}})
            record={model['bindings']['fields']['ticket.'+k]:v for k,v in values.items()}
            actual=module.decide('cancelTicket',record,module.TrustedActor(**actor),{})
            assert actual==expected,(expected,actual)
        try: module.decide('cancelTicket',{}, {'id':'alice','role':'ROLE-001'}, {})
        except TypeError: pass
        else: raise AssertionError('body actor accepted')
    compile(files[SERVICE],SERVICE,'exec')
    bad=deepcopy(model);bad['bindings']=None
    try: emit(bad,catalog=catalog,routes=routes,profile=profile)
    except ModelError: pass
    else: raise AssertionError('missing bindings accepted')
    print(json.dumps({'conformance_cases':len(cases()),'deterministic':True,'files':list(files),'postgresql_tested':False}))


if __name__=='__main__': main()
