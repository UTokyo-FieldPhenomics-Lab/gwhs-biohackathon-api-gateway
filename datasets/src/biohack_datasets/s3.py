"""The narrow S3 surface the publisher needs: head one object, put one object.

Two methods are the whole interface, and an in-memory implementation of them keeps every
decision in the publisher testable without a bucket. The publisher never lists, never
deletes, and never copies: a path is written once, and the only question is whether it
already holds exactly what this run would write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3
from botocore.exceptions import ClientError

from biohack_datasets.credentials import AccessKeys

_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})


@dataclass(frozen=True)
class ObjectInfo:
    """What one `HeadObject` answers about an existing object.

    Attributes
    ----------
    size : int
        Content length in bytes.
    etag : str
        Entity tag with the surrounding quotes removed. For a single-part `PUT` it is the
        content MD5, which is what makes the comparison cheap.
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

    def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
        """Write one object in a single request and return its entity tag.

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
            The entity tag the bucket answered with, quotes removed. A single-part
            `PUT` makes it the content MD5, which verification compares against.
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
            raise
        return ObjectInfo(
            size=int(response["ContentLength"]),
            etag=str(response["ETag"]).strip('"'),
            content_type=response.get("ContentType"),
            cache_control=response.get("CacheControl"),
        )

    def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
        """Write one object in a single request and return its entity tag."""
        response = self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=cache_control,
        )
        return str(response["ETag"]).strip('"')


@dataclass
class MemoryObjectStore:
    """An in-memory bucket, used by tests and by a dry local run.

    Attributes
    ----------
    objects : dict
        Key to `(body, content_type, cache_control)`.
    """

    objects: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)

    def head(self, key: str) -> ObjectInfo | None:
        """Return what the store holds under `key`, or `None` if it is absent."""
        stored = self.objects.get(key)
        if stored is None:
            return None
        body, content_type, cache_control = stored
        return ObjectInfo(len(body), hashlib.md5(body).hexdigest(), content_type, cache_control)

    def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
        """Store one object and answer with the entity tag the real bucket would."""
        self.objects[key] = (bytes(body), content_type, cache_control)
        return hashlib.md5(body).hexdigest()

    def tamper(self, key: str, body: bytes) -> None:
        """Replace an object's bytes behind the publisher's back.

        Parameters
        ----------
        key : str
            Object to replace.
        body : bytes
            New bytes, same length as the old ones so only the entity tag changes.
        """
        _, content_type, cache_control = self.objects[key]
        self.objects[key] = (bytes(body), content_type, cache_control)
