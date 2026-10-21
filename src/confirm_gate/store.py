"""Where the gate keeps its records, and the two atomic operations it relies on.

The whole replay story rests on two primitives every store must make atomic:

* ``put_if_absent`` — first writer wins (fingerprints, signatures, pairing).
* ``set_once`` — set a field only if it is still empty (consume a nonce, use evidence).

Documents are plain JSON-able dicts, addressed by ``(kind, key)``.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol


class Store(Protocol):
    def put(self, kind: str, key: str, doc: dict[str, Any]) -> None: ...
    def get(self, kind: str, key: str) -> dict[str, Any] | None: ...
    def put_if_absent(self, kind: str, key: str, doc: dict[str, Any]) -> bool: ...
    def set_once(self, kind: str, key: str, field: str, value: Any, updates: dict[str, Any] | None = None) -> bool: ...
    def scan(self, kind: str, **equals: Any) -> list[dict[str, Any]]: ...


def _matches(doc: dict[str, Any], equals: dict[str, Any]) -> bool:
    return all(doc.get(k) == v for k, v in equals.items())


class MemoryStore:
    """In-process store for tests and single-process demos."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, kind: str, key: str, doc: dict[str, Any]) -> None:
        with self._lock:
            self._data[(kind, key)] = copy.deepcopy(doc)

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            doc = self._data.get((kind, key))
            return copy.deepcopy(doc) if doc is not None else None

    def put_if_absent(self, kind: str, key: str, doc: dict[str, Any]) -> bool:
        with self._lock:
            if (kind, key) in self._data:
                return False
            self._data[(kind, key)] = copy.deepcopy(doc)
            return True

    def set_once(self, kind: str, key: str, field: str, value: Any, updates: dict[str, Any] | None = None) -> bool:
        with self._lock:
            doc = self._data.get((kind, key))
            if doc is None or doc.get(field) is not None:
                return False
            doc[field] = value
            doc.update(copy.deepcopy(updates or {}))
            return True

    def scan(self, kind: str, **equals: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(d) for (k, _), d in self._data.items() if k == kind and _matches(d, equals)]


class SqliteStore:
    """A file-backed store; safe across threads and processes via ``BEGIN IMMEDIATE``."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs (kind TEXT NOT NULL, key TEXT NOT NULL, doc TEXT NOT NULL,"
            " PRIMARY KEY (kind, key))"
        )

    def put(self, kind: str, key: str, doc: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO docs (kind, key, doc) VALUES (?, ?, ?)"
                " ON CONFLICT(kind, key) DO UPDATE SET doc = excluded.doc",
                (kind, key, json.dumps(doc)),
            )

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT doc FROM docs WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put_if_absent(self, kind: str, key: str, doc: dict[str, Any]) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO docs (kind, key, doc) VALUES (?, ?, ?)", (kind, key, json.dumps(doc))
            )
            return cur.rowcount == 1

    def set_once(self, kind: str, key: str, field: str, value: Any, updates: dict[str, Any] | None = None) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT doc FROM docs WHERE kind = ? AND key = ?", (kind, key)).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return False
                doc = json.loads(row[0])
                if doc.get(field) is not None:
                    self._conn.execute("ROLLBACK")
                    return False
                doc[field] = value
                doc.update(updates or {})
                self._conn.execute("UPDATE docs SET doc = ? WHERE kind = ? AND key = ?", (json.dumps(doc), kind, key))
                self._conn.execute("COMMIT")
                return True
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def scan(self, kind: str, **equals: Any) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT doc FROM docs WHERE kind = ? ORDER BY rowid", (kind,)).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        return [d for d in docs if _matches(d, equals)]


KIND_INDEX = "kind-index"
USER_INDEX = "user-kind-index"  # one user's rows of one kind, without reading anyone else's


class DynamoStore:
    """One DynamoDB table, key ``pk = "<kind>#<key>"``, the document as a JSON string.

    ``set_once`` uses a conditional write on a top-level marker attribute named after
    the field, so two writers racing on one nonce cannot both win.
    """

    def __init__(self, table_name: str, *, client: Any | None = None, region: str | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("dynamodb", region_name=region)
        self.table = table_name
        self._c = client
        self._has_index: bool | None = None
        self._has_user_index: bool | None = None

    @staticmethod
    def create_table(table_name: str, *, client: Any) -> None:
        client.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "kind", "AttributeType": "S"},
                                  {"AttributeName": "f_user_id", "AttributeType": "S"}],
            GlobalSecondaryIndexes=[
                {"IndexName": KIND_INDEX, "KeySchema": [{"AttributeName": "kind", "KeyType": "HASH"}],
                 "Projection": {"ProjectionType": "ALL"}},
                {"IndexName": USER_INDEX, "KeySchema": [{"AttributeName": "f_user_id", "KeyType": "HASH"},
                                                        {"AttributeName": "kind", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=table_name)

    @staticmethod
    def _pk(kind: str, key: str) -> dict[str, str]:
        return {"S": f"{kind}#{key}"}

    def _item(self, kind: str, key: str, doc: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {"pk": self._pk(kind, key), "kind": {"S": kind}, "doc": {"S": json.dumps(doc)}}
        for k, v in doc.items():  # top-level scalars, for scan filters and conditions
            if isinstance(v, str) and v and k not in ("pk", "kind", "doc"):
                item[f"f_{k}"] = {"S": v}
            if v is not None:
                # A set field keeps its one-time marker even if the whole doc is rewritten.
                item[f"once_{k}"] = {"BOOL": True}
        stamp = doc.get("expires_at") if isinstance(doc.get("expires_at"), int | float) else doc.get("at")
        if isinstance(stamp, int | float) and not isinstance(stamp, bool):
            item["ttl"] = {"N": str(int(stamp) + 30 * 86400)}  # rows age out 30 days after they expire or happen
        return item

    def put(self, kind: str, key: str, doc: dict[str, Any]) -> None:
        self._c.put_item(TableName=self.table, Item=self._item(kind, key, doc))

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        resp = self._c.get_item(TableName=self.table, Key={"pk": self._pk(kind, key)}, ConsistentRead=True)
        item = resp.get("Item")
        return json.loads(item["doc"]["S"]) if item else None

    def put_if_absent(self, kind: str, key: str, doc: dict[str, Any]) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._c.put_item(
                TableName=self.table,
                Item=self._item(kind, key, doc),
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def set_once(self, kind: str, key: str, field: str, value: Any, updates: dict[str, Any] | None = None) -> bool:
        from botocore.exceptions import ClientError

        doc = self.get(kind, key)
        if doc is None or doc.get(field) is not None:
            return False
        doc[field] = value
        doc.update(updates or {})
        marker = f"once_{field}"
        try:
            self._c.update_item(
                TableName=self.table,
                Key={"pk": self._pk(kind, key)},
                UpdateExpression="SET #doc = :doc, #m = :true",
                ConditionExpression="attribute_exists(pk) AND attribute_not_exists(#m)",
                ExpressionAttributeNames={"#doc": "doc", "#m": marker},
                ExpressionAttributeValues={":doc": {"S": json.dumps(doc)}, ":true": {"BOOL": True}},
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def scan(self, kind: str, **equals: Any) -> list[dict[str, Any]]:
        """Every doc of *kind* matching *equals*: a Query on the kind index, or a table Scan without one.

        A missing index (an older table) switches to scans for good; an index that is still backfilling
        is retried on the next call.
        """
        names = {"#k": "kind"}
        values: dict[str, Any] = {":k": {"S": kind}}
        filters = []
        for i, (field, value) in enumerate(equals.items()):
            if isinstance(value, str) and value:
                names[f"#f{i}"] = f"f_{field}"
                values[f":f{i}"] = {"S": value}
                filters.append(f"#f{i} = :f{i}")
        base: dict[str, Any] = {"TableName": self.table, "ExpressionAttributeNames": names,
                                "ExpressionAttributeValues": values}
        user = equals.get("user_id")
        if isinstance(user, str) and user and self._has_user_index is not False:
            others = [f for f in filters if names.get(f.split(" = ")[0]) != "f_user_id"]
            used = {"#k", "#u", *(f.split(" = ")[0] for f in others)}
            used_values = {":k", ":u", *(f.split(" = ")[1] for f in others)}
            q = {"TableName": self.table, "IndexName": USER_INDEX,
                 "KeyConditionExpression": "#u = :u AND #k = :k",
                 "ExpressionAttributeNames": {k: v for k, v in {**names, "#u": "f_user_id"}.items() if k in used},
                 "ExpressionAttributeValues": {k: v for k, v in {**values, ":u": {"S": user}}.items()
                                               if k in used_values}}
            if others:
                q["FilterExpression"] = " AND ".join(others)
            try:
                return [d for d in self._pages(self._c.query, q) if _matches(d, equals)]
            except Exception as exc:
                msg = str(exc).lower()
                if "index" not in msg:
                    raise
                if "backfilling" not in msg:
                    self._has_user_index = False
        if self._has_index is not False:
            try:
                return [d for d in self._pages(self._c.query, {**base, "IndexName": KIND_INDEX,
                                                                "KeyConditionExpression": "#k = :k",
                                                                **({"FilterExpression": " AND ".join(filters)}
                                                                   if filters else {})})
                        if _matches(d, equals)]
            except Exception as exc:
                msg = str(exc).lower()
                if "backfilling" in msg:
                    pass  # the index is being built: scan this time, try the index again next time
                elif "index" in msg:
                    self._has_index = False
                else:
                    raise
        scan = {**base, "FilterExpression": " AND ".join(["#k = :k", *filters]), "ConsistentRead": True}
        return [d for d in self._pages(self._c.scan, scan) if _matches(d, equals)]

    @staticmethod
    def _pages(op: Any, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        while True:
            resp = op(**kwargs)
            docs.extend(json.loads(i["doc"]["S"]) for i in resp.get("Items", []))
            if not resp.get("LastEvaluatedKey"):
                return docs
            kwargs = {**kwargs, "ExclusiveStartKey": resp["LastEvaluatedKey"]}

