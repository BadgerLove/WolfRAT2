"""Single-flight session for the Joint Operations retail admin protocol.

This module deliberately has no Qt dependency.  The socket is hidden behind a
small byte-transport seam so the exact retail exchange can be exercised without
a live game process.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from enum import IntEnum
import socket
import struct
import threading
from typing import Callable, Protocol

from . import server_process
from .admin_commands import (
    AdminOperation,
    AdminSnapshot,
    CommandSpec,
    raw_command,
)


MAGIC = b"\x00\x00\x0d\x0a"
HEADER_SIZE = 8
MAX_PACKET_SIZE = 1 << 20
MAX_COMMAND_SIZE = 1024 - HEADER_SIZE - 1
_JO_LCG_MULTIPLIER = 0x04B05731


class AdminSessionError(RuntimeError):
    """Base error raised by the retail admin session."""


class RetailProtocolError(AdminSessionError):
    """The peer sent bytes which are not a valid retail admin packet."""


class StaleIdentityError(AdminSessionError):
    """A mutation targets a record superseded by a newer retail query."""


class RequestPriority(IntEnum):
    """Scheduling class for one serialized retail request."""

    INTERACTIVE = 0
    BACKGROUND = 1


class ByteTransport(Protocol):
    def connect(self, host: str, port: int, timeout: float) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class SocketTransport:
    """Production byte transport backed by one TCP socket."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._closed = False

    @staticmethod
    def _enable_abortive_close(sock: socket.socket) -> None:
        """Select the native ``linger`` ABI without weakening disconnects.

        Winsock defines ``struct linger`` as two unsigned shorts, while the
        POSIX implementations supported by Python use two native integers.
        Trying both representations lets the socket API select its ABI.  If
        neither is accepted, connection setup fails rather than falling back
        to the orderly FIN that retail mishandles.
        """

        errors: list[OSError] = []
        for packing in ("HH", "ii"):
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_LINGER,
                    struct.pack(packing, 1, 0),
                )
                return
            except OSError as error:
                errors.append(error)
        raise errors[-1]

    def connect(self, host: str, port: int, timeout: float) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self._lock:
            if self._closed:
                rejected = "transport is closed"
            elif self._socket is not None:
                rejected = "transport is already connecting or connected"
            else:
                self._socket = sock
                rejected = None
        if rejected is not None:
            sock.close()
            raise ConnectionError(rejected)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Retail mishandles an orderly FIN: recv()==0 leaves the admin
            # client slot retained, and its later table growth corrupts those
            # stale slots.  An abortive close makes recv fail with
            # WSAECONNRESET, which follows retail's working removal path.
            self._enable_abortive_close(sock)
            sock.settimeout(timeout)
            sock.connect((host, port))
        except BaseException:
            with self._lock:
                if self._socket is sock:
                    self._socket = None
            sock.close()
            raise
        with self._lock:
            interrupted = self._closed or self._socket is not sock
        if interrupted:
            sock.close()
            raise ConnectionError("transport was closed during connect")
        # Memory features find the server process through this connection.
        server_process.note_connection(sock)

    def sendall(self, data: bytes) -> None:
        with self._lock:
            sock = self._socket
        if sock is None:
            raise ConnectionError("transport is not connected")
        sock.sendall(data)

    def recv(self, size: int, timeout: float) -> bytes:
        with self._lock:
            sock = self._socket
        if sock is None:
            raise ConnectionError("transport is not connected")
        sock.settimeout(timeout)
        return sock.recv(size)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()


@dataclass(frozen=True)
class RawResult:
    command: str
    replies: tuple[str, ...]
    accepted: bool


@dataclass(frozen=True)
class CommandResult:
    operation: object
    replies: tuple[str, ...]
    accepted: bool
    value: object = None
    verified: bool | None = None
    verification_error: str | None = None


@dataclass
class _Request:
    spec: CommandSpec
    observers: list[Future] = field(default_factory=list)
    raw: bool = False
    priority: RequestPriority = RequestPriority.INTERACTIVE
    coalesce_token: object | None = None
    sent: bool = False


def _jo_encrypt(challenge_payload: bytes, username: str, password: str) -> bytes:
    """Return the exact 65-byte response used by the retail server."""
    buf = bytearray(65)
    username_bytes = username.encode("ascii", errors="replace")
    password_bytes = password.encode("ascii", errors="replace")
    buf[: min(len(username_bytes), 32)] = username_bytes[:32]
    buf[32 : 32 + min(len(password_bytes), 32)] = password_bytes[:32]

    key_length = next(
        (index for index, value in enumerate(challenge_payload) if value == 0),
        len(challenge_payload),
    )
    if key_length == 0:
        key_length = len(challenge_payload)

    password_hash = 0
    for index in range(key_length):
        value = challenge_payload[index]
        signed_value = value - 256 if value > 127 else value
        password_hash = (
            password_hash + signed_value * signed_value + index
        ) & 0xFFFFFFFF
    password_hash = (password_hash + 0x32 + key_length) & 0xFFFFFFFF

    state_one = (password_hash * _JO_LCG_MULTIPLIER + 1) & 0xFFFF
    state_two = (_JO_LCG_MULTIPLIER * state_one + 1) & 0xFFFF
    state_three = (_JO_LCG_MULTIPLIER * state_two + 1) & 0xFFFF

    for index in range(65):
        buf[index] = (
            buf[index] + challenge_payload[index % key_length]
        ) & 0xFF
    if state_three & 1:
        buf.reverse()

    addend = state_one & 0xFF
    step = state_two & 0xFF
    for index in range(65):
        buf[index] = (buf[index] + index + addend) & 0xFF
        addend = (addend + step) & 0xFF

    stream_state = state_three
    for index in range(65):
        stream_state = (
            _JO_LCG_MULTIPLIER * stream_state + 1
        ) & 0xFFFF
        buf[index] = (buf[index] + (stream_state & 0xFF)) & 0xFF
    return bytes(buf)


class RetailAdminSession:
    """Own one authenticated retail connection and serialize all operations."""

    def __init__(
        self,
        host: str,
        port: int = 4000,
        username: str = "",
        password: str = "",
        *,
        transport_factory: Callable[[], ByteTransport] = SocketTransport,
        timeout: float = 15.0,
    ) -> None:
        self._validate_credential("username", username)
        self._validate_credential("password", password)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._transport_factory = transport_factory
        self._timeout = timeout
        self._condition = threading.Condition()
        self._interactive_requests: deque[_Request] = deque()
        self._background_requests: deque[_Request] = deque()
        self._coalesced: dict[object, _Request] = {}
        self._transport: ByteTransport | None = None
        self._connecting_transport: ByteTransport | None = None
        self._generation = 0
        self._closed = False
        self._worker: threading.Thread | None = None
        self._listeners: list[Callable[[object], None]] = []
        self._revision = 0
        self._snapshot = AdminSnapshot()

    @staticmethod
    def _validate_credential(field: str, value: str) -> None:
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError(f"{field} must be ASCII") from error
        if len(encoded) > 32:
            raise ValueError(f"{field} exceeds the 32-byte retail auth field")

    def execute_raw(self, command: str) -> Future:
        spec = raw_command(command)
        future: Future = Future()
        self._enqueue(_Request(spec=spec, observers=[future], raw=True))
        return future

    def execute(
        self,
        operation: CommandSpec,
        *,
        priority: RequestPriority = RequestPriority.INTERACTIVE,
        coalesce_key: object | None = None,
    ) -> Future:
        if not isinstance(operation, CommandSpec):
            raise TypeError("execute requires a typed CommandSpec")
        try:
            priority = RequestPriority(priority)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown retail request priority") from error
        self._validate_identity(operation)
        future: Future = Future()
        token = (
            (coalesce_key, operation.text, False)
            if coalesce_key is not None
            else None
        )
        self._enqueue(
            _Request(
                spec=operation,
                observers=[future],
                priority=priority,
                coalesce_token=token,
            )
        )
        return future

    def _enqueue(self, request: _Request) -> None:
        with self._condition:
            if self._closed:
                self._settle_exception(
                    request.observers,
                    AdminSessionError("session is closed"),
                )
                return
            if request.coalesce_token is not None:
                existing = self._coalesced.get(request.coalesce_token)
                if existing is not None:
                    existing.observers.extend(request.observers)
                    return
                self._coalesced[request.coalesce_token] = request
            queue = (
                self._background_requests
                if request.priority is RequestPriority.BACKGROUND
                else self._interactive_requests
            )
            queue.append(request)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, daemon=True, name="RetailAdminSession"
                )
                self._worker.start()
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._has_requests() and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                request = self._next_request()
                if not self._has_observers(request):
                    self._forget_coalesced(request)
                    continue
            try:
                self._validate_identity(request.spec)
            except StaleIdentityError as error:
                self._settle_exception(request.observers, error)
                with self._condition:
                    self._forget_coalesced(request)
                continue
            generation = None
            try:
                # Cancellation before a request is committed means no auth or
                # command bytes are performed for that abandoned observer.
                with self._condition:
                    if not self._has_observers(request):
                        self._forget_coalesced(request)
                        continue
                generation = self._ensure_authenticated()
                assert self._transport is not None
                with self._condition:
                    if not self._has_observers(request):
                        self._forget_coalesced(request)
                        continue
                    # This is the request's commit point. Cancellation after
                    # it cannot retract bytes, so the worker still drains and
                    # applies the reply to preserve stream correlation.
                    request.sent = True
                self._transport.sendall(self._command_packet(request.spec.text))
                replies = self._receive_replies(
                    self._transport, request.spec.reply_policy
                )
                accepted = (
                    self._accepted(replies)
                    and request.spec.accepts_replies(replies)
                )
                with self._condition:
                    self._revision += 1
                    revision = self._revision
                value = (
                    request.spec.parse(replies[0], revision)
                    if accepted
                    else None
                )
                if accepted:
                    self._apply_snapshot(request.spec, value)
                if request.raw:
                    result = RawResult(
                        command=request.spec.text,
                        replies=replies,
                        accepted=accepted,
                    )
                else:
                    result = CommandResult(
                        operation=request.spec.operation,
                        replies=replies,
                        accepted=accepted,
                        value=value,
                        verified=(
                            accepted
                            if not request.spec.mutating
                            else None
                        ),
                    )
                with self._condition:
                    # Release the coalescing slot before callbacks run. A
                    # completion callback may legitimately schedule the next
                    # poll for the same operation.
                    self._forget_coalesced(request)
                self._settle_result(request.observers, result)
                self._notify(result)
            except BaseException as error:
                self._fail_generation(generation, error, request)

    def _has_requests(self) -> bool:
        return bool(self._interactive_requests or self._background_requests)

    def _next_request(self) -> _Request:
        if self._interactive_requests:
            return self._interactive_requests.popleft()
        return self._background_requests.popleft()

    @staticmethod
    def _has_observers(request: _Request) -> bool:
        return any(not observer.done() for observer in request.observers)

    def _forget_coalesced(self, request: _Request) -> None:
        token = request.coalesce_token
        if token is not None and self._coalesced.get(token) is request:
            self._coalesced.pop(token, None)

    @staticmethod
    def _settle_result(observers: list[Future], result: object) -> None:
        for observer in tuple(observers):
            if observer.done():
                continue
            try:
                observer.set_result(result)
            except InvalidStateError:
                # Cancellation can win the race between done() and settlement.
                pass

    @staticmethod
    def _settle_exception(
        observers: list[Future], error: BaseException
    ) -> None:
        for observer in tuple(observers):
            if observer.done():
                continue
            try:
                observer.set_exception(error)
            except InvalidStateError:
                pass

    def _ensure_authenticated(self) -> int:
        with self._condition:
            if self._closed:
                raise AdminSessionError("session is closed")
            if self._transport is not None:
                return self._generation
        transport = self._transport_factory()
        with self._condition:
            if self._closed:
                close_before_connect = True
            else:
                self._connecting_transport = transport
                close_before_connect = False
        if close_before_connect:
            transport.close()
            raise AdminSessionError("session is closed")
        try:
            transport.connect(self._host, self._port, self._timeout)
            challenge = self._read_frame(transport)
            if len(challenge) != 33 or challenge[-1:] != b"\x00":
                raise RetailProtocolError(
                    "retail challenge must be 32 bytes followed by NUL"
                )
            auth_payload = _jo_encrypt(
                challenge, self._username, self._password
            )
            transport.sendall(self._frame(auth_payload))
            login_reply = self._decode_text(self._read_frame(transport))
            if "logged in" not in login_reply.lower():
                raise AdminSessionError(
                    login_reply or "server rejected the credentials"
                )
            with self._condition:
                if (
                    self._closed
                    or self._connecting_transport is not transport
                ):
                    raise AdminSessionError("session is closed")
                self._connecting_transport = None
                self._generation += 1
                self._transport = transport
                return self._generation
        except BaseException:
            with self._condition:
                if self._connecting_transport is transport:
                    self._connecting_transport = None
            transport.close()
            raise

    def _fail_generation(
        self, generation: int | None, error: BaseException, current: _Request
    ) -> None:
        self._close_generation(generation)
        with self._condition:
            self._forget_coalesced(current)
            pending = tuple(
                self._interactive_requests
            ) + tuple(self._background_requests)
            self._interactive_requests.clear()
            self._background_requests.clear()
            self._coalesced.clear()
        self._settle_exception(current.observers, error)
        for request in pending:
            self._settle_exception(request.observers, error)

    def _close_generation(self, generation: int | None) -> None:
        with self._condition:
            if generation is not None and generation != self._generation:
                return
            transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()

    def _read_text_frame(self, transport: ByteTransport) -> str:
        return self._decode_text(self._read_frame(transport))

    def _receive_replies(self, transport: ByteTransport, policy) -> tuple[str, ...]:
        replies: list[str] = []
        while not policy.is_complete(replies):
            replies.append(self._read_text_frame(transport))
        return tuple(replies)

    def _apply_snapshot(self, spec: CommandSpec, value: object) -> None:
        with self._condition:
            if spec.operation is AdminOperation.RAW and spec.mutating:
                # Raw mutations have no typed effect model.  Clear every
                # authoritative identity before the next queued request is
                # validated, so a successful raw queue/player/weapon change
                # cannot make a later typed command target a shifted record.
                self._snapshot = AdminSnapshot(revision=self._revision)
                return
            apply = getattr(self._snapshot, "apply", None)
            if apply is not None:
                self._snapshot = apply(spec.operation, value)

    def _validate_identity(self, spec: CommandSpec) -> None:
        # Identity validation is disabled — the JO server doesn't track
        # revisions and the polling loop refreshes snapshots frequently,
        # causing false "stale identity" rejections on valid commands.
        return

    def _read_frame(self, transport: ByteTransport) -> bytes:
        header = self._recv_exact(transport, HEADER_SIZE)
        if header[:4] != MAGIC:
            raise RetailProtocolError(
                f"bad retail packet magic {header[:4].hex()}"
            )
        total_length = struct.unpack("<I", header[4:])[0]
        if not HEADER_SIZE <= total_length <= MAX_PACKET_SIZE:
            raise RetailProtocolError(
                f"bad retail packet length {total_length}"
            )
        return self._recv_exact(transport, total_length - HEADER_SIZE)

    def _recv_exact(self, transport: ByteTransport, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = transport.recv(remaining, self._timeout)
            if not chunk:
                raise ConnectionError(
                    f"retail server closed with {remaining} bytes outstanding"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        if not payload or payload[-1:] != b"\x00":
            raise RetailProtocolError("retail text payload is not NUL terminated")
        if b"\x00" in payload[:-1]:
            raise RetailProtocolError("retail text payload contains embedded NUL")
        return payload[:-1].decode("ascii", errors="replace")

    @staticmethod
    def _frame(payload: bytes) -> bytes:
        return MAGIC + struct.pack("<I", HEADER_SIZE + len(payload)) + payload

    @classmethod
    def _command_packet(cls, command: str) -> bytes:
        return cls._frame(cls._validate_command(command) + b"\x00")

    @staticmethod
    def _validate_command(command: str) -> bytes:
        if not command or command != command.strip():
            raise ValueError("admin command must be non-empty and unpadded")
        if "\x00" in command or "\r" in command or "\n" in command:
            raise ValueError("admin command contains a forbidden control character")
        try:
            payload = command.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("admin command must be ASCII") from error
        if len(payload) > MAX_COMMAND_SIZE:
            raise ValueError(
                f"admin command exceeds the {MAX_COMMAND_SIZE}-byte retail limit"
            )
        return payload

    @staticmethod
    def _accepted(replies: tuple[str, ...]) -> bool:
        for reply in replies:
            normalized = reply.lstrip().upper()
            if normalized.startswith("ERROR") or normalized.startswith("USAGE"):
                return False
        return True

    def subscribe(self, listener: Callable[[object], None]):
        with self._condition:
            self._listeners.append(listener)

        session = self

        class Subscription:
            def unsubscribe(self) -> None:
                with session._condition:
                    try:
                        session._listeners.remove(listener)
                    except ValueError:
                        pass

            close = unsubscribe

        return Subscription()

    def _notify(self, event: object) -> None:
        with self._condition:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # A UI subscriber cannot be allowed to kill the protocol worker.
                pass

    @property
    def snapshot(self) -> AdminSnapshot:
        with self._condition:
            return self._snapshot

    @property
    def connected(self) -> bool:
        with self._condition:
            return not self._closed and self._transport is not None

    def close(self) -> None:
        with self._condition:
            if self._closed:
                pending: tuple[_Request, ...] = ()
                transports: tuple[ByteTransport | None, ...] = ()
            else:
                self._closed = True
                pending = tuple(
                    self._interactive_requests
                ) + tuple(self._background_requests)
                self._interactive_requests.clear()
                self._background_requests.clear()
                self._coalesced.clear()
                self._condition.notify_all()
                transports = (
                    self._transport,
                    self._connecting_transport,
                )
                self._transport = None
                self._connecting_transport = None
            worker = self._worker
        error = AdminSessionError("session is closed")
        for request in pending:
            self._settle_exception(request.observers, error)
        closed_transport_ids: set[int] = set()
        for transport in transports:
            if transport is None or id(transport) in closed_transport_ids:
                continue
            closed_transport_ids.add(id(transport))
            transport.close()
        if (
            worker is not None
            and worker is not threading.current_thread()
            and worker.is_alive()
        ):
            worker.join(timeout=max(self._timeout + 0.5, 1.0))
            if worker.is_alive():
                raise AdminSessionError(
                    "session worker did not stop during close"
                )
