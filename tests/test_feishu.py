import unittest

import lark_oapi as lark

from light_claw.communication.feishu import parse_long_connection_message


class FeishuLongConnectionParsingTest(unittest.TestCase):
    def test_parse_long_connection_message_builds_chat_reply_target(self) -> None:
        event = lark.im.v1.P2ImMessageReceiveV1(
            {
                "schema": "2.0",
                "header": {
                    "event_id": "evt_123",
                    "event_type": "im.message.receive_v1",
                    "create_time": "0",
                    "token": "token",
                    "app_id": "cli_test",
                    "tenant_key": "tenant",
                },
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_test"},
                        "sender_type": "user",
                        "tenant_key": "tenant",
                    },
                    "message": {
                        "message_id": "om_123",
                        "chat_id": "oc_123",
                        "chat_type": "group",
                        "message_type": "text",
                        "content": '{"text":"hello"}',
                    },
                },
            }
        )

        inbound = parse_long_connection_message(
            event,
            agent_id="writer",
            bot_app_id="cli_test",
        )

        self.assertIsNotNone(inbound)
        assert inbound is not None
        self.assertEqual(inbound.agent_id, "writer")
        self.assertEqual(inbound.bot_app_id, "cli_test")
        self.assertEqual(inbound.owner_id, "ou_test")
        self.assertEqual(inbound.conversation_id, "feishu:chat:oc_123")
        self.assertEqual(inbound.reply_target.receive_id_type, "chat_id")
        self.assertEqual(inbound.reply_target.receive_id, "oc_123")
        self.assertEqual(inbound.content, "hello")


if __name__ == "__main__":
    unittest.main()
