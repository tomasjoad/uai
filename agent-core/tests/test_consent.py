import pytest

from agent_core.consent import ConsentEngine, ResourceSpec
from agent_core.storage import Store


@pytest.fixture
async def engine(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    store = await Store.open(tmp_path / "agent.db")
    yield ConsentEngine(store), ResourceSpec("path", root)
    await store.close()


async def test_unknown_actions_ask(engine):
    consent, spec = engine
    verdict = await consent.evaluate("write_file", {"path": "a.txt"}, spec)
    assert verdict.outcome == "ask"
    assert verdict.resource == "a.txt"


async def test_paths_outside_the_sandbox_are_denied_even_with_a_rule(engine):
    consent, spec = engine
    await consent.remember("write_file", "**")
    verdict = await consent.evaluate("write_file", {"path": "../a.txt"}, spec)
    assert verdict.outcome == "deny"


async def test_rules_match_by_tool_and_pattern(engine):
    consent, spec = engine
    rule_id = await consent.remember("write_file", "notes/*")
    allowed = await consent.evaluate("write_file", {"path": "notes/a.txt"}, spec)
    assert (allowed.outcome, allowed.rule_id) == ("allow", rule_id)
    assert (await consent.evaluate("write_file", {"path": "other.txt"}, spec)).outcome == "ask"
    assert (await consent.evaluate("read_file", {"path": "notes/a.txt"}, spec)).outcome == "ask"


async def test_revoked_rules_ask_again(engine):
    consent, spec = engine
    rule_id = await consent.remember("write_file")
    assert (await consent.evaluate("write_file", {"path": "a.txt"}, spec)).outcome == "allow"
    assert await consent.revoke(rule_id)
    assert not await consent.revoke(rule_id)
    assert (await consent.evaluate("write_file", {"path": "a.txt"}, spec)).outcome == "ask"


async def test_tools_without_a_resource_need_a_wildcard_rule(engine):
    consent, _ = engine
    await consent.remember("ping", "notes/*")
    assert (await consent.evaluate("ping", {}, None)).outcome == "ask"
    await consent.remember("ping", "*")
    assert (await consent.evaluate("ping", {}, None)).outcome == "allow"
    assert await consent.always_allowed("ping")
