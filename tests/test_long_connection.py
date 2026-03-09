from opencode_bot.feishu_long_connection import _normalize_command_text, _to_dict
from opencode_bot.feishu_long_connection import FeishuLongConnectionRunner


class Node:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_to_dict_with_nested_objects():
    event = Node(
        action=Node(value=Node(action="bind_session", session_id="ses_x")),
        context=Node(open_chat_id="oc_x"),
        operator=Node(operator_id=Node(open_id="ou_x")),
    )

    data = _to_dict(event)
    assert data["action"]["value"]["action"] == "bind_session"
    assert data["action"]["value"]["session_id"] == "ses_x"
    assert data["context"]["open_chat_id"] == "oc_x"


def test_normalize_command_text_removes_at_tag():
    raw = '<at user_id="ou_x"></at>  /sessions'
    assert _normalize_command_text(raw) == "/sessions"


class _FakeBuilder:
    def __init__(self):
        self.called = []

    def register_p2_im_message_message_read_v1(self, handler):
        self.called.append(("message_read", handler))
        return self

    def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, handler):
        self.called.append(("bot_entered", handler))
        return self


def test_register_optional_event_processors_when_builder_supports_methods():
    runner = FeishuLongConnectionRunner.__new__(FeishuLongConnectionRunner)
    builder = _FakeBuilder()

    output = runner._register_optional_event_processors(builder)
    assert output is builder
    assert [item[0] for item in builder.called] == ["message_read", "bot_entered"]


def test_register_optional_event_processors_when_builder_lacks_methods():
    runner = FeishuLongConnectionRunner.__new__(FeishuLongConnectionRunner)

    class _MinimalBuilder:
        pass

    builder = _MinimalBuilder()
    output = runner._register_optional_event_processors(builder)
    assert output is builder
