"""Read-only HTTP proof for a real Run; never invokes agents or seeds results."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import time

import httpx
from dotenv import load_dotenv

REQUIRED = {'requirements.json', 'questions.json', 'docs/system-analysis.md', 'architecture.json',
            'contracts/openapi.yaml', 'data-model.json', 'ui-spec.json', 'generation-plan.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/api/v1')
    parser.add_argument('--wait-seconds', type=int, default=900)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    with httpx.Client(base_url=args.base_url.rstrip('/'), timeout=30) as client:
        token = client.post('/auth/token', json={'login': os.environ['PANEL_LOGIN'], 'password': os.environ['PANEL_PASSWORD']})
        token.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + token.json()['access_token']
        deadline, previous = time.monotonic() + args.wait_seconds, None
        while True:
            response = client.get('/runs/' + args.run_id)
            response.raise_for_status()
            run = response.json()
            state = [(a['stage_id'], a['attempt_no'], a['status'], a.get('reason')) for a in run['stages']]
            if state != previous:
                print(json.dumps({'status': run['status'], 'stages': state}, ensure_ascii=False), flush=True)
                previous = state
            architecture = [a for a in run['stages'] if a['stage_id'] == 'architecture']
            if architecture and max(architecture, key=lambda a: a['attempt_no'])['status'] == 'succeeded':
                break
            if run['status'] in {'failed', 'budget_exceeded', 'cancelled', 'waiting_for_input'}:
                raise SystemExit('Run requires intervention; no successful architecture claim')
            if time.monotonic() >= deadline:
                raise SystemExit('Architecture is not accepted within the wait limit')
            time.sleep(2)
        response = client.get('/runs/' + args.run_id + '/accepted-artifacts')
        response.raise_for_status()
        items = {item['path']: item for item in response.json()['items']}
        if not REQUIRED <= items.keys():
            raise SystemExit('Required accepted artifacts are missing')
        for path in sorted(REQUIRED):
            item = items[path]
            response = client.get('/runs/' + args.run_id + '/artifacts/' + item['artifact_id'])
            response.raise_for_status()
            if len(response.content) != item['size_bytes'] or sha256(response.content).hexdigest() != item['checksum']:
                raise SystemExit('Artifact bytes do not match metadata: ' + path)
            if args.output:
                target = args.output / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
            print(json.dumps({'path': path, 'bytes': item['size_bytes'], 'sha256': item['checksum']}), flush=True)
        print(json.dumps({'run_id': args.run_id, 'accepted_artifacts': len(REQUIRED), 'metrics': run['metrics'],
                          'run_status': run['status'], 'verification': 'HTTP bytes and checksums; no build/test claim'}))


if __name__ == '__main__':
    main()
