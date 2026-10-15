"""Tests for reading the console's access-key CSV."""

from __future__ import annotations

from pathlib import Path

import pytest

from biohack_datasets.credentials import CredentialError, read_access_keys_csv

BOM = b"\xef\xbb\xbf"
HEADER = "Access key ID,Secret access key"


def _write(tmp_path: Path, body: bytes, name: str = "awsAccessKeys.csv") -> Path:
    """Write a credential CSV and return its path."""
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_the_console_download_is_read_with_its_bom_and_crlf(tmp_path: Path) -> None:
    """The two quirks of a browser download must not reach the key material."""
    path = _write(tmp_path, BOM + (HEADER + "\r\nIDEXAMPLE,secret/with+symbols\r\n").encode())

    keys = read_access_keys_csv(path)

    assert keys.access_key_id == "IDEXAMPLE"
    assert keys.secret_access_key == "secret/with+symbols"


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    """Absent is the state before §5, and the error says what to place and where."""
    with pytest.raises(CredentialError, match="awsAccessKeys.csv"):
        read_access_keys_csv(tmp_path / "awsAccessKeys.csv")


def test_a_wrong_header_names_the_columns_and_never_a_value(tmp_path: Path) -> None:
    """A value must not leak into an error message."""
    path = _write(tmp_path, b"Key,Secret\nIDEXAMPLE,VERYSECRET\n")

    with pytest.raises(CredentialError) as info:
        read_access_keys_csv(path)

    assert "Access key ID" in str(info.value)
    assert "VERYSECRET" not in str(info.value)


def test_a_header_without_a_row_is_refused(tmp_path: Path) -> None:
    """The console always writes one row; a header alone means a truncated download."""
    path = _write(tmp_path, (HEADER + "\n").encode())

    with pytest.raises(CredentialError, match="no access-key row"):
        read_access_keys_csv(path)


@pytest.mark.parametrize("body", [f"{HEADER}\n,secret\n", f"{HEADER}\nID,\n"])
def test_an_empty_value_is_refused(tmp_path: Path, body: str) -> None:
    """An empty half of the pair must not become an anonymous S3 request."""
    with pytest.raises(CredentialError, match="empty value"):
        read_access_keys_csv(_write(tmp_path, body.encode()))
