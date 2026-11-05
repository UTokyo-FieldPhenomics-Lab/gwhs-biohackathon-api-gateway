"""The quickstart must collect credentials before constructing its real client."""

from __future__ import annotations

import getpass
import os
import runpy
from pathlib import Path
from typing import Any

import pytest

import global_wheat

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("existing", [None, "", "existing-participant-key"])
def test_setup_collects_a_key_before_constructing_the_client(
    existing: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run the generated setup cells without network calls or a pre-seeded credential."""
    monkeypatch.setenv("GLOBAL_WHEAT_API_KEY", existing or "")
    if existing is None:
        monkeypatch.delenv("GLOBAL_WHEAT_API_KEY", raising=False)
    prompts: list[str] = []

    def supply_key(prompt: str) -> str:
        """Model private user input without echoing it into notebook output."""
        prompts.append(prompt)
        return "entered-participant-key"

    monkeypatch.setattr(getpass, "getpass", supply_key)
    converter = runpy.run_path(str(REPO / "docs/scripts/make_notebook.py"))
    page = (REPO / "docs/content/examples/quickstart.md").read_text(encoding="utf-8")
    cells = converter["cells"](converter["strip_skipped"](page))
    namespace: dict[str, Any] = {}
    for cell in cells:
        source = "".join(cell["source"])
        # A shell cell is Python only after the notebook's `!` prefix, and it may open with a
        # comment above the command. A line starting with `!` is not valid Python anywhere, so
        # this finds the cell whatever the page put before its first command.
        if cell["cell_type"] != "code" or any(
            line.lstrip().startswith("!") for line in source.splitlines()
        ):
            continue
        exec(compile(source, "quickstart setup", "exec"), namespace)
        if "client" in namespace:
            break

    assert isinstance(namespace.get("client"), global_wheat.Client)
    assert os.environ["GLOBAL_WHEAT_API_KEY"] == (existing or "entered-participant-key")
    assert len(prompts) == (0 if existing else 1)
    # The setup prints the versions it found before the client's own name; the name is the last
    # line, and what this test is about is that the client was constructed at all.
    assert capsys.readouterr().out.strip().splitlines()[-1] == "Client"
