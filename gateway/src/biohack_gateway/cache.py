"""Row answers kept on disk, so asking the same question twice costs a lookup and not a lane.

A model's answer to a published row is a function of the row, the model, the query and the revision
the row was published at — nothing about it belongs to the caller or the session. The second
participant to ask therefore has no reason to occupy a GPU, and the pool is the one scarce thing
this gateway holds.

**The key is the whole answer's provenance, and it is readable.** A stored answer is filed under the
canonical question itself — dataset, published `revision`, row, model and query as compact JSON — so
an operator reading the file sees what each entry answers, and no digest stands between a question
and its entry. Changing the published `revision` changes its keys and makes those entries cold;
republishing the same revision retains valid answers, because the revision identifies the row.
The query is taken literally and only ordered by parameter name: two spellings of the same number
(`0.7` and `0.70`) are two entries, which is a miss that costs one inference and never a wrong
answer.

**Only a `200` is ever stored**, by the caller: a refusal is about this request, and caching one
would hand it to a caller who asked something else.

**The cache is an optimization and must never be a dependency.** A file that cannot be read or
written is a miss with a warning; the backend can still answer, and turning a full disk into an
outage would invert what this module is for.

SQLite rather than a dictionary: an answer is up to about 12 KB, the pool answers thousands of them,
and the gateway is restarted far more often than the dataset is republished. `journal_mode=WAL` and
one connection per thread are what let the row handlers — which run in FastAPI's thread pool —
read while another thread writes, without a lock around the whole store.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path.home() / ".config" / "biohack-gateway" / "cache.sqlite"
"""Where answers are kept unless the host names another file, beside the index it answers about."""

DEFAULT_CACHE_MAX_BYTES = 2 * 1024**3
"""How much a cache may hold: two gigabytes, about 170 000 row answers, on a host that has the room.

It is a cap rather than a policy: the store is pruned back under it at startup and every
:data:`PRUNE_EVERY_WRITES` writes, oldest answer first.
"""

CACHE_PATH_ENV = "BIOHACK_CACHE_PATH"
"""Overrides :data:`DEFAULT_CACHE_PATH`, so a host can put the file on another volume."""

CACHE_ENV = "BIOHACK_CACHE"
"""Switches the cache off when it reads ``off``, ``0``, ``false`` or ``no``.

An escape hatch for the host rather than a setting per request: a cache that is answering wrongly
is a production incident, and a restart with one variable set is the smallest response to it.
"""

CACHE_MAX_BYTES_ENV = "BIOHACK_CACHE_MAX_BYTES"
"""Overrides :data:`DEFAULT_CACHE_MAX_BYTES`. A value that is not a positive number is ignored."""

PRUNE_EVERY_WRITES = 1000
"""How many stored answers pass between prunes, besides the one at startup.

Rare enough to cost nothing per request, frequent enough that a cache filling at tens of answers a
minute is back under its cap within a minute of crossing it.
"""

_OFF = frozenset({"off", "0", "false", "no"})
"""The values of :data:`CACHE_ENV` that switch the store off. Everything else leaves it on."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    key         TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    body        BLOB NOT NULL,
    computed_at TEXT NOT NULL,
    bytes       INTEGER NOT NULL
)
"""
"""One row per answer. `key` is the canonical question itself, and `request` repeats that text: the
columns are kept as they were so a store filled before the key was readable still opens, cold-misses
and ages out under pruning instead of needing a migration.
"""


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    """A stored answer, as a caller reads it back.

    Attributes
    ----------
    body : bytes
        The backend's own body, byte for byte: the answer is re-served from it, not re-derived.
    computed_at : datetime
        When that body was stored, which the caller publishes as ``cached_at`` so a reader knows it
        is an answer from the past rather than one just made.
    """

    body: bytes
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class CacheStats:
    """What the store holds and how it has answered, for the operator's surface.

    Attributes
    ----------
    entries, bytes : int
        How many answers are stored and how much their bodies weigh. `bytes` is the figure the
        budget is counted in — body bytes only — so the database file is larger: it also holds the
        request each entry is keyed by and SQLite's own pages.
    hits, misses : int
        Lookups answered from the store and lookups that found nothing, since the process started.
    """

    entries: int
    bytes: int
    hits: int
    misses: int


def canonical_request(*, dataset: str, revision: str, row: int, model: str, query: str) -> str:
    """Write the question a cache entry answers, in one canonical spelling.

    Parameters
    ----------
    dataset : str
        The dataset id the row belongs to.
    revision : str
        The dataset's published revision; changing it makes a different question.
    row : int
        The absolute row of that revision.
    model : str
        The model id that answered.
    query : str
        The query string as the client sent it.

    Returns
    -------
    str
        Compact JSON with sorted keys and the query's parameters in name order. It *is* the cache
        key: :class:`ResultCache` files an answer under exactly this text, so a reader of the file
        sees what an entry answers.

    Notes
    -----
    Only the *order* of the parameters is normalized, never their spelling: sorting is what makes
    two clients that send the same parameters in a different order one entry, while ``0.70`` and
    ``0.7`` stay two — this module may not know which of a model's parameters are numbers.

    The `revision` is part of the text rather than a detail beside it, so a key says which publish
    of a dataset it answers: a republish that changes the revision invalidates every answer by
    itself, and one that keeps the revision leaves them valid.
    """
    return json.dumps(
        {
            "dataset": dataset,
            "revision": revision,
            "row": row,
            "model": model,
            "query": _sorted_query(query),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _sorted_query(query: str) -> str:
    """Order a query string's parameters by name, leaving each one's spelling alone.

    Parameters
    ----------
    query : str
        The raw query string, without the leading ``?``.

    Returns
    -------
    str
        The same components, ordered by the name before each ``=``. A stable sort, so a repeated
        name keeps the order it was sent in.
    """
    parts = [part for part in query.split("&") if part]
    return "&".join(sorted(parts, key=lambda part: part.partition("=")[0]))


class ResultCache:
    """The answers this gateway has already computed, keyed by the question.

    Parameters
    ----------
    path : Path or None, optional
        The SQLite file. `None` takes :data:`CACHE_PATH_ENV`, or :data:`DEFAULT_CACHE_PATH` where
        that is unset.
    max_bytes : int or None, optional
        The cap on the stored bodies. `None` takes :data:`CACHE_MAX_BYTES_ENV`, or
        :data:`DEFAULT_CACHE_MAX_BYTES`.

        Bodies, not bytes on disk: each entry also stores the canonical request twice — once as the
        key and once in the `request` column the existing schema keeps — and SQLite keeps its own
        pages beside the rows. The file is therefore larger than this number, by a factor that
        depends on how small the answers are: a body of a few bytes against a query string of up to
        :data:`biohack_gateway.models.MAX_QUERY_BYTES` weighs some hundreds of times what it is
        accounted at. Treat the setting as a bound on what is served from memory and replayed, not
        as a disk quota; watch the file itself if disk is the thing that matters.
    enabled : bool or None, optional
        Whether anything is stored at all. `None` reads :data:`CACHE_ENV`, which switches the cache
        off when it names one of ``off``, ``0``, ``false``, ``no``. An explicit value wins over the
        environment, which is what lets one process ask for a store while the host has it off.

    Raises
    ------
    ValueError
        `max_bytes` is not positive, so the store could never hold anything.

    Notes
    -----
    A disabled instance is a no-op: it creates no file, always misses and never writes, so the
    routes need no branch around it and a host can switch it off by restarting with one variable.

    **One connection per thread**, kept in a `threading.local` and closed together by
    :meth:`close`. The predict handlers run in FastAPI's thread pool, so two threads really do
    touch this store at once; WAL lets a reader proceed while another thread commits, which a
    single shared connection under a lock would not. Connections are tracked in a list only so the
    process can close them — a dead pool thread's connection stays open until then, which is a
    handful of descriptors on a fixed-size pool and not a leak that grows with traffic.
    """

    def __init__(
        self,
        path: Path | None = None,
        max_bytes: int | None = None,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._enabled = (not _switched_off()) if enabled is None else enabled
        self._path = (
            Path(os.environ.get(CACHE_PATH_ENV) or DEFAULT_CACHE_PATH) if path is None else path
        )
        self._max_bytes = _env_max_bytes() if max_bytes is None else max_bytes
        if self._max_bytes <= 0:
            raise ValueError(f"a cache of {self._max_bytes} bytes can hold nothing")
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._entries = 0
        self._stored_bytes = 0
        self._closed = False
        if self._enabled:
            # Read once, here: after this the counts are kept as answers are written and dropped, so
            # `/pool` — which any key may call — never scans a file that may be gigabytes.
            self._recount()
            # At startup rather than on the first answer: a host restarted after a week of traffic
            # should come back within its cap, not prune in the middle of serving.
            self.prune()

    def get(self, request: str) -> CachedAnswer | None:
        """Look an answer up, or report that there is none.

        Parameters
        ----------
        request : str
            A string from :func:`canonical_request`: the question itself, which is the key.

        Returns
        -------
        CachedAnswer or None
            The stored body and when it was computed, or `None` when nothing is stored under that
            key — which is equally the answer of a disabled or unreadable cache.
        """
        if not self._enabled or self._closed:
            return None
        try:
            record = (
                self._connection()
                .execute("SELECT body, computed_at FROM answers WHERE key = ?", (request,))
                .fetchone()
            )
        except (OSError, sqlite3.Error) as error:
            # A miss, never a failed request: the backend can still answer.
            logger.warning("could not read a cached answer: %s", error)
            self._count(hit=False)
            return None
        if record is None:
            self._count(hit=False)
            return None
        self._count(hit=True)
        return CachedAnswer(body=record[0], computed_at=datetime.fromisoformat(record[1]))

    def put(self, request: str, body: bytes) -> None:
        """Store one backend's answer under the question it answers.

        Parameters
        ----------
        request : str
            The canonical question from :func:`canonical_request`, used as the key and written to
            the `request` column too, since the schema predates the key being readable.
        body : bytes
            The backend's own body. The caller stores a `200` and nothing else: a refusal belongs to
            the request that provoked it.

        Notes
        -----
        An existing entry is replaced rather than refused: two callers that miss the same question
        at once both compute it, and the later write is as true as the earlier one.

        The connection is taken before the lock, because opening one takes that same lock: a thread
        writing without a prior successful read would otherwise wait for a lock it is holding. This
        includes a read that missed because opening failed. The write and counts move under the
        lock, so a replacement is counted once however many threads store the same key.
        """
        if not self._enabled or self._closed:
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connection()
            with self._lock:
                previous = connection.execute(
                    "SELECT bytes FROM answers WHERE key = ?", (request,)
                ).fetchone()
                connection.execute(
                    "INSERT OR REPLACE INTO answers (key, request, body, computed_at, bytes)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (request, request, body, datetime.now(UTC).isoformat(), len(body)),
                )
                connection.commit()
                if previous is None:
                    self._entries += 1
                    self._stored_bytes += len(body)
                else:
                    self._stored_bytes += len(body) - previous[0]
        except (OSError, sqlite3.Error) as error:
            _discard(connection)
            logger.warning("could not store a cached answer: %s", error)
            return
        if self._count_write() % PRUNE_EVERY_WRITES == 0:
            self.prune()

    def forget(self, request: str) -> None:
        """Drop one stored answer, so the next call for it asks a backend.

        Parameters
        ----------
        request : str
            The canonical question from :func:`canonical_request`, which is the key.

        Notes
        -----
        The caller reaches for this when a stored body can no longer be read as this contract's
        answer — after a shape change, or after somebody wrote the file by hand. Dropping it is
        what keeps a stale entry from becoming a permanent failure for one row: the cache's worst
        allowed outcome is a miss, never an error nobody can clear without a shell.
        """
        if not self._enabled or self._closed:
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connection()
            with self._lock:
                previous = connection.execute(
                    "SELECT bytes FROM answers WHERE key = ?", (request,)
                ).fetchone()
                connection.execute("DELETE FROM answers WHERE key = ?", (request,))
                connection.commit()
                if previous is not None:
                    self._entries -= 1
                    self._stored_bytes -= previous[0]
        except (OSError, sqlite3.Error) as error:
            _discard(connection)
            logger.warning("could not drop a cached answer: %s", error)

    def stats(self) -> CacheStats:
        """Report what is stored and how the store has answered since the process started.

        Returns
        -------
        CacheStats
            Counts of entries, stored bytes, hits and misses. A disabled or unreadable cache reports
            zero entries, which is what it holds.

        Notes
        -----
        The two stored figures are kept as answers are written and dropped, not measured here: this
        route is open to any participant key, and the file it describes can be gigabytes. The price
        is that they describe what *this process* has stored — one process serves, so that is the
        file — and that a write which lost its commit is not counted.
        """
        with self._lock:
            hits, misses = self._hits, self._misses
            entries, stored = self._entries, self._stored_bytes
        if not self._enabled or self._closed:
            return CacheStats(entries=0, bytes=0, hits=hits, misses=misses)
        return CacheStats(entries=entries, bytes=stored, hits=hits, misses=misses)

    def _recount(self) -> None:
        """Read the store's totals once, so the rest of the process can count instead of scan.

        Notes
        -----
        Called when the store is opened, before anything serves. A file that cannot be read leaves
        the counts at zero, which is what a cache that only misses is worth; the first write that
        succeeds starts counting from there.
        """
        try:
            record = (
                self._connection()
                .execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM answers")
                .fetchone()
            )
        except (OSError, sqlite3.Error) as error:
            logger.warning("could not measure the cache: %s", error)
            return
        self._entries, self._stored_bytes = record[0], record[1]

    def prune(self) -> None:
        """Drop the oldest answers until the stored bodies fit under the cap.

        Notes
        -----
        Ordered by `computed_at`, then by key so that two answers stored inside the same clock tick
        are dropped in a fixed order. Whole entries are dropped rather than trimmed: half an answer
        is no answer.

        The connection is taken before the lock for the reason :meth:`put` gives: this method runs
        at startup and from a thread that stored the answer that filled the store, and opening a
        connection takes the lock it would otherwise already hold.
        """
        if not self._enabled or self._closed:
            return
        connection: sqlite3.Connection | None = None
        doomed: list[tuple[str]] = []
        try:
            connection = self._connection()
            with self._lock:
                stored = self._stored_bytes
                if stored <= self._max_bytes:
                    return
                excess = stored - self._max_bytes
                freed = 0
                oldest = connection.execute(
                    "SELECT key, bytes FROM answers ORDER BY computed_at, key"
                ).fetchall()
                for key, size in oldest:
                    if freed >= excess:
                        break
                    doomed.append((key,))
                    freed += size
                connection.executemany("DELETE FROM answers WHERE key = ?", doomed)
                connection.commit()
                self._entries -= len(doomed)
                self._stored_bytes -= freed
        except (OSError, sqlite3.Error) as error:
            _discard(connection)
            logger.warning("could not prune the cache: %s", error)
            return
        logger.info("cache pruned %d entries to stay under %d bytes", len(doomed), self._max_bytes)

    def close(self) -> None:
        """Close every connection this store opened, and refuse to open another.

        Called on shutdown. Afterwards the store is a no-op rather than an error, because a request
        already in flight when the process is told to stop must still be answered.
        """
        with self._lock:
            self._closed = True
            connections, self._connections = self._connections, []
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error as error:
                logger.warning("could not close a cache connection: %s", error)

    def _connection(self) -> sqlite3.Connection:
        """Answer with this thread's connection, opening one on first use.

        Returns
        -------
        sqlite3.Connection
            The connection belonging to the calling thread.

        Notes
        -----
        A connection that failed to open is kept nowhere, so the next call on this thread tries
        again rather than answering every request as a miss until the process restarts.
        """
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open()
            self._local.connection = connection
        return connection

    def _open(self) -> sqlite3.Connection:
        """Open one connection, create the file if it is new, and remember it for :meth:`close`.

        Returns
        -------
        sqlite3.Connection
            A connection whose journal is WAL and whose commits are `NORMAL` — durable against a
            crash of this process, which is the only crash a cache has to survive.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # `check_same_thread=False` because the connections are closed from the shutdown thread,
        # not from the thread that opened them. No two threads ever use one connection: this store
        # hands each thread its own, and only `close` reaches across threads, after serving stopped.
        connection = sqlite3.connect(self._path, check_same_thread=False)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(_SCHEMA)
            connection.commit()
        except sqlite3.Error:
            connection.close()
            raise
        with self._lock:
            self._connections.append(connection)
        return connection

    def _count(self, *, hit: bool) -> None:
        """Record one lookup, so :meth:`stats` can report what the cache is worth."""
        with self._lock:
            if hit:
                self._hits += 1
            else:
                self._misses += 1

    def _count_write(self) -> int:
        """Record one stored answer and answer how many this process has written.

        Returns
        -------
        int
            The number of writes so far, counting this one — the modulus that triggers a prune.
        """
        with self._lock:
            self._writes += 1
            return self._writes


def _discard(connection: sqlite3.Connection | None) -> None:
    """Abandon a write whose commit did not succeed, so a later commit cannot store it uncounted.

    Parameters
    ----------
    connection : sqlite3.Connection or None
        The connection the failed write used, or `None` when the connection itself could not be
        obtained and there is nothing to abandon.

    Notes
    -----
    SQLite leaves the transaction open when a commit fails, so without this the next statement on
    the same thread joins that transaction and its commit stores both rows while the counts kept
    beside them describe one. A best-effort rollback: it is the failure path already, and a cache
    that cannot roll back still has to answer.
    """
    if connection is None:
        return
    try:
        connection.rollback()
    except sqlite3.Error as error:
        logger.warning("could not roll back a cache write: %s", error)


def _switched_off() -> bool:
    """Say whether the host switched the cache off through :data:`CACHE_ENV`.

    Returns
    -------
    bool
        `True` only for the spellings in :data:`_OFF`. Anything else, including an empty value,
        leaves the cache on: the switch has to be deliberate to take effect.
    """
    return os.environ.get(CACHE_ENV, "").strip().lower() in _OFF


def _env_max_bytes() -> int:
    """Read the cap from :data:`CACHE_MAX_BYTES_ENV`, falling back to the default.

    Returns
    -------
    int
        The configured cap, or :data:`DEFAULT_CACHE_MAX_BYTES` when the variable is unset, is not a
        number, or is not positive — a host with a typo serves with the default cap rather than
        pruning the cache away after every answer.
    """
    raw = os.environ.get(CACHE_MAX_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_CACHE_MAX_BYTES
    try:
        cap = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %d", CACHE_MAX_BYTES_ENV, raw, DEFAULT_CACHE_MAX_BYTES
        )
        return DEFAULT_CACHE_MAX_BYTES
    if cap <= 0:
        logger.warning(
            "%s=%r is not positive; using %d", CACHE_MAX_BYTES_ENV, raw, DEFAULT_CACHE_MAX_BYTES
        )
        return DEFAULT_CACHE_MAX_BYTES
    return cap
