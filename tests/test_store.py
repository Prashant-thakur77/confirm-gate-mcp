"""Every store gives the same one-time guarantees; the gate's replay protection rests on them."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from confirm_gate import DynamoStore, MemoryStore, SqliteStore


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def any_store(request: pytest.FixtureRequest, tmp_path: Any) -> Iterator[Any]:
    if request.param == "memory":
        yield MemoryStore()
    elif request.param == "sqlite":
        yield SqliteStore(tmp_path / "s.db")
    else:
        with mock_aws():
            ddb = boto3.client("dynamodb", region_name="us-east-1")
            DynamoStore.create_table("gate-test", client=ddb)
            yield DynamoStore("gate-test", client=ddb)


def test_put_get_round_trip(any_store: Any) -> None:
    any_store.put("pending", "p1", {"a": 1, "nested": {"b": [1, 2]}, "used_at": None})
    assert any_store.get("pending", "p1") == {"a": 1, "nested": {"b": [1, 2]}, "used_at": None}
    assert any_store.get("pending", "nope") is None
    assert any_store.get("evidence", "p1") is None


def test_put_if_absent_wins_once(any_store: Any) -> None:
    assert any_store.put_if_absent("fp", "x", {"by": 1})
    assert not any_store.put_if_absent("fp", "x", {"by": 2})
    assert any_store.get("fp", "x") == {"by": 1}


def test_set_once_sets_a_field_once(any_store: Any) -> None:
    any_store.put("evidence", "e1", {"used_at": None, "n": 0})
    assert any_store.set_once("evidence", "e1", "used_at", 5.0, {"n": 1})
    assert not any_store.set_once("evidence", "e1", "used_at", 6.0, {"n": 2})
    assert any_store.get("evidence", "e1") == {"used_at": 5.0, "n": 1}


def test_set_once_on_a_missing_key_fails(any_store: Any) -> None:
    assert not any_store.set_once("evidence", "ghost", "used_at", 1.0)


def test_set_once_under_contention(any_store: Any) -> None:
    if isinstance(any_store, DynamoStore):
        pytest.skip("moto's DynamoDB mock is not thread-safe (conditional updates race inside the mock); "
                    "real DynamoDB is covered by test_real_dynamodb_set_once_under_contention")
    any_store.put("pending", "p", {"consumed_at": None})
    wins: list[bool] = []
    barrier = threading.Barrier(10)

    def go(i: int) -> None:
        barrier.wait()
        wins.append(any_store.set_once("pending", "p", "consumed_at", float(i)))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert wins.count(True) == 1


def test_scan_filters_by_fields(any_store: Any) -> None:
    any_store.put("receipt", "r1", {"user_id": "u1", "t": 1})
    any_store.put("receipt", "r2", {"user_id": "u2", "t": 2})
    any_store.put("pending", "p1", {"user_id": "u1"})
    rows = any_store.scan("receipt", user_id="u1")
    assert rows == [{"user_id": "u1", "t": 1}]
    assert len(any_store.scan("receipt")) == 2


def test_floats_and_bools_survive(any_store: Any) -> None:
    any_store.put("k", "1", {"f": 0.125, "b": True, "n": None, "s": "", "l": []})
    assert any_store.get("k", "1") == {"f": 0.125, "b": True, "n": None, "s": "", "l": []}


@pytest.mark.skipif(not os.environ.get("CONFIRM_GATE_IT_TABLE"), reason="set CONFIRM_GATE_IT_TABLE to a real DynamoDB table")
def test_real_dynamodb_set_once_under_contention() -> None:
    import uuid

    store = DynamoStore(os.environ["CONFIRM_GATE_IT_TABLE"], region=os.environ.get("AWS_REGION", "us-east-1"))
    for _ in range(20):
        key = "it-" + uuid.uuid4().hex[:12]
        store.put("racetest", key, {"consumed_at": None})
        wins: list[bool] = []
        barrier = threading.Barrier(16)

        def go(i: int, key: str = key) -> None:
            barrier.wait()
            wins.append(store.set_once("racetest", key, "consumed_at", float(i)))

        ts = [threading.Thread(target=go, args=(i,)) for i in range(16)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        store._c.delete_item(TableName=store.table, Key={"pk": {"S": f"racetest#{key}"}})
        assert wins.count(True) == 1
