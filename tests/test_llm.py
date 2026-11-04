from types import SimpleNamespace

from conftest import notes_json

from labmeeting.llm import AnthropicProvider, extract_json
from labmeeting.schema import MeetingNotes


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeAnthropic:
    def __init__(self, text):
        self.kwargs = None
        msg = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                              content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                              usage=SimpleNamespace(input_tokens=10, output_tokens=20))
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self._msg = msg

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        return FakeStream(self._msg)


def test_extract_json_strips_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('여기 결과입니다:\n{"a": {"b": 2}}\n끝') == '{"a": {"b": 2}}'


def test_anthropic_request_puts_schema_in_prompt_not_grammar():
    """API 의 구조화 출력 문법 제한(400 'compiled grammar is too large')을 피하는지 확인."""
    client = FakeAnthropic("```json\n" + notes_json() + "\n```")
    result = AnthropicProvider("k", "claude-opus-5-5", client=client).generate_json("지침", "녹취록", MeetingNotes)
    assert "format" not in client.kwargs["output_config"]
    assert '"action_items"' in client.kwargs["system"]
    MeetingNotes.model_validate_json(result.text)  # 코드 블록이 벗겨져 바로 검증 가능
