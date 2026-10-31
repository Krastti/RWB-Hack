"""HTTP rejection accounting and provider format compatibility, using real A-01."""
import asyncio
import json
import httpx
import pytest
from backend.app.agents.llm import LLMAdapter, LLMConfig
from backend.app.agents.llm.adapter import request_payload


@pytest.mark.parametrize('status', [400, 401, 403, 404, 413, 415, 422, 429, 500, 503])
def test_rejected_requests_release_tokens_but_server_failures_remain_unknown(status):
    result = asyncio.run(LLMAdapter(LLMConfig(base_url='https://controlled.invalid/v1', model='test', api_key='test'),
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={'error': {'message': 'rejected'}}))).generate(
            system='system', prompt='prompt', schema={'type': 'object'}))
    assert not result.ok and result.usage.calls == 1
    expected = None if status >= 500 else 0
    assert result.usage.tokens_in == expected and result.usage.tokens_out == expected


def test_json_object_keeps_conditional_schema_validation_locally():
    schema = {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}, 'name': {'type': 'string'}},
              'required': ['enabled'], 'if': {'properties': {'enabled': {'const': True}}},
              'then': {'required': ['name']}}
    seen = []
    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"enabled": true}'}}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    result = asyncio.run(LLMAdapter(LLMConfig(base_url='https://controlled.invalid/v1', model='test', api_key='test', response_format='json_object'),
        transport=httpx.MockTransport(reply)).generate(system='system', prompt='prompt', schema=schema))
    assert seen[0]['response_format'] == {'type': 'json_object'}
    assert '"then"' in seen[0]['messages'][0]['content']
    assert not result.ok and result.usage.tokens_in == 10 and result.usage.tokens_out == 5


def test_json_schema_is_not_duplicated_in_system_prompt():
    config = LLMConfig(base_url='https://controlled.invalid/v1', model='test', api_key='test')
    schema = {'type': 'object', 'description': 'Описание схемы'}
    payload = request_payload(config, 'system', 'prompt', schema)
    assert payload['response_format']['json_schema']['schema'] == schema
    assert 'Описание схемы' not in payload['messages'][0]['content']


def test_thinking_control_is_only_sent_when_explicitly_configured():
    common = dict(base_url='https://controlled.invalid/v1', model='test', api_key='test')
    assert 'chat_template_kwargs' not in request_payload(LLMConfig(**common), 's', 'p', {})
    assert request_payload(LLMConfig(**common, enable_thinking=False), 's', 'p', {})['chat_template_kwargs'] == {'enable_thinking': False}


@pytest.mark.parametrize('status', [400, 429, 500])
def test_explicit_error_usage_is_never_replaced_with_zero(status):
    result = asyncio.run(LLMAdapter(LLMConfig(base_url='https://controlled.invalid/v1', model='test', api_key='test'),
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={'error': {'message': 'failed'},
            'usage': {'prompt_tokens': 120, 'completion_tokens': 10, 'total_tokens': 130}}))).generate(
                system='s', prompt='p', schema={'type': 'object'}))
    assert result.usage.tokens_in == 120 and result.usage.tokens_out == 10


def test_compat_decoder_keeps_full_contract_validation():
    schema = {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}, 'name': {'type': 'string'}},
              'required': ['enabled'], 'additionalProperties': False,
              'if': {'properties': {'enabled': {'const': True}}}, 'then': {'required': ['name']}}
    seen = []
    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"enabled":true}'}}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    result = asyncio.run(LLMAdapter(LLMConfig(base_url='https://controlled.invalid/v1', model='test', api_key='test', response_format='json_schema_compat'),
        transport=httpx.MockTransport(reply)).generate(system='s', prompt='p', schema=schema))
    wire = seen[0]['response_format']['json_schema']['schema']
    assert 'if' not in wire and 'then' not in wire
    assert 'if' in schema and wire['additionalProperties'] is False
    assert not result.ok and result.candidate == {'enabled': True}


def test_decoder_regex_preserves_json_schema_search_semantics():
    import re
    from backend.app.agents.llm.adapter import provider_shape
    wire = provider_shape({'type': 'string', 'pattern': r'\S'})
    assert re.fullmatch(wire['pattern'], 'A complete source quote with spaces.')
    assert not re.fullmatch(wire['pattern'], '   ')


def test_decoder_literal_enums_avoid_provider_regex_intersection_bug():
    from backend.app.agents.llm.adapter import provider_shape
    original = {'type': 'object', 'properties': {'text': {'type': 'string', 'pattern': r'\S'}},
                'enum': [{'text': 'An exact accepted source.'}]}
    assert provider_shape(original) == {'enum': original['enum']}
    assert 'properties' in original
