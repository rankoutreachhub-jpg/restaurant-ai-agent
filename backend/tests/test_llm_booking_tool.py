"""
Unit tests for llm.py's Gemini function-calling orchestration (Stage
2c): the create_booking tool's declared shape, and the two-call round
trip when the model decides to call it. No real Gemini API calls and no
DB/booking.py involved — book_tool_handler is a plain stub here.

See tests/test_chat_booking_tool.py for the end-to-end behaviour with
the real app/booking.py create_booking() and a real database.
"""

from types import SimpleNamespace

from app import llm


def _fake_response(text=None, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _fake_call(name, args):
    return SimpleNamespace(name=name, args=args)


def test_no_tools_declared_without_a_handler(monkeypatch):
    captured = {}

    def fake_generate_content(model, contents, config):
        captured["config"] = config
        return _fake_response(text="Hiya! Ask away.")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    reply = llm.generate_reply("Hello", [], "RESTAURANT DATA", book_tool_handler=None)

    assert reply == "Hiya! Ask away."
    assert captured["config"].tools is None


def test_booking_tool_is_declared_when_handler_given(monkeypatch):
    captured = {}

    def fake_generate_content(model, contents, config):
        captured["config"] = config
        return _fake_response(text="Sure, happy to help you book.")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    llm.generate_reply(
        "I'd like to book", [], "RESTAURANT DATA", book_tool_handler=lambda args: {}
    )

    tools = captured["config"].tools
    assert tools is not None and len(tools) == 1
    declarations = tools[0].function_declarations
    assert len(declarations) == 1
    declaration = declarations[0]
    assert declaration.name == llm.BOOKING_TOOL_NAME
    assert set(declaration.parameters.required) == {
        "customer_name",
        "phone",
        "email",
        "booking_date",
        "booking_time",
        "party_size",
    }
    # notes is accepted but optional — must not be required.
    assert "notes" in declaration.parameters.properties
    assert "notes" not in declaration.parameters.required


def test_tool_call_invokes_handler_and_relays_final_text(monkeypatch):
    calls = []

    def fake_generate_content(model, contents, config):
        calls.append(contents)
        if len(calls) == 1:
            return _fake_response(
                function_calls=[_fake_call("create_booking", {"customer_name": "Jane"})]
            )
        return _fake_response(text="You're all booked in, Jane!")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    received_args = {}

    def handler(args):
        received_args.update(args)
        return {"status": "confirmed", "booking_id": 1}

    reply = llm.generate_reply("Book me in", [], "RESTAURANT DATA", book_tool_handler=handler)

    assert reply == "You're all booked in, Jane!"
    assert received_args == {"customer_name": "Jane"}
    assert len(calls) == 2
    # The second call must carry the model's function-call turn and the
    # function response, on top of the original single user turn.
    assert len(calls[1]) == 3


def test_tool_rejection_result_is_relayed_not_hidden(monkeypatch):
    calls = []

    def fake_generate_content(model, contents, config):
        calls.append(contents)
        if len(calls) == 1:
            return _fake_response(
                function_calls=[_fake_call("create_booking", {"party_size": 999})]
            )
        return _fake_response(text="Sorry, we're fully booked then.")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    reply = llm.generate_reply(
        "Book a huge party",
        [],
        "RESTAURANT DATA",
        book_tool_handler=lambda args: {"status": "rejected", "reason": "No capacity."},
    )

    assert reply == "Sorry, we're fully booked then."


def test_unrecognised_tool_name_is_rejected_not_executed(monkeypatch):
    calls = []
    handler_called = []

    def fake_generate_content(model, contents, config):
        calls.append(contents)
        if len(calls) == 1:
            return _fake_response(function_calls=[_fake_call("delete_everything", {})])
        return _fake_response(text="okay")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    def handler(args):
        handler_called.append(args)
        return {"status": "confirmed"}

    llm.generate_reply("hi", [], "RESTAURANT DATA", book_tool_handler=handler)

    # Never invoked for a tool we didn't declare — no silent success.
    assert handler_called == []


def test_no_function_call_in_response_skips_the_tool_round_trip(monkeypatch):
    calls = []

    def fake_generate_content(model, contents, config):
        calls.append(contents)
        return _fake_response(text="Just a normal answer, no booking involved.")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    handler_called = []
    reply = llm.generate_reply(
        "What's on the menu?",
        [],
        "RESTAURANT DATA",
        book_tool_handler=lambda args: handler_called.append(args) or {},
    )

    assert reply == "Just a normal answer, no booking involved."
    assert handler_called == []
    assert len(calls) == 1  # only one Gemini call needed
