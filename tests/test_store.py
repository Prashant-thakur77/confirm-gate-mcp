"""Every store gives the same one-time guarantees; the gate's replay protection rests on them."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from confirm_gate import DynamoStore, MemoryStore, SqliteStore
from moto import mock_aws


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


def test_add_counts_atomically_and_reads_with_zero(any_store: Any) -> None:
    assert any_store.add("ratelimit", "ip:1", 1, expires_at=100.0) == 1
    assert any_store.add("ratelimit", "ip:1", 2, expires_at=100.0) == 3
    assert any_store.add("ratelimit", "ip:1", -1) == 2
    assert any_store.add("ratelimit", "ip:1", 0) == 2
    assert any_store.add("ratelimit", "ip:2", 0) == 0
    assert any_store.get("ratelimit", "ip:1") is None  # counters are not documents


def test_add_under_contention_loses_no_increments(any_store: Any) -> None:
    if isinstance(any_store, DynamoStore):
        pytest.skip("moto's DynamoDB mock is not thread-safe; real DynamoDB ADD is atomic")
    seen: list[int] = []
    barrier = threading.Barrier(12)

    def go() -> None:
        barrier.wait()
        for _ in range(10):
            seen.append(any_store.add("ratelimit", "k", 1))

    ts = [threading.Thread(target=go) for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(seen) == list(range(1, 121))


@pytest.mark.skipif(not os.environ.get("CONFIRM_GATE_IT_TABLE"), reason="set CONFIRM_GATE_IT_TABLE to a real DynamoDB table")
def test_real_dynamodb_add_under_contention() -> None:
    import uuid

    store = DynamoStore(os.environ["CONFIRM_GATE_IT_TABLE"], region=os.environ.get("AWS_REGION", "us-east-1"))
    key = "it-" + uuid.uuid4().hex[:12]
    seen: list[int] = []
    barrier = threading.Barrier(16)

    def go() -> None:
        barrier.wait()
        for _ in range(4):
            seen.append(store.add("racetest", key, 1, expires_at=1.0))

    ts = [threading.Thread(target=go) for _ in range(16)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    store._c.delete_item(TableName=store.table, Key={"pk": {"S": f"racetest#{key}"}})
    assert sorted(seen) == list(range(1, 65))


@pytest.mark.skipif(not os.environ.get("CONFIRM_GATE_IT_TABLE"), reason="set CONFIRM_GATE_IT_TABLE to a real DynamoDB table")
def test_real_dynamodb_set_once_under_contention() -> None:
    import uuid

    store = DynamoStore(os.environ["CONFIRM_GATE_IT_TABLE"], region=os.environ.get("AWS_REGION", "us-east-1"))
    for _ in range(20):
        key = "it-" + uuid.uuid4().hex[:12]
        store.put("racetest", key, {"consumed_at": None})
        wins: list[bool] = []
        barrier = threading.Barrier(16)

        def go(i: int, key: str = key, barrier: threading.Barrier = barrier, wins: list[bool] = wins) -> None:
            barrier.wait()
            wins.append(store.set_once("racetest", key, "consumed_at", float(i)))

        ts = [threading.Thread(target=go, args=(i,)) for i in range(16)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        store._c.delete_item(TableName=store.table, Key={"pk": {"S": f"racetest#{key}"}})
        assert wins.count(True) == 1


def test_dynamo_scans_by_user_read_only_that_users_rows() -> None:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        DynamoStore.create_table("t-user", client=ddb)
        store = DynamoStore("t-user", client=ddb)
        for i in range(30):
            store.put("pending", f"p{i}", {"user_id": f"u{i % 3}", "n": i})
        calls: list[dict[str, Any]] = []
        real = ddb.query

        def spy(**kw: Any) -> Any:
            calls.append(kw)
            return real(**kw)

        ddb.query = spy  # type: ignore[method-assign]
        rows = store.scan("pending", user_id="u1")
        assert sorted(r["n"] for r in rows) == list(range(1, 30, 3))
        assert calls[-1]["IndexName"] == "user-kind-index"
        assert sum(len(c) for c in [rows]) == 10


def test_dynamo_works_on_a_table_without_indexes() -> None:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(TableName="old", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
        store = DynamoStore("old", client=ddb)
        store.put("receipt", "r1", {"user_id": "a"})
        store.put("receipt", "r2", {"user_id": "b"})
        assert store.scan("receipt", user_id="a") == [{"user_id": "a"}]
        assert len(store.scan("receipt")) == 2


def test_dynamo_never_mistakes_access_denied_for_a_missing_index() -> None:
    from botocore.exceptions import ClientError

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        DynamoStore.create_table("t-deny", client=ddb)
        store = DynamoStore("t-deny", client=ddb)
        store.put("pending", "p1", {"user_id": "u1"})

        def denied(**kw: Any) -> Any:  # what IAM says when a policy covers the table but not its indexes
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message":
                               "User is not authorized to perform: dynamodb:Query on resource: "
                               "arn:aws:dynamodb:us-east-1:1:table/t-deny/index/user-kind-index"}}, "Query")

        ddb.query = denied  # type: ignore[method-assign]
        with pytest.raises(ClientError):
            store.scan("pending", user_id="u1")  # a misconfigured policy must fail loudly, not scan the whole table
        assert store._has_user_index is not False and store._has_index is not False


def test_events_get_a_ttl() -> None:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        DynamoStore.create_table("t-ttl", client=ddb)
        store = DynamoStore("t-ttl", client=ddb)
        store.put("event", "e1", {"user_id": "u", "at": 1_800_000_000.0})
        item = ddb.get_item(TableName="t-ttl", Key={"pk": {"S": "event#e1"}})["Item"]
        assert int(item["ttl"]["N"]) == 1_800_000_000 + 30 * 86400


def test_sqlite_reads_by_user_use_the_user_column(tmp_path: Any) -> None:
    store = SqliteStore(tmp_path / "u.db")
    for i in range(50):
        store.put("pending", f"p{i}", {"user_id": f"u{i % 5}", "n": i})
    plan = store._conn.execute("EXPLAIN QUERY PLAN SELECT doc FROM docs WHERE kind = ? AND user = ?", ("pending", "u1")).fetchall()
    assert any("idx_docs_kind_user" in str(row) for row in plan)
    assert sorted(r["n"] for r in store.scan("pending", user_id="u1")) == list(range(1, 50, 5))


def test_sqlite_purges_rows_long_past_their_time(tmp_path: Any) -> None:
    store = SqliteStore(tmp_path / "p.db")
    store.put("signature", "old", {"expires_at": 1000.0})
    store.put("event", "old-ev", {"at": 1000.0, "user_id": "u"})
    store.put("signature", "new", {"expires_at": 9e12})
    removed = store.purge(now=1000.0 + 31 * 86400)
    assert removed == 2 and store.get("signature", "new") and store.get("signature", "old") is None


def test_set_once_never_loses_a_concurrent_set_once_on_another_field(any_store: Any) -> None:
    """Third review, finding 8: DynamoStore wrote back the whole doc it had read, dropping a field set in between."""
    any_store.put("device", "d1", {"revoked_at": None, "transcriber_at": None, "transcriber": ""})
    real_get = any_store.get
    injected = {"done": False}

    def racing_get(kind: str, key: str) -> Any:
        doc = real_get(kind, key)
        if kind == "device" and not injected["done"]:
            injected["done"] = True
            assert any_store.set_once("device", "d1", "revoked_at", 5.0)  # the user unpairs meanwhile
        return doc

    any_store.get = racing_get
    client = getattr(any_store, "_c", None)
    if client is not None:  # DynamoStore reads through its client
        real_get_item = client.get_item

        def racing_get_item(**kw: Any) -> Any:
            out = real_get_item(**kw)
            if not injected["done"]:
                injected["done"] = True
                any_store.get = real_get
                assert any_store.set_once("device", "d1", "revoked_at", 5.0)
            return out

        client.get_item = racing_get_item
    assert any_store.set_once("device", "d1", "transcriber_at", 6.0, {"transcriber": "alexa"})
    any_store.get = real_get
    if client is not None:
        client.get_item = real_get_item
    if not injected["done"]:  # memory and SQLite never read through get(): the race cannot happen there
        assert any_store.set_once("device", "d1", "revoked_at", 5.0)
    doc = any_store.get("device", "d1")
    assert doc["revoked_at"] == 5.0 and doc["transcriber"] == "alexa"


def test_update_replaces_a_doc_with_what_the_function_returns(any_store: Any) -> None:
    any_store.put("pending", "p1", {"user_id": "u1", "expires_at": 10.0, "consumed_at": None})
    out = any_store.update("pending", "p1", lambda d: {**d, "expires_at": 99.0, "extensions": 1})
    assert out == {"user_id": "u1", "expires_at": 99.0, "consumed_at": None, "extensions": 1}
    assert any_store.get("pending", "p1") == out
    assert any_store.scan("pending", user_id="u1") == [out]


def test_update_that_returns_none_changes_nothing(any_store: Any) -> None:
    any_store.put("pending", "p1", {"expires_at": 10.0})
    assert any_store.update("pending", "p1", lambda d: None) is None
    assert any_store.get("pending", "p1") == {"expires_at": 10.0}
    assert any_store.update("pending", "ghost", lambda d: {**d, "x": 1}) is None
    assert any_store.get("pending", "ghost") is None


def test_update_never_writes_over_a_change_made_after_its_read(any_store: Any) -> None:
    """A nonce consumed between update's read and its write wins: update reads again and sees it."""
    any_store.put("pending", "p1", {"expires_at": 10.0, "consumed_at": None})
    seen: list[Any] = []

    def push(doc: dict[str, Any]) -> dict[str, Any] | None:
        seen.append(doc.get("consumed_at"))
        if len(seen) == 1:  # someone answers the card set while we were deciding
            assert any_store.set_once("pending", "p1", "consumed_at", 5.0, {"outcome": "confirmed"})
        if doc.get("consumed_at") is not None:
            return None
        return {**doc, "expires_at": 99.0}

    assert any_store.update("pending", "p1", push) is None
    assert seen == [None, 5.0]
    assert any_store.get("pending", "p1") == {"expires_at": 10.0, "consumed_at": 5.0, "outcome": "confirmed"}
    assert not any_store.set_once("pending", "p1", "consumed_at", 6.0)


def test_update_keeps_a_one_time_field_one_time(any_store: Any) -> None:
    any_store.put("pending", "p1", {"expires_at": 10.0, "consumed_at": None})
    assert any_store.set_once("pending", "p1", "consumed_at", 5.0)
    any_store.update("pending", "p1", lambda d: {**d, "note": "x"})
    assert not any_store.set_once("pending", "p1", "consumed_at", 6.0)
    assert any_store.get("pending", "p1")["consumed_at"] == 5.0


def test_update_under_contention_loses_no_write(any_store: Any) -> None:
    if isinstance(any_store, DynamoStore):
        pytest.skip("moto's DynamoDB mock is not thread-safe; the conditional write is covered above")
    any_store.put("pending", "p", {"n": 0})
    barrier = threading.Barrier(8)

    def go() -> None:
        barrier.wait()
        for _ in range(5):
            assert any_store.update("pending", "p", lambda d: {**d, "n": d["n"] + 1}) is not None

    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert any_store.get("pending", "p") == {"n": 40}
