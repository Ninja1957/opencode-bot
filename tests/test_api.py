from opencode_bot.api import _extract_card_bind_action


def test_extract_card_bind_action_chat_scope():
    event = {
        "type": "card.action.trigger",
        "action": {
            "value": {
                "action": "bind_session",
                "session_id": "ses_abc",
            }
        },
        "context": {"open_chat_id": "oc_x"},
        "operator": {"operator_id": {"open_id": "ou_x"}},
    }

    result = _extract_card_bind_action(event)
    assert result == ("chat:oc_x", "oc_x", "chat_id", "ses_abc")


def test_extract_card_bind_action_user_scope():
    event = {
        "action": {
            "value": {
                "action": "bind_session",
                "session_id": "ses_abc",
            }
        },
        "operator": {"operator_id": {"open_id": "ou_x"}},
    }

    result = _extract_card_bind_action(event)
    assert result == ("user:ou_x", "ou_x", "open_id", "ses_abc")
