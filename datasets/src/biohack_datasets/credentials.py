"""Read the publisher's credential from the AWS console's access-key CSV.

The console hands out a two-column CSV — `Access key ID` and `Secret access key` — and that
download is the publisher's credential record: it is placed in the state directory at mode 0600
and read from there. An AWS SDK cannot parse it (a shared-credentials file is INI), which is why
these lines exist rather than pointing boto3 at the file. Nothing here ever prints a value: an
error names the file and the column, never the secret.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

ACCESS_KEY_COLUMN = "Access key ID"
"""First column of the console's download, byte-order mark stripped by the reader."""

SECRET_COLUMN = "Secret access key"
"""Second column of the console's download."""


class CredentialError(RuntimeError):
    """The access-key CSV is absent, malformed, or carries an empty value."""


@dataclass(frozen=True)
class AccessKeys:
    """One AWS access-key pair.

    Attributes
    ----------
    access_key_id : str
        The key id.
    secret_access_key : str
        The secret, held only long enough to hand to the SDK.
    """

    access_key_id: str
    secret_access_key: str


def read_access_keys_csv(path: Path) -> AccessKeys:
    """Read the console's access-key CSV.

    Parameters
    ----------
    path : pathlib.Path
        The downloaded CSV, placed in the publisher's state directory.

    Returns
    -------
    AccessKeys
        The key pair.

    Raises
    ------
    CredentialError
        The file is absent, its header is not the console's two columns, it has no data row,
        or a value is empty. The message names the path and the column, never a value.
    """
    if not path.is_file():
        raise CredentialError(
            f"{path}: no access-key CSV; place the console's download there and point"
            " BIOHACK_ACCESS_KEYS_CSV at it"
        )
    # utf-8-sig removes the byte-order mark the console writes; newline="" lets the csv module
    # handle CRLF, which a browser download on Windows produces.
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        missing = [column for column in (ACCESS_KEY_COLUMN, SECRET_COLUMN) if column not in fields]
        if missing:
            raise CredentialError(
                f"{path}: expected columns {ACCESS_KEY_COLUMN!r} and {SECRET_COLUMN!r},"
                f" missing {missing}"
            )
        row = next(reader, None)
    if row is None:
        raise CredentialError(f"{path}: the CSV has a header but no access-key row")
    access_key_id = (row.get(ACCESS_KEY_COLUMN) or "").strip()
    secret_access_key = (row.get(SECRET_COLUMN) or "").strip()
    if not access_key_id or not secret_access_key:
        raise CredentialError(f"{path}: the access-key row has an empty value")
    return AccessKeys(access_key_id, secret_access_key)
