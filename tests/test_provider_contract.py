import pytest


@pytest.mark.skip(reason="TODO: implement reusable ChatContextProvider contract suite")
async def test_provider_rejects_ambiguous_conversation_matches() -> None:
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: implement reusable ChatContextProvider contract suite")
async def test_provider_returns_at_most_target_plus_ten_nearby_messages() -> None:
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: implement provider version validation")
async def test_unknown_provider_version_warns_and_fails_open() -> None:
    raise NotImplementedError
