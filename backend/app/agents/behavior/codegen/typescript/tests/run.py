"""Compile emitted Angular source and run its zoneless component regressions."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from ....fixtures import bound_example
from ....profile import behavior_profile
from ..emitter import emit

ROOT = Path(__file__).resolve().parents[7]


def main():
    node = os.environ.get('NODE_BINARY', 'node')
    modules = Path(os.environ.get('ANGULAR_NODE_MODULES', ROOT / 'frontend/node_modules')).resolve()
    _, model = bound_example()
    catalog = {'contract_version': 'demo-v1', 'entities': {'Ticket': ['owner_id', 'status', 'priority']},
               'operations': {'cancelTicket': 'Ticket'}, 'screens': {'ticket_detail': {'cancel_button': 'cancelTicket'}}}
    files = emit(model, catalog=catalog, profile=behavior_profile())
    with TemporaryDirectory() as directory:
        target = Path(directory)
        (target / 'node_modules').symlink_to(modules, target_is_directory=True)
        (target / 'package.json').write_text('{"type":"module"}')
        for path, content in files.items():
            (target / Path(path).name).write_text(content)
        config = json.loads((ROOT / 'frontend/tsconfig.json').read_text())
        config['compilerOptions'].update(outDir='out', types=[])
        config['files'] = ['policy.ts', 'domain-action.ts', 'ui-bindings.ts']
        (target / 'tsconfig.json').write_text(json.dumps(config))
        subprocess.run([node, str(modules / '@angular/compiler-cli/bundles/src/bin/ngc.js'),
                        '-p', str(target / 'tsconfig.json')], check=True)
        component = target / 'out/domain-action.js'
        component.write_text(component.read_text().replace("from './policy'", "from './policy.js'"))
        (target / 'component.test.mjs').write_text(Path(__file__).with_name('component.test.mjs').read_text())
        subprocess.run([node, '--test', str(target / 'component.test.mjs')], check=True)


if __name__ == '__main__':
    main()
