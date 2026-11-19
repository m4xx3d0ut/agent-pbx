"""Durable selection and admission for the single WorkerBee backend owner."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
import asyncio
from contextlib import closing, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import re
import sqlite3
import stat
import threading
import time
from typing import Any

from .workerbee_runtime_identity import (
    WorkerBeeRuntimeSelection,
    decode_runtime_selection,
    make_runtime_selection,
)

DB_NAME = "cutover-v1.sqlite"
_CURRENT_OPERATION: ContextVar[WorkerBeeOperation | None] = ContextVar(
    "workerbee_transport_operation", default=None,
)


def current_workerbee_operation():
    return _CURRENT_OPERATION.get()


async def wait_workerbee_thread(func, /, *args, **kwargs):
    """Keep an admitted reservation until an offloaded operation really exits."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    break
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


class WorkerBeeCutoverError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self), "remediation": ""}


class WorkerBeeCutoverDraining(WorkerBeeCutoverError):
    """Admission was refused by a transient drain in this owner epoch."""

    def __init__(self, epoch: int) -> None:
        super().__init__("WORKERBEE_CUTOVER_CLOSED", "WorkerBee backend admission is closed")
        self.epoch = epoch


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _selection(value: str | None) -> WorkerBeeRuntimeSelection | None:
    return None if value is None else decode_runtime_selection(json.loads(value))


def read_durable_selection(path: Path) -> WorkerBeeRuntimeSelection | None:
    """Diagnostics can inspect the committed selection without acquiring the writer."""
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        row = db.execute("SELECT selected FROM control WHERE singleton=1").fetchone()
    if row is None:
        raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_STATE_INVALID", "Selection row missing")
    return _selection(row[0])


@dataclass(slots=True)
class WorkerBeeOperation:
    operation_id: str
    runtime: WorkerBeeRuntimeSelection | None
    kind: str
    lease_id: str = ""
    binding_id: str = ""
    transport_identity: str = ""
    uncertain: bool = False
    closed: bool = False

    def dispatch(self) -> None:
        self.uncertain = True

    def terminal(self) -> None:
        self.uncertain = False

    def verify_connection(self, result: Any) -> None:
        server = getattr(result, "serverInfo", None)
        meta = getattr(result, "meta", None) or {}
        if (
            self.closed or self.runtime is None
            or (self.runtime.server_name and getattr(server, "name", None) != self.runtime.server_name)
            or (self.runtime.server_version
                and getattr(server, "version", None) != self.runtime.server_version)
            or (self.transport_identity
                and meta.get("workerbee_transport_identity_sha256") != self.transport_identity)
        ):
            raise WorkerBeeCutoverError(
                "WORKERBEE_TRANSPORT_DRIFT", "Connected WorkerBee identity differs from the admitted selection"
            )


class WorkerBeeCutover:
    """One lifetime advisory owner and short SQLite transactions.

    Local state must reside on a filesystem with reliable flock and SQLite
    locks. A competing daemon is rejected at startup, even for the same user.
    Unresolved operation intents survive process loss.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self.path = self.root / DB_NAME
        self._owner_fd: int | None = None
        self._epoch: int | None = None
        self._serving = False
        self._active = 0
        self._controller_active = False
        self._condition = threading.Condition(threading.RLock())
        self._open_waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()
        self._operation: ContextVar[WorkerBeeOperation | None] = ContextVar(
            "workerbee_cutover_operation", default=None
        )

    @staticmethod
    def _owned_file(path: Path, *, create: bool = False) -> int:
        flags = os.O_CLOEXEC | os.O_NOFOLLOW
        flags |= os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
        ):
            os.close(fd)
            raise WorkerBeeCutoverError(
                "WORKERBEE_CUTOVER_UNSAFE_STATE", "Cutover state file is not owner-private"
            )
        return fd

    def start(self) -> None:
        with self._condition:
            if self._owner_fd is not None:
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_OWNER_BUSY", "Cutover owner already started"
                )
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = self.root.lstat()
            if (
                self.root.resolve(strict=True) != self.root or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_UNSAFE_STATE", "Cutover root is not canonical and private"
                )
            fd = self._owned_file(self.root / "cutover.owner", create=True)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise WorkerBeeCutoverError(
                        "WORKERBEE_CUTOVER_OWNER_BUSY", "Another daemon owns WorkerBee cutover state"
                    ) from exc
                os.close(self._owned_file(self.path, create=True))
                with self._connection(write=True, owner=False) as db:
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1):
                        raise WorkerBeeCutoverError(
                            "WORKERBEE_CUTOVER_STATE_INVALID", "Unsupported cutover schema"
                        )
                    if version == 0:
                        self._initialize(db)
                    row = self._control(db)
                    _selection(row["selected"])
                    self._epoch = row["epoch"] + 1
                    db.execute("UPDATE control SET epoch=? WHERE singleton=1", (self._epoch,))
                    if db.execute("SELECT 1 FROM admitted LIMIT 1").fetchone():
                        db.execute(
                            "UPDATE control SET gate='recovery_required', reason='orphaned_operations' "
                            "WHERE singleton=1"
                        )
                self._owner_fd = fd
            except BaseException:
                self._epoch = None
                os.close(fd)
                raise

    def _initialize(self, db: sqlite3.Connection) -> None:
        db.execute(
            "CREATE TABLE control (singleton INTEGER PRIMARY KEY CHECK (singleton=1), "
            "epoch INTEGER NOT NULL, gate TEXT NOT NULL, reason TEXT NOT NULL, "
            "selected TEXT, transaction_id TEXT, publication TEXT)"
        )
        db.execute(
            "CREATE TABLE switch_journal (transaction_id TEXT PRIMARY KEY, controller TEXT "
            "NOT NULL, phase TEXT NOT NULL, record TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE admitted (operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "epoch INTEGER NOT NULL, identity TEXT NOT NULL)"
        )
        try:
            fd = self._owned_file(self.root / "selected-runtime.json")
        except FileNotFoundError:
            legacy = None
        else:
            with os.fdopen(fd, encoding="utf-8") as file:
                data = file.read(65537)
            if len(data) > 65536:
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_STATE_INVALID", "Legacy selection exceeds size bound"
                )
            legacy = decode_runtime_selection(json.loads(data))
        db.execute(
            "INSERT INTO control VALUES(1, 0, 'open', '', ?, NULL, NULL)",
            (_json(legacy.public_dict()) if legacy else None,),
        )
        db.execute("PRAGMA user_version=1")

    @contextmanager
    def _connection(self, *, write: bool = False, owner: bool = True) -> Iterator[sqlite3.Connection]:
        if owner and self._owner_fd is None:
            raise WorkerBeeCutoverError(
                "WORKERBEE_CUTOVER_NOT_STARTED", "WorkerBee cutover owner is not initialized"
            )
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                if owner and self._control(db)["epoch"] != self._epoch:
                    raise WorkerBeeCutoverError(
                        "WORKERBEE_CUTOVER_FENCED", "WorkerBee owner epoch changed"
                    )
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _control(db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute("SELECT * FROM control WHERE singleton=1").fetchone()
        if row is None:
            raise WorkerBeeCutoverError(
                "WORKERBEE_CUTOVER_STATE_INVALID", "Selection row missing"
            )
        return row

    def enable_serving(self) -> None:
        """Called only after startup recovery/readback, including a fenced hold."""
        with self._condition:
            if self._owner_fd is None:
                raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_NOT_STARTED", "No cutover owner")
            self._serving = True

    def stop(self) -> None:
        with self._condition:
            self._serving = False
            self._wake_open_waiters()
            if self._active or self._controller_active:
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_OWNER_BUSY", "Admitted operations are still running"
                )
            fd, self._owner_fd = self._owner_fd, None
            self._epoch = None
            if fd is not None:
                os.close(fd)

    def _wake_open_waiters(self) -> None:
        for loop, event in self._open_waiters:
            with suppress(RuntimeError):  # An event loop may already be shutting down.
                loop.call_soon_threadsafe(event.set)

    @contextmanager
    def _gate_write(self, *, notify: bool = True) -> Iterator[sqlite3.Connection]:
        with self._condition:
            with self._connection(write=True) as db:
                yield db
            if notify:
                self._wake_open_waiters()

    async def wait_for_reopen(self, *, epoch: int, timeout: float) -> bool:
        """Wait outside admission for a committed open gate in the same daemon epoch."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        event = asyncio.Event()
        waiter = (loop, event)
        with self._condition:
            self._open_waiters.add(waiter)
        try:
            while True:
                with self._condition:
                    if not self._serving or self._epoch != epoch:
                        return False
                    with self._connection() as db:
                        gate = self._control(db)["gate"]
                    if gate != "draining":
                        return gate == "open"
                    event.clear()
                await asyncio.wait_for(event.wait(), timeout=deadline - loop.time())
        except asyncio.TimeoutError:
            return False
        finally:
            with self._condition:
                self._open_waiters.discard(waiter)

    def authorize_dispatch(self, *, effect: bool) -> None:
        """Allow an admitted run to finish against the selection being drained."""
        operation = self._operation.get()
        with self._condition, self._connection() as db:
            state = self._control(db)
            if (
                not self._serving or state["gate"] not in {"open", "draining"}
                or operation is None or operation.closed or operation.kind != "controlled_run_execute"
                or operation.runtime != _selection(state["selected"])
            ):
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_DISPATCH_HELD",
                    "The admitted WorkerBee operation can no longer dispatch on its pinned selection.",
                )
            if effect:
                operation.dispatch()

    @contextmanager
    def controller(self) -> Iterator[None]:
        with self._condition:
            if self._owner_fd is None or self._controller_active:
                raise WorkerBeeCutoverError("WORKERBEE_SWITCH_BUSY", "Switch controller is unavailable")
            self._controller_active = True
        try:
            yield
        finally:
            with self._condition:
                self._controller_active = False

    def selected(self) -> WorkerBeeRuntimeSelection | None:
        operation = self._operation.get()
        if operation is not None:
            return operation.runtime
        if not self.path.exists():
            return None
        return read_durable_selection(self.path)

    def posture(self) -> dict[str, Any]:
        if self._owner_fd is None:
            return {"admission": "startup", "reason": "owner_not_started", "admitted": 0}
        with self._connection(owner=False) as db:
            row = self._control(db)
            return {
                "admission": row["gate"],
                "reason": row["reason"],
                "transaction_id": row["transaction_id"],
                "admitted": db.execute("SELECT COUNT(*) FROM admitted").fetchone()[0],
            }

    def observe(self, provenance: Mapping[str, Any], *, reason: str) -> WorkerBeeRuntimeSelection:
        observed = make_runtime_selection(provenance, reason=reason)
        drift = False
        with self._gate_write() as db:
            row = self._control(db)
            prior = _selection(row["selected"])
            if row["gate"] != "open":
                raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_CLOSED", "Admission is closed")
            if prior is None:
                db.execute("UPDATE control SET selected=? WHERE singleton=1", (_json(observed.public_dict()),))
            elif prior.fingerprint != observed.fingerprint:
                db.execute(
                    "UPDATE control SET gate='recovery_required', reason='external_identity_drift' "
                    "WHERE singleton=1"
                )
                drift = True
            else:
                observed = prior
        if drift:
            raise WorkerBeeCutoverError(
                "WORKERBEE_BACKEND_DRIFT", "Observed backend differs from committed selection"
            )
        return observed

    @contextmanager
    def operation(
        self, kind: str, *, require_runtime: bool = True, lease_id: str = "", binding_id: str = "",
        inherit: bool = True, expected_epoch: int | None = None,
    ) -> Iterator[WorkerBeeOperation]:
        previous = self._operation.get()
        if previous is not None and inherit:
            if previous.closed:
                raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_FENCED", "Operation reservation has ended")
            if require_runtime and previous.runtime is None:
                raise WorkerBeeCutoverError(
                    "WORKERBEE_RUNTIME_UNSELECTED", "No verified WorkerBee runtime is selected"
                )
            if (lease_id and previous.lease_id and lease_id != previous.lease_id) or (
                binding_id and previous.binding_id and binding_id != previous.binding_id
            ):
                raise WorkerBeeCutoverError(
                    "WORKERBEE_CUTOVER_PIN_MISMATCH", "Work differs from the admitted lease or binding"
                )
            yield previous
            return
        with self._condition:
            with self._connection(write=True) as db:
                row = self._control(db)
                if expected_epoch is not None and row["epoch"] != expected_epoch:
                    raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_FENCED", "WorkerBee owner changed")
                if self._serving and row["gate"] == "draining":
                    raise WorkerBeeCutoverDraining(row["epoch"])
                if not self._serving or row["gate"] != "open":
                    raise WorkerBeeCutoverError(
                        "WORKERBEE_CUTOVER_CLOSED", "WorkerBee backend admission is closed"
                    )
                runtime = _selection(row["selected"])
                if runtime is None and require_runtime:
                    raise WorkerBeeCutoverError(
                        "WORKERBEE_RUNTIME_UNSELECTED", "No verified WorkerBee runtime is selected"
                    )
                publication = json.loads(row["publication"]) if row["publication"] else {}
                item = WorkerBeeOperation(
                    secrets.token_hex(16), runtime, kind, lease_id, binding_id,
                    publication.get("transport_identity_sha256", ""),
                )
                db.execute(
                    "INSERT INTO admitted VALUES (?, ?, ?, ?)",
                    (item.operation_id, kind, self._epoch, _json({
                        "runtime": runtime.public_dict() if runtime else None,
                        "lease_id": lease_id,
                        "binding_id": binding_id,
                        "transport_identity_sha256": item.transport_identity,
                    })),
                )
            self._active += 1
        token = self._operation.set(item)
        transport_token = _CURRENT_OPERATION.set(item)
        try:
            yield item
        finally:
            item.closed = True
            _CURRENT_OPERATION.reset(transport_token)
            self._operation.reset(token)
            with self._condition:
                try:
                    with self._gate_write(notify=item.uncertain) as db:
                        if item.uncertain:
                            db.execute(
                                "UPDATE control SET gate='recovery_required', reason='uncertain_operation' "
                                "WHERE singleton=1"
                            )
                        else:
                            db.execute("DELETE FROM admitted WHERE operation_id=?", (item.operation_id,))
                finally:
                    self._active -= 1
                    self._condition.notify_all()

    def pin_workspace(
        self, lease_id: str, binding_id: str, path_domain_sha256: str,
        *, replacing_binding_id: str = "",
    ) -> None:
        operation = self._operation.get()
        if operation is None or (
            operation.lease_id not in {"", lease_id}
            or operation.binding_id not in {"", binding_id, replacing_binding_id}
            or not re.fullmatch(r"[0-9a-f]{64}", path_domain_sha256)
        ):
            raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_PIN_MISMATCH", "Workspace evidence changed")
        with self._connection(write=True) as db:
            row = db.execute(
                "SELECT identity FROM admitted WHERE operation_id=? AND epoch=?",
                (operation.operation_id, self._epoch),
            ).fetchone()
            if row is None:
                raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_FENCED", "Workspace reservation has ended")
            identity = json.loads(row["identity"])
            prior_digest = identity.get("path_domain_receipt_sha256")
            if prior_digest and prior_digest != path_domain_sha256 and not (
                replacing_binding_id and replacing_binding_id != binding_id
            ):
                raise WorkerBeeCutoverError("WORKERBEE_CUTOVER_PIN_MISMATCH", "Workspace receipt changed")
            identity.update(
                lease_id=lease_id, binding_id=binding_id,
                path_domain_receipt_sha256=path_domain_sha256,
            )
            db.execute(
                "UPDATE admitted SET identity=? WHERE operation_id=?",
                (_json(identity), operation.operation_id),
            )
        operation.lease_id = lease_id
        operation.binding_id = binding_id

    def current_operation(self) -> WorkerBeeOperation | None:
        return self._operation.get()

    def drain(self, transaction_id: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                with self._connection() as db:
                    state = self._control(db)
                    if state["transaction_id"] != transaction_id or state["gate"] != "draining":
                        return False
                    if not db.execute("SELECT 1 FROM admitted LIMIT 1").fetchone():
                        return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)

    def preview(self, transaction_id: str, controller: str, record: dict[str, Any]) -> None:
        with self._connection(write=True) as db:
            row = self._control(db)
            selection = _selection(row["selected"])
            if row["gate"] != "open" or selection is None or selection.public_dict() != record["source"]:
                raise WorkerBeeCutoverError("WORKERBEE_SWITCH_STALE", "Selection changed during preview")
            db.execute(
                "INSERT INTO switch_journal VALUES (?, ?, 'previewed', ?)",
                (transaction_id, controller, _json(record)),
            )

    @staticmethod
    def _journal(db: sqlite3.Connection, transaction_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM switch_journal WHERE transaction_id=?", (transaction_id,),
        ).fetchone()
        if row is None:
            raise WorkerBeeCutoverError("WORKERBEE_SWITCH_UNKNOWN", "Unknown switch transaction")
        return row

    def journal(self, transaction_id: str, *, controller: str | None = None) -> dict[str, Any]:
        with self._connection() as db:
            row = self._journal(db, transaction_id)
            if controller is not None and row["controller"] != controller:
                raise WorkerBeeCutoverError("WORKERBEE_SWITCH_OWNER_MISMATCH", "Switch controller differs")
            return {**json.loads(row["record"]), "phase": row["phase"]}

    def publication(self) -> dict[str, Any] | None:
        with self._connection() as db:
            raw = self._control(db)["publication"]
            return json.loads(raw) if raw else None

    def hold(self, reason: str) -> None:
        with self._gate_write() as db:
            db.execute(
                "UPDATE control SET gate='recovery_required', reason=? WHERE singleton=1", (reason,),
            )

    def latest(self) -> dict[str, Any] | None:
        with self._connection(owner=False) as db:
            row = db.execute(
                "SELECT transaction_id, phase FROM switch_journal ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def claim(
        self, transaction_id: str, controller: str, *, phase: str,
        selection: WorkerBeeRuntimeSelection,
    ) -> None:
        with self._condition, self._connection(write=True) as db:
            state = self._control(db)
            journal = self._journal(db, transaction_id)
            if (
                not self._serving or state["gate"] != "open"
                or state["transaction_id"] is not None
                or journal["controller"] != controller or journal["phase"] != phase
                or _selection(state["selected"]) != selection
            ):
                raise WorkerBeeCutoverError(
                    "WORKERBEE_SWITCH_STALE", "Switch ownership, phase, or committed selection changed"
                )
            db.execute(
                "UPDATE control SET gate='draining', reason='', transaction_id=? WHERE singleton=1",
                (transaction_id,),
            )
            next_phase = "draining" if phase == "previewed" else "rollback_draining"
            db.execute(
                "UPDATE switch_journal SET phase=? WHERE transaction_id=?", (next_phase, transaction_id),
            )

    def advance(
        self, transaction_id: str, expected_phase: str, phase: str, record: dict[str, Any],
        *, reopen: bool = False, hold: str = "",
        publish: WorkerBeeRuntimeSelection | None = None, transport_identity: str = "",
    ) -> None:
        """Compare state and phase under the same transaction as journal/publication."""
        with self._gate_write(notify=reopen or bool(hold)) as db:
            state = self._control(db)
            journal = self._journal(db, transaction_id)
            if state["transaction_id"] != transaction_id or journal["phase"] != expected_phase:
                raise WorkerBeeCutoverError("WORKERBEE_SWITCH_FENCED", "Switch journal no longer owns admission")
            if state["gate"] == "recovery_required" and not hold and not (
                state["reason"] == "switch_verification_or_recovery_required"
                and expected_phase in {"verify_target", "verify_restore", "published", "restore_published"}
            ):
                raise WorkerBeeCutoverError("WORKERBEE_SWITCH_RECOVERY_REQUIRED", "Uncertain admitted operation")
            if not hold and db.execute("SELECT 1 FROM admitted LIMIT 1").fetchone():
                # Timeout may release the old, unchanged gate while known operations finish.
                if not (reopen and phase == "drain_timeout" and self._active):
                    raise WorkerBeeCutoverError("WORKERBEE_SWITCH_NOT_DRAINED", "Switch has undrained work")
            if publish is not None:
                old = _selection(state["selected"])
                if old is None or publish.generation != old.generation + 1:
                    raise WorkerBeeCutoverError("WORKERBEE_SWITCH_FENCED", "Publication generation changed")
                db.execute(
                    "UPDATE control SET selected=?, publication=? WHERE singleton=1",
                    (_json(publish.public_dict()), _json({
                        "transaction_id": transaction_id,
                        "generation": publish.generation,
                        "transport_identity_sha256": transport_identity,
                    })),
                )
            db.execute(
                "UPDATE switch_journal SET phase=?, record=? WHERE transaction_id=?",
                (phase, _json(record), transaction_id),
            )
            if reopen or hold:
                db.execute(
                    "UPDATE control SET gate=?, reason=?, transaction_id=? WHERE singleton=1",
                    ("open" if reopen else "recovery_required", hold, None if reopen else transaction_id),
                )
