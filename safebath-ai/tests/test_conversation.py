import asyncio

from backend.conversation import ConversationController, MAX_TURNS


class StubProvider:
    def __init__(self):
        self.calls = []

    async def respond(self, history):
        self.calls.append(history)
        return "Are you okay?"


def test_conversation_keeps_bounded_context_and_can_end():
    provider = StubProvider()
    controller = ConversationController(provider)
    for i in range(MAX_TURNS + 3):
        asyncio.run(controller.say("test-session", f"answer {i}"))
    assert len(controller.sessions["test-session"].history) <= MAX_TURNS * 2
    assert controller.end("test-session") is True
    assert controller.end("test-session") is False


def test_conversation_passes_only_current_bounded_history():
    provider = StubProvider()
    controller = ConversationController(provider)
    asyncio.run(controller.say("s1", "help"))
    assert provider.calls[0] == [{"role": "user", "text": "help"}]
