from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from ..config import get_settings


class LLMService:
    def __init__(self) -> None:
        settings = get_settings()
        self.chat_model = settings.chat_model
        self.chat_fallback_model = settings.chat_fallback_model
        self.embedding_model = settings.embedding_model
        self._client = (
            OpenAI(api_key=settings.glm_api_key, base_url=settings.glm_base_url)
            if settings.glm_api_key
            else None
        )

    def is_ready(self) -> bool:
        return self._client is not None

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        if not self._client:
            raise RuntimeError("GLM_API_KEY is not configured")

        history = history or []

        input_items: list[dict[str, Any]] = []

        if system_prompt.strip():
            input_items.append({
                "role": "system",
                "content": system_prompt.strip(),
            })

        for msg in history:
            role = (msg.get("role", "") or "").strip()
            content = (msg.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue

            input_items.append({
                "role": role,
                "content": content,
            })

        input_items.append({
            "role": "user",
            "content": user_prompt.strip(),
        })

        models_to_try = [self.chat_model]
        if (
            self.chat_fallback_model
            and self.chat_fallback_model != self.chat_model
        ):
            models_to_try.append(self.chat_fallback_model)

        last_rate_limit: Exception | None = None
        for idx, model_name in enumerate(models_to_try):
            try:
                response = self._client.chat.completions.create(
                    model=model_name,
                    messages=input_items,
                )
                text = self._extract_chat_text(response)
                if not text:
                    raise RuntimeError("GLM returned empty output.")
                return text, model_name
            except RateLimitError as exc:
                quota_code = self._extract_rate_limit_code(exc)
                if quota_code == "insufficient_quota":
                    raise RuntimeError(
                        "GLM insufficient quota. Please check billing/quota or use another API key."
                    ) from exc
                last_rate_limit = exc
                if idx < len(models_to_try) - 1:
                    continue
                break
            except AuthenticationError as exc:
                raise RuntimeError("GLM auth failed. Check GLM_API_KEY.") from exc
            except (APITimeoutError, APIConnectionError) as exc:
                raise RuntimeError("GLM network timeout/connection error.") from exc
            except APIError as exc:
                raise RuntimeError(f"GLM API error: {exc}") from exc

        if len(models_to_try) > 1:
            raise RuntimeError(
                "GLM rate limit reached on both primary and fallback models. Retry later."
            ) from last_rate_limit
        raise RuntimeError("GLM rate limit reached. Retry later.") from last_rate_limit

    def _extract_chat_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if not message:
            return ""
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"text", "output_text"} and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(parts).strip()
        return ""

    def _extract_rate_limit_code(self, exc: RateLimitError) -> str | None:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            code = body.get("code")
            if not code and isinstance(body.get("error"), dict):
                code = body["error"].get("code")
            if isinstance(code, str):
                return code
        return None
