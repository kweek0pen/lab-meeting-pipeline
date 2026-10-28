"""회의록 생성용 LLM 호출. 제공자(Anthropic / OpenAI)는 설정으로 전환한다.

각 제공자는 '스키마에 맞는 JSON 문자열'만 돌려주고, 검증·재시도는 minutes.py 가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    model: str

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult: ...


class AnthropicProvider:
    MAX_TOKENS = 64000

    def __init__(self, api_key: str, model: str, effort: str = "high", client=None):
        import anthropic

        self.model = model
        self.effort = effort
        # SDK 가 429·5xx·네트워크 오류를 지수형 백오프로 자동 재시도
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=5)

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        import anthropic

        try:
            # 긴 출력이므로 스트리밍으로 받는다 (HTTP 타임아웃 방지).
            # fallbacks="default": 안전 분류기가 요청을 거절하면 서버가 다른 모델로 자동 재시도.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic API 키가 올바르지 않습니다. 설정에서 ANTHROPIC_API_KEY를 확인하세요.") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"Anthropic 계정에 '{self.model}' 사용 권한이 없습니다.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Anthropic 요청 한도 초과. 잠시 후 '이어서 처리'를 누르세요.") from e
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower():
                raise LLMError("Anthropic API 크레딧이 부족합니다. Claude 구독과 API 요금은 별개이므로 "
                               "platform.claude.com 의 Billing 에서 크레딧을 충전하세요.") from e
            raise LLMError(f"Anthropic 요청 오류: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("네트워크 오류로 회의록을 생성하지 못했습니다. 인터넷 연결 후 '이어서 처리'를 누르세요.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic 서버 오류({e.status_code}). 잠시 후 다시 시도하세요.") from e

        if message.stop_reason == "refusal":
            raise LLMError("모델이 이 요청 처리를 거절했습니다. 녹취록은 저장되어 있습니다.")
        # max_tokens 로 잘린 JSON 은 minutes.py 의 검증에서 실패 → 제한적으로 재시도
        text = "".join(block.text for block in message.content if block.type == "text")
        return LLMResult(text=text, model=message.model or self.model,
                         input_tokens=message.usage.input_tokens or 0,
                         output_tokens=message.usage.output_tokens or 0)


class OpenAIProvider:
    def __init__(self, api_key: str, model: str, effort: str = "high", client=None):
        from openai import OpenAI

        self.model = model
        self.effort = effort
        self.client = client or OpenAI(api_key=api_key, max_retries=5, timeout=1800)

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        import openai

        try:
            response = self.client.responses.parse(
                model=self.model, instructions=system, input=user, text_format=schema,
            )
        except openai.AuthenticationError as e:
            raise LLMError("OpenAI API 키가 올바르지 않습니다.") from e
        except openai.RateLimitError as e:
            if "quota" in str(e).lower():
                raise LLMError("OpenAI API 크레딧이 없습니다. platform.openai.com Billing 에서 등록하세요.") from e
            raise LLMError("OpenAI 요청 한도 초과. 잠시 후 다시 시도하세요.") from e
        except openai.APIConnectionError as e:
            raise LLMError("네트워크 오류로 회의록을 생성하지 못했습니다.") from e
        except openai.APIStatusError as e:
            raise LLMError(f"OpenAI 요청 오류({e.status_code}): {e.message}") from e
        except Exception as e:  # SDK 내부 파싱 실패 등 → 재시도 대상
            raise LLMError(f"응답 처리 실패: {e}") from e
        usage = response.usage
        return LLMResult(text=response.output_text or "", model=self.model,
                         input_tokens=getattr(usage, "input_tokens", 0) or 0,
                         output_tokens=getattr(usage, "output_tokens", 0) or 0)


def make_provider(settings) -> LLMProvider:
    if settings.summary_provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.summary_model, settings.summary_effort)
    return AnthropicProvider(settings.anthropic_api_key, settings.summary_model, settings.summary_effort)
