import pytest


@pytest.mark.skip(reason="TODO: provide in-memory adapters before broker implementation")
async def test_broker_routes_one_request_through_module_interfaces() -> None:
    # TODO: Assemble fake Interception, Context, Action, Decision, State, and UI modules.
    # TODO: Prove a request can cross only public module interfaces end to end.
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: implement deterministic Jev fixtures")
async def test_scene_and_action_are_independent_typed_questions() -> None:
    # TODO: Verify the request contains both questions and stable semantic keys.
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: implement preference matching")
async def test_preference_match_skips_jev_and_marks_scene_unevaluated() -> None:
    raise NotImplementedError
