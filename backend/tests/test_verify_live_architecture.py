"""Live verification must refuse a failed Run even with accepted old stages."""
import sys

import httpx
import pytest

from backend.tools import verify_live_architecture as verifier


@pytest.mark.parametrize('status', ['failed', 'budget_exceeded', 'cancelling', 'cancelled', 'waiting_for_input'])
def test_terminal_run_cannot_be_reported_as_success(monkeypatch, status):
    requested = []

    def reply(request):
        requested.append(request.url.path)
        if request.url.path.endswith('/auth/token'):
            return httpx.Response(200, json={'access_token': 'test-token'})
        assert request.url.path == '/api/v1/runs/test-run', 'Verifier must stop before reading accepted artifacts'
        return httpx.Response(200, json={'status': status, 'stages': [
            {'stage_id': 'architecture', 'attempt_no': 1, 'status': 'succeeded'},
        ]})

    client = httpx.Client(base_url='https://controlled.invalid/api/v1', transport=httpx.MockTransport(reply))
    monkeypatch.setattr(verifier.httpx, 'Client', lambda **kwargs: client)
    monkeypatch.setattr(verifier, 'load_dotenv', lambda *args, **kwargs: None)
    monkeypatch.setenv('PANEL_LOGIN', 'test')
    monkeypatch.setenv('PANEL_PASSWORD', 'test')
    monkeypatch.setattr(sys, 'argv', ['verify_live_architecture', 'test-run', '--wait-seconds', '0'])
    with pytest.raises(SystemExit, match='Run requires intervention'):
        verifier.main()
    assert len(requested) == 2
