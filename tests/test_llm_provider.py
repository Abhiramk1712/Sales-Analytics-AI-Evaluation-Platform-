"""
tests/test_llm_provider.py
===========================
backend/llm/provider.py had no test file at all — the only coverage anywhere
in the suite was tests/test_api_routes.py monkeypatching get_llm_provider with
a stub, which exercises the agent route's fallback behaviour and nothing about
the providers themselves. That's how AnthropicProvider shipped as a documented
LLM_PROVIDER=anthropic option (.env.example lists ANTHROPIC_API_KEY, provider
is exported from backend/llm/__init__.py) while chat_completion and
stream_complete both unconditionally raised NotImplementedError. See #21.

These tests fake the underlying SDK client after construction (client
construction itself makes no network call — confirmed directly against both
installed SDKs) so the provider's own request-shaping and response-parsing
logic runs for real, without hitting the network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.llm.provider import AnthropicProvider, OpenAIProvider, get_llm_provider


# ── validate_config ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider_cls, env_name", [
    (OpenAIProvider, "OPENAI_API_KEY"),
    (AnthropicProvider, "ANTHROPIC_API_KEY"),
])
@pytest.mark.parametrize("bad_key", ["", "   ", None])
def test_validate_config_rejects_missing_key(provider_cls, env_name, bad_key):
    with pytest.raises(ValueError, match=env_name):
        provider_cls(api_key=bad_key)


# ── factory dispatch ─────────────────────────────────────────────────────────

def test_get_llm_provider_dispatches_openai():
    provider = get_llm_provider(provider_name="openai", openai_key="sk-test")
    assert isinstance(provider, OpenAIProvider)


def test_get_llm_provider_dispatches_anthropic():
    provider = get_llm_provider(provider_name="anthropic", anthropic_key="sk-ant-test")
    assert isinstance(provider, AnthropicProvider)


def test_get_llm_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_llm_provider(provider_name="cohere", openai_key="sk-test")


# ── OpenAIProvider ────────────────────────────────────────────────────────────

class _FakeOpenAIChoiceMessage:
    def __init__(self, content):
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content=None, delta_content=None):
        if content is not None:
            self.message = _FakeOpenAIChoiceMessage(content)
        if delta_content is not None:
            self.delta = SimpleNamespace(content=delta_content)


class _FakeOpenAIChatCompletions:
    def __init__(self, reply_text=None, stream_chunks=None):
        self._reply_text = reply_text
        self._stream_chunks = stream_chunks or []
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if kwargs.get("stream"):
            return self._achunks()
        return SimpleNamespace(choices=[_FakeOpenAIChoice(content=self._reply_text)])

    async def _achunks(self):
        for text in self._stream_chunks:
            yield SimpleNamespace(choices=[_FakeOpenAIChoice(delta_content=text)])


def _openai_provider_with_fake_client(reply_text=None, stream_chunks=None):
    provider = OpenAIProvider(api_key="sk-test")
    fake_completions = _FakeOpenAIChatCompletions(reply_text, stream_chunks)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
    return provider, fake_completions


@pytest.mark.asyncio
async def test_openai_chat_completion_returns_message_text():
    provider, fake = _openai_provider_with_fake_client(reply_text="hello from gpt")
    result = await provider.chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="be nice",
    )
    assert result == "hello from gpt"
    assert fake.last_kwargs["messages"][0] == {"role": "system", "content": "be nice"}
    assert fake.last_kwargs["messages"][1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_openai_stream_complete_yields_each_delta():
    provider, fake = _openai_provider_with_fake_client(stream_chunks=["Hel", "lo", "!"])
    tokens = [t async for t in provider.stream_complete(messages=[{"role": "user", "content": "hi"}])]
    assert tokens == ["Hel", "lo", "!"]
    assert fake.last_kwargs["stream"] is True


# ── AnthropicProvider ─────────────────────────────────────────────────────────

class _FakeAnthropicStreamCtx:
    def __init__(self, tokens):
        self.text_stream = self._agen(tokens)

    async def _agen(self, tokens):
        for t in tokens:
            yield t

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAnthropicMessages:
    def __init__(self, content_text=None, stream_tokens=None):
        self._content_text = content_text
        self._stream_tokens = stream_tokens or []
        self.create_kwargs = None
        self.stream_kwargs = None

    async def create(self, **kwargs):
        self.create_kwargs = kwargs
        block = SimpleNamespace(type="text", text=self._content_text)
        return SimpleNamespace(content=[block])

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return _FakeAnthropicStreamCtx(self._stream_tokens)


def _anthropic_provider_with_fake_client(content_text=None, stream_tokens=None):
    provider = AnthropicProvider(api_key="sk-ant-test")
    fake_messages = _FakeAnthropicMessages(content_text, stream_tokens)
    provider.client = SimpleNamespace(messages=fake_messages)
    return provider, fake_messages


@pytest.mark.asyncio
async def test_anthropic_chat_completion_returns_text_block_content():
    provider, fake = _anthropic_provider_with_fake_client(content_text="hello from claude")
    result = await provider.chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="be nice",
        max_tokens=500,
    )
    assert result == "hello from claude"
    assert fake.create_kwargs["system"] == "be nice"
    assert fake.create_kwargs["max_tokens"] == 500
    assert fake.create_kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_anthropic_chat_completion_defaults_max_tokens_when_none():
    provider, fake = _anthropic_provider_with_fake_client(content_text="ok")
    await provider.chat_completion(messages=[{"role": "user", "content": "hi"}])
    assert fake.create_kwargs["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_anthropic_stream_complete_yields_each_text_delta():
    provider, fake = _anthropic_provider_with_fake_client(stream_tokens=["Cl", "au", "de"])
    tokens = [t async for t in provider.stream_complete(messages=[{"role": "user", "content": "hi"}])]
    assert tokens == ["Cl", "au", "de"]
    assert "".join(tokens) == "Claude"
    assert fake.stream_kwargs["model"] == AnthropicProvider.DEFAULT_MODEL
