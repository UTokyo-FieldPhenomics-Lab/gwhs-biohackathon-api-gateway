"""The ``biohack-gateway`` command line.

Token issuance happens here rather than behind an endpoint: it is a human action taken a handful of
times before an event and a few times during it, and a public surface whose whole purpose is to mint
credentials would be the most dangerous thing on the host — as well as a second thing needing a
secret, inside an API contract that is frozen for the event. The command reads and writes a file,
and nothing else, so it runs as well on a laptop against a copy of the pool as on the host serving
it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from biohack_gateway.tokens import DEFAULT_TOKENS_FILE, TokenPool, TokenPoolError


def _pool_option() -> argparse.ArgumentParser:
    """Build the parent parser that carries the option every verb shares.

    Returns
    -------
    argparse.ArgumentParser
        A parser holding ``--file``, to be passed through ``parents`` so that every verb accepts it
        in the same position: after the verb, as in ``tokens list --file …``.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_TOKENS_FILE,
        help="the pool file to read and write (default: %(default)s)",
    )
    return common


def _build_parser() -> argparse.ArgumentParser:
    """Build the command line.

    Returns
    -------
    argparse.ArgumentParser
        The top-level parser, with ``tokens`` and its three verbs.
    """
    parser = argparse.ArgumentParser(
        prog="biohack-gateway",
        description="Global Wheat API gateway: administration.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    tokens = commands.add_parser("tokens", help="manage the participant token pool")
    verbs = tokens.add_subparsers(dest="verb", required=True)
    common = _pool_option()

    verbs.add_parser("list", parents=[common], help="print the team ids, never the tokens")

    add = verbs.add_parser("add", parents=[common], help="issue a token to a team")
    add.add_argument("--id", required=True, help="the team id, as it will appear in the logs")
    add.add_argument("--token", help="a token to store verbatim instead of generating one")
    add.add_argument(
        "--force",
        action="store_true",
        help="replace the token of a team that already has one, keeping its id",
    )

    remove = verbs.add_parser("remove", parents=[common], help="revoke a team's token")
    remove.add_argument("--id", required=True, help="the team id to remove")

    return parser


def _note_written(path: Path) -> None:
    """Tell the operator what a written pool still needs, on stderr to keep stdout scriptable.

    Parameters
    ----------
    path : Path
        The file that was just replaced.
    """
    print(f"written: {path}", file=sys.stderr)
    print(
        "the gateway applies this on its next request; refresh the copy you keep off the host",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : sequence of str, optional
        The arguments to parse; the process arguments when omitted.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the pool refused the operation. A usage error exits ``2`` from
        argparse itself, which keeps the two kinds of failure apart in a script.
    """
    args = _build_parser().parse_args(argv)
    try:
        pool = TokenPool.load(args.file)
        match args.verb:
            case "list":
                for team_id in pool.team_ids():
                    print(team_id)
            case "add":
                token = pool.issue(args.id, args.token, replace=args.force)
                pool.save(args.file)
                # stdout is the value the operator came for, so it survives a redirect, and it is
                # flushed first so the notes below cannot win the race to the terminal.
                print(token, flush=True)
                _note_written(args.file)
            case "remove":
                pool.revoke(args.id)
                pool.save(args.file)
                _note_written(args.file)
    except TokenPoolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
