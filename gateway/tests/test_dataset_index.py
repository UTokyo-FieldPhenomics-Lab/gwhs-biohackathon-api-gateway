"""The index reader: what a dataset offers, and which rows a filter selects."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GENERATED_AT, REVISION, write_index

from biohack_gateway.dataset_index import DatasetIndexError, load_datasets


def test_a_directory_that_does_not_exist_is_no_datasets(tmp_path: Path) -> None:
    """The gateway starts before the first publish, so an absent index is normal, not fatal."""
    assert load_datasets(tmp_path / "absent") == {}


def test_every_index_is_loaded_under_its_own_id(index_dir: Path) -> None:
    """A dataset id comes from the file's own dataset row, never from the file's name."""
    datasets = load_datasets(index_dir)

    assert list(datasets) == ["gwfss", "gwhd", "gwhs-temp"]
    assert datasets["gwhd"].revision == REVISION
    assert datasets["gwfss"].license == "cc-by-4.0"
    assert datasets["gwhd"].generated_at == GENERATED_AT


def test_the_shape_of_a_dataset_is_read_off_the_index(index_dir: Path) -> None:
    """Geometry, mask kind, and the metadata a dataset carries are facts in the file."""
    datasets = load_datasets(index_dir)

    assert datasets["gwhd"].geometry == "boxes"
    assert datasets["gwhd"].mask_kind is None
    assert datasets["gwhd"].metadata_keys == (
        "domain",
        "country",
        "location",
        "development_stage",
    )
    assert datasets["gwfss"].geometry is None
    assert datasets["gwfss"].mask_kind == "semantic"
    assert datasets["gwfss"].metadata_keys == ()
    assert datasets["gwhs-temp"].geometry == "rle"
    assert datasets["gwhs-temp"].image_id_present is True
    assert datasets["gwhs-temp"].metadata_keys == ()


def test_a_facet_counts_what_a_filter_may_name(index_dir: Path) -> None:
    """A key is offered when the index holds a value for it, and its count is a row count."""
    datasets = load_datasets(index_dir)

    assert dict(datasets["gwfss"].filters) == {
        "split": {"labelled": 2},
        "class": {"head": 2, "stem": 1, "leaf": 2},
    }
    assert dict(datasets["gwhs-temp"].filters) == {
        "split": {"train": 2},
        "tags": {"Labo": 2, "healthy": 1},
        "category": {"spikelet_-_side1": 2, "spikelet_-_side2": 1},
        "attribute": {"Green-Spikelet": 1},
    }
    # A box dataset's category is the upstream's unnamed integer; the field is still an index field.
    assert dict(datasets["gwhd"].filters) == {
        "split": {"train": 1, "validation": 1},
        "category": {"0": 2},
        "domain": {"field": 2},
        "country": {"France": 1, "Germany": 1},
        "location": {"Einbeck": 1},
        "development_stage": {"stem_elongation": 1, "tillering": 1},
    }


def test_select_matches_every_key_a_filter_may_name(index_dir: Path) -> None:
    """Each filter key reaches the table that holds it."""
    gwhs = load_datasets(index_dir)["gwhs-temp"]

    def rows(filters: list[tuple[str, str]]) -> list[int]:
        return [row.row for row in gwhs.select(filters, offset=0, limit=10).rows]

    assert rows([("tags", "Labo")]) == [0, 1]
    assert rows([("tags", "healthy")]) == [0]
    assert rows([("category", "spikelet_-_side2")]) == [0]
    assert rows([("attribute", "Green-Spikelet")]) == [0]
    assert rows([("split", "train")]) == [0, 1]
    assert rows([]) == [0, 1]


def test_two_values_for_one_key_narrow_rather_than_widen(index_dir: Path) -> None:
    """Repeating a key is a conjunction, so a row must carry both values to survive."""
    gwhs = load_datasets(index_dir)["gwhs-temp"]

    page = gwhs.select([("tags", "Labo"), ("tags", "healthy")], offset=0, limit=10)

    assert [row.row for row in page.rows] == [0]


def test_a_key_the_dataset_does_not_have_selects_nothing(index_dir: Path) -> None:
    """An unknown key is not an error here; it is a filter nothing satisfies."""
    page = load_datasets(index_dir)["gwfss"].select([("country", "Germany")], offset=0, limit=10)

    assert page.rows == ()
    assert page.next_offset is None


def test_select_pages_with_an_offset(index_dir: Path) -> None:
    """A page is a slice of the match, and the offset it hands on is where the next one starts."""
    gwhs = load_datasets(index_dir)["gwhs-temp"]

    first = gwhs.select([], offset=0, limit=1)
    assert [row.row for row in first.rows] == [0]
    assert first.next_offset == 1

    last = gwhs.select([], offset=1, limit=10)
    assert [row.row for row in last.rows] == [1]
    assert last.next_offset is None


def test_a_file_that_is_not_an_index_is_refused(tmp_path: Path) -> None:
    """A broken index stops the gateway at startup, because serving a wrong answer is worse."""
    directory = tmp_path / "index"
    directory.mkdir()
    (directory / "broken.db").write_text("not a database", encoding="utf-8")

    with pytest.raises(DatasetIndexError, match="broken.db"):
        load_datasets(directory)


def test_a_row_class_the_palette_does_not_define_is_refused(tmp_path: Path) -> None:
    """A class id with no name would be silently dropped, so it is refused instead."""
    directory = tmp_path / "index"
    directory.mkdir()
    write_index(
        directory / "gwhs.db",
        dataset=("gwhs", REVISION, "0.1.0", None, "semantic", GENERATED_AT),
        rows=((0, "train", "png", "png", 8, 8, None, None, None, None, None),),
        row_classes=((0, 7),),
    )

    with pytest.raises(DatasetIndexError, match="class 7"):
        load_datasets(directory)
