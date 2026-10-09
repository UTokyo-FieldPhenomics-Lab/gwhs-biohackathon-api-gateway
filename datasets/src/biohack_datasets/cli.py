"""Command-line entry point for the dataset publisher.

The interface is fixed by the contract: one command with ``--release``, ``--bucket``,
``--dry-run``, and ``--concurrency``. ``build`` and ``sync`` are intentionally one command so
no stage can be skipped, and the exit code is the only signal that authorizes tearing down the
temporary instance.
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    """Run the ``biohack-datasets`` CLI.

    Parameters
    ----------
    argv : list of str, optional
        Argument list to parse, excluding the program name. Defaults to
        ``sys.argv[1:]``.

    Returns
    -------
    int
        Process exit code. A non-zero value stops the pipeline and forbids the
        instance-teardown step.

    Raises
    ------
    NotImplementedError
        The publisher is not implemented yet.
    """
    parser = argparse.ArgumentParser(prog="biohack-datasets", description=__doc__)
    parser.add_argument("--release", required=True, help="dataset release, e.g. v1.0")
    parser.add_argument("--bucket", default=None, help="target S3 bucket; omit to build locally")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument("--concurrency", type=int, default=16, help="parallel uploads")
    parser.parse_args(argv)
    raise NotImplementedError("dataset publisher is not implemented yet")


if __name__ == "__main__":
    raise SystemExit(main())
