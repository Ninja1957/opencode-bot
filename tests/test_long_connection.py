from opencode_bot.feishu_long_connection import _normalize_command_text, _to_dict


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
