"""
backend/llm/provider.py
=======================
LLM Provider Abstraction Layer

Supports pluggable LLM providers (OpenAI, Anthropic, etc.)
Loads provider from LLM_PROVIDER environment variable.
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional
import os


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Execute a chat completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: System message to prepend
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Max output tokens
        
        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    def validate_config(self) -> None:
        """
        Validate that required API keys and configs are present.
        Raise ValueError if configuration is incomplete.
        """
        pass

    @abstractmethod
    async def stream_complete(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream tokens from a chat completion request.

        Yields individual token strings as they arrive.
        The caller is responsible for assembling the full response.
        """
        # This is a generator — must be implemented via `yield`
        # Declare as abstract using a sentinel:
        raise NotImplementedError
        yield  # makes the type-checker treat this as an async generator


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider using the official client."""

    def __init__(self, api_key: str):
        """
        Initialize OpenAI provider.
        
        Args:
            api_key: OpenAI API key
        """
        self.api_key = api_key
        self.validate_config()
        
        # Lazy import to avoid dependency if not using OpenAI
        try:
            import openai
            self.client = openai.AsyncOpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError("openai package required for OpenAI provider. Install with: pip install openai")

    def validate_config(self) -> None:
        """Ensure API key is configured."""
        if not self.api_key or self.api_key.strip() == "":
            raise ValueError(
                "OpenAI API key not configured. "
                "Set OPENAI_API_KEY in .env or environment variables."
            )

    async def chat_completion(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Execute OpenAI chat completion."""
        # Prepend system prompt if provided
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",  # or gpt-4-turbo, gpt-4, etc.
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        return response.choices[0].message.content

    async def stream_complete(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from OpenAI chat completion."""
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        stream = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


class AnthropicProvider(BaseLLMProvider):
    """Anthropic API provider using the official client."""

    #: Kept as a module-level default rather than a config field, matching
    #: how OpenAIProvider pins its model inline below.
    DEFAULT_MODEL = "claude-sonnet-5"

    def __init__(self, api_key: str):
        """
        Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key
        """
        self.api_key = api_key
        self.validate_config()

        # Lazy import to avoid dependency if not using Anthropic
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError("anthropic package required for Anthropic provider. Install with: pip install anthropic")

    def validate_config(self) -> None:
        """Ensure API key is configured."""
        if not self.api_key or self.api_key.strip() == "":
            raise ValueError(
                "Anthropic API key not configured. "
                "Set ANTHROPIC_API_KEY in .env or environment variables."
            )

    async def chat_completion(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Execute Anthropic chat completion."""
        response = await self.client.messages.create(
            model=self.DEFAULT_MODEL,
            max_tokens=max_tokens or 1024,
            system=system_prompt or "",
            messages=messages,
            temperature=temperature,
        )
        return "".join(block.text for block in response.content if block.type == "text")

    async def stream_complete(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from an Anthropic chat completion."""
        async with self.client.messages.stream(
            model=self.DEFAULT_MODEL,
            max_tokens=max_tokens or 1024,
            system=system_prompt or "",
            messages=messages,
            temperature=temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text


def get_llm_provider(
    provider_name: Optional[str] = None,
    openai_key: Optional[str] = None,
    anthropic_key: Optional[str] = None,
) -> BaseLLMProvider:
    """
    Factory function to instantiate the correct LLM provider.
    
    Args:
        provider_name: Provider name ('openai' or 'anthropic'). 
                      If None, reads from LLM_PROVIDER env var.
        openai_key: OpenAI API key. If None, reads from OPENAI_API_KEY env var.
        anthropic_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
    
    Returns:
        Instantiated provider object
    
    Raises:
        ValueError: If provider name is invalid or required config is missing
    """
    # Determine provider name
    if provider_name is None:
        provider_name = os.environ.get("LLM_PROVIDER", "openai").lower()
    else:
        provider_name = provider_name.lower()

    # Get API keys from env if not provided
    if openai_key is None:
        openai_key = os.environ.get("OPENAI_API_KEY", "")
    if anthropic_key is None:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Instantiate provider
    if provider_name == "openai":
        return OpenAIProvider(openai_key)
    elif provider_name == "anthropic":
        return AnthropicProvider(anthropic_key)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. "
            f"Supported providers: 'openai', 'anthropic'"
        )
