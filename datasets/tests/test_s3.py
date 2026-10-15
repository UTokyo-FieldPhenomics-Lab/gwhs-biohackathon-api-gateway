"""Tests for the narrow S3 surface and its in-memory implementation."""

from __future__ import annotations

import hashlib

import pytest
from botocore.exceptions import ClientError

from biohack_datasets.credentials import AccessKeys
from biohack_datasets.s3 import (
    AlreadyExists,
    Boto3ObjectStore,
    MemoryObjectStore,
    ObjectInfo,
    ObjectUnreadable,
    session_from_access_keys,
)


class _Client:
    """A stand-in boto3 client recording the calls it receives."""

    def __init__(self, head: object = None, put: object = None) -> None:
        self.head_response = head
        self.put_response = put
        self.calls: list[tuple[str, dict[str, object]]] = []

    def head_object(self, **kwargs: object) -> object:
        self.calls.append(("head", kwargs))
        if isinstance(self.head_response, Exception):
            raise self.head_response
        return self.head_response

    def put_object(self, **kwargs: object) -> object:
        self.calls.append(("put", kwargs))
        if isinstance(self.put_response, Exception):
            raise self.put_response
        return self.put_response


class _Session:
    """A stand-in boto3 session returning a fixed client."""

    def __init__(self, client: _Client) -> None:
        self._client = client

    def client(self, name: str) -> _Client:
        assert name == "s3"
        return self._client


def test_the_memory_store_round_trips_an_object() -> None:
    """An absent key is None; a written key reads back with its two owned headers."""
    store = MemoryObjectStore()
    assert store.head("k") is None

    etag = store.put("k", b"abc", content_type="image/jpeg", cache_control="public")

    assert etag == hashlib.md5(b"abc").hexdigest()
    assert store.head("k") == ObjectInfo(3, etag, "image/jpeg", "public")


def test_boto3_head_answers_none_when_the_object_is_absent() -> None:
    """S3's 404 is the normal first-publish case, not an error."""
    store = Boto3ObjectStore(
        "bucket", session=_Session(_Client(head=ClientError({"Error": {"Code": "404"}}, "H")))
    )

    assert store.head("k") is None


def test_boto3_head_maps_the_response_into_an_object_info() -> None:
    """Four fields, quotes stripped from the entity tag."""
    response = {
        "ContentLength": 3,
        "ETag": '"abc"',
        "ContentType": "image/jpeg",
        "CacheControl": "public",
    }
    store = Boto3ObjectStore("bucket", session=_Session(_Client(head=response)))

    assert store.head("k") == ObjectInfo(3, "abc", "image/jpeg", "public")


def test_boto3_put_is_one_request_with_both_headers() -> None:
    """A single-part PUT keeps the entity tag equal to the content MD5."""
    client = _Client(put={"ETag": '"abc"'})
    store = Boto3ObjectStore("bucket", session=_Session(client))

    assert store.put("k", b"abc", content_type="image/jpeg", cache_control="public") == "abc"

    ((action, kwargs),) = client.calls
    assert action == "put"
    assert kwargs["Bucket"] == "bucket"
    assert kwargs["Key"] == "k"
    assert kwargs["Body"] == b"abc"
    assert kwargs["ContentType"] == "image/jpeg"
    assert kwargs["CacheControl"] == "public"


def test_a_denied_head_is_not_read_as_an_absence() -> None:
    """A bucket without ListBucket answers 403 for a missing key; that is not 'absent'."""
    error = ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
    store = Boto3ObjectStore("bucket", session=_Session(_Client(head=error)))

    with pytest.raises(ObjectUnreadable):
        store.head("k")


def test_boto3_reraises_an_error_that_is_not_expected() -> None:
    """A server error is neither an absence nor a hidden key, and must surface."""
    error = ClientError({"Error": {"Code": "InternalError"}}, "HeadObject")
    store = Boto3ObjectStore("bucket", session=_Session(_Client(head=error)))

    with pytest.raises(ClientError):
        store.head("k")


def test_a_conditional_create_that_loses_the_race_is_reported() -> None:
    """The bucket refusing a create means the key is occupied, not that the write failed."""
    client = _Client(put=ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject"))
    store = Boto3ObjectStore("bucket", session=_Session(client))

    with pytest.raises(AlreadyExists):
        store.put(
            "k", b"abc", content_type="image/jpeg", cache_control="public", if_none_match=True
        )

    ((_, kwargs),) = client.calls
    assert kwargs["IfNoneMatch"] == "*"


def test_a_plain_put_does_not_ask_for_a_condition() -> None:
    """The repair path writes unconditionally; only the create path is conditional."""
    client = _Client(put={"ETag": '"abc"'})
    store = Boto3ObjectStore("bucket", session=_Session(client))

    store.put("k", b"abc", content_type="image/jpeg", cache_control="public")

    ((_, kwargs),) = client.calls
    assert "IfNoneMatch" not in kwargs


def test_the_memory_store_refuses_a_conditional_create_on_an_occupied_key() -> None:
    """The fake enforces the same create-if-absent rule the bucket does."""
    store = MemoryObjectStore()
    store.put("k", b"abc", content_type="image/jpeg", cache_control="public")

    with pytest.raises(AlreadyExists):
        store.put(
            "k", b"abc", content_type="image/jpeg", cache_control="public", if_none_match=True
        )


def test_a_session_is_built_from_an_explicit_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console CSV's pair reaches boto3 directly, never through the environment."""
    captured: dict[str, object] = {}

    class _Session:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("biohack_datasets.s3.boto3.Session", _Session)

    session_from_access_keys(AccessKeys("IDEXAMPLE", "SECRET"))

    assert captured == {"aws_access_key_id": "IDEXAMPLE", "aws_secret_access_key": "SECRET"}
