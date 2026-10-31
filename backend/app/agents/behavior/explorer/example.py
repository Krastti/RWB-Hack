"""Deliberate guard mutation versus an independently specified owner invariant."""
from copy import deepcopy
import json
from ..fixtures import example_model
from ..model import canonical
from ..interpreter import step
from .search import explore,replay


def examples():
    initial={'entity':'ticket','values':{'owner':'alice','status':'draft','priority':1}}
    _,good=example_model()
    _,bad=example_model(missing_guard=True)
    args=dict(max_depth=3,max_states=20,timeout_seconds=2)
    safe=explore(good,initial,**args)
    counterexample=explore(bad,initial,**args)
    assert safe['status']=='no_counterexample_within_bounds',safe
    assert counterexample['status']=='counterexample' and len(counterexample['trace'])==1,counterexample
    assert counterexample['trace'][0]['actor']['id']=='bob'
    replay(bad,counterexample)
    limited=explore(good,initial,max_states=1)
    assert limited['status']=='inconclusive' and limited['reason']=='STATE_LIMIT'
    invalid=deepcopy(good);invalid['rules'][0]['expression']['op']='execute_python'
    unsupported=explore(invalid,initial)
    assert unsupported['status']=='unsupported'
    _,partial=example_model(unresolved=True)
    unknown=explore(partial,initial)
    assert unknown['status']=='inconclusive'
    assert step(good,initial,{'id':'bob','role':'ROLE-001'},{'id':'cancel','parameters':{}})['status']=='denied'
    for owner in [None,'missing']:
        state=deepcopy(initial)
        if owner=='missing': del state['values']['owner']
        else: state['values']['owner']=owner
        assert step(good,state,{'id':'alice','role':'ROLE-001'},{'id':'cancel','parameters':{}})['status']=='unknown'
    assert explore(bad,initial,**args)==counterexample
    return {'fixed':safe,'removed_guard':counterexample,'state_limit':limited,'unknown_operator':unsupported,'partial':unknown}


if __name__=='__main__':
    print(json.dumps(examples(),ensure_ascii=False,indent=2))
