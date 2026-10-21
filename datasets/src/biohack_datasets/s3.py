"""The narrow S3 surface the publisher needs: head one object, create one object.

Two methods are the whole interface, and an in-memory implementation of them keeps every
decision in the publisher testable without a bucket. The publisher never lists, never
deletes, and never copies: a path is written once, so the only write is a create that the
bucket refuses when the key is already taken. There is no unconditional write to reach for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3
from botocore.exceptions import ClientError

from biohack_datasets.credentials import AccessKeys

_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})
_DENIED = frozenset({"403", "AccessDenied"})
_PRECONDITION_FAILED = frozenset({"412", "PreconditionFailed"})


class ObjectUnreadable(RuntimeError):
    """A HEAD was denied, so whether the object exists cannot be determined.

    A private bucket that grants `GetObject` but not `ListBucket` answers `403` for a key
    that does not exist, because telling the two apart would leak existence. A reader must
    therefore treat that answer as neither "absent" nor "present". The publisher never
    decides existence from a HEAD before writing — it attempts a conditional create — and
    needs a HEAD only to verify a write or to inspect what an occupied key holds.
    """


class AlreadyExists(RuntimeError):
    """A conditional create was refused because the key already holds an object."""


@dataclass(frozen=True)
class ObjectInfo:
    """What one `HeadObject` answers about an existing object.

    Attributes
    ----------
    size : int
        Content length in bytes.
    etag : str
        Entity tag with the surrounding quotes removed. It is opaque: the publisher compares
        the answer to a write with the HEAD that follows it, and never computes or interprets
        one.
    content_type : str or None
        Stored content type, absent on an object written by other means.
    cache_control : str or None
        Stored cache lifetime, absent on an object written by other means.
    """

    size: int
    etag: str
    content_type: str | None
    cache_control: str | None


class ObjectStore(Protocol):
    """The two operations the publisher performs against a bucket."""

    def head(self, key: str) -> ObjectInfo | None:
        """Return what the bucket holds under `key`, or `None` if it is absent.

        Parameters
        ----------
        key : str
            Object key inside the bucket.

        Returns
        -------
        ObjectInfo or None
            The object's size, entity tag, and owned headers.
        """
        ...

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> str:
        """Create one object and return the entity tag the bucket answered with.

        The write is conditional on the key being free, so it can never replace an object
        that is already published. There is deliberately no unconditional variant: a header
        that is wrong is a conflict for a human to resolve, not something a publish repairs.

        Parameters
        ----------
        key : str
            Object key inside the bucket.
        body : bytes
            Bytes to store.
        content_type : str
            Type to store with the object.
        cache_control : str
            Cache lifetime to store with the object.

        Returns
        -------
        str
            The entity tag the bucket answered with, quotes removed. It is opaque and is
            compared only against the HEAD that follows the write.

        Raises
        ------
        AlreadyExists
            The key already holds an object.
        """
        ...


def session_from_access_keys(keys: AccessKeys) -> Any:
    """Build a boto3 session that authenticates with an explicit key pair.

    Parameters
    ----------
    keys : AccessKeys
        The pair read from the console's CSV.

    Returns
    -------
    boto3.Session
        A session carrying those keys. The region is resolved from the environment exactly as
        it would be for a profile, so `AWS_REGION` is still the one place it is set.
    """
    return boto3.Session(
        aws_access_key_id=keys.access_key_id,
        aws_secret_access_key=keys.secret_access_key,
    )


class Boto3ObjectStore:
    """An :class:`ObjectStore` backed by a real bucket.

    Attributes
    ----------
    bucket : str
        Name of the target bucket, without the `s3://` prefix.
    """

    def __init__(self, bucket: str, session: Any | None = None) -> None:
        """Create the store.

        Parameters
        ----------
        bucket : str
            Target bucket name.
        session : boto3.Session, optional
            Session to take the client from. Omitted, a default session applies boto3's
            standard chain; the publisher passes one built from the console CSV
            (:func:`session_from_access_keys`).
        """
        self.bucket = bucket
        self._client = (session or boto3.Session()).client("s3")

    def head(self, key: str) -> ObjectInfo | None:
        """Return what the bucket holds under `key`, or `None` if it is absent."""
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND:
                return None
            if code in _DENIED:
                raise ObjectUnreadable(
                    f"{self.bucket}/{key}: HEAD was denied, existence is unknown"
                ) from error
            raise
        return ObjectInfo(
            size=int(response["ContentLength"]),
            etag=str(response["ETag"]).strip('"'),
            content_type=response.get("ContentType"),
            cache_control=response.get("CacheControl"),
        )

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> str:
        """Create one object and return its entity tag."""
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "CacheControl": cache_control,
            "IfNoneMatch": "*",
        }
        try:
            response = self._client.put_object(**request)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in _PRECONDITION_FAILED:
                raise AlreadyExists(f"{self.bucket}/{key}: already exists") from error
            raise
        return str(response["ETag"]).strip('"')


@dataclass(frozen=True)
class StoredObject:
    """What the in-memory bucket holds under one key.

    Attributes
    ----------
    body : bytes
        Stored bytes.
    content_type : str
        Stored content type.
    cache_control : str
        Stored cache lifetime.
    etag : str
        Entity tag this particular write was given. It is opaque and unique per write,
        never derived from the bytes, so a test can model a readback that disagrees with the
        write without any local hash.
    """

    body: bytes
    content_type: str
    cache_control: str
    etag: str


@dataclass
class MemoryObjectStore:
    """An in-memory bucket, used by tests.

    Attributes
    ----------
    objects : dict
        Key to :class:`StoredObject`.
    """

    objects: dict[str, StoredObject] = field(default_factory=dict)
    _writes: int = field(default=0, repr=False, compare=False)

    def head(self, key: str) -> ObjectInfo | None:
        """Return what the store holds under `key`, or `None` if it is absent."""
        stored = self.objects.get(key)
        if stored is None:
            return None
        return ObjectInfo(len(stored.body), stored.etag, stored.content_type, stored.cache_control)

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> str:
        """Create one object under a fresh simulated entity tag."""
        if key in self.objects:
            raise AlreadyExists(f"{key}: already exists")
        self._writes += 1
        etag = f"simulated-etag-{self._writes}"
        self.objects[key] = StoredObject(bytes(body), content_type, cache_control, etag)
        return etag
