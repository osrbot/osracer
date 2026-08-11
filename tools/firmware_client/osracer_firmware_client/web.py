"""Local-only single-page interface for the standalone firmware client."""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from . import __version__, core
from .build_info import load_build_info
from .images import MAX_APP_BYTES
from .operations import ClientSettings, ErasePreparation, FirmwareClient, OperationResult
from .storage import write_private_file


MAX_JSON_BYTES = 64 * 1024


class WebApplication:
    def __init__(self, settings: ClientSettings):
        self.token = secrets.token_urlsafe(32)
        self._state_lock = threading.Lock()
        self._busy = False
        self._operation = "idle"
        self._events: list[dict[str, Any]] = []
        self._result: dict[str, Any] | None = None
        self._error: dict[str, Any] | None = None
        self._preparation: dict[str, Any] | None = None
        self._client = FirmwareClient(settings=settings, event_sink=self._event)
        self.settings = settings

    def _event(self, event: dict[str, Any]) -> None:
        with self._state_lock:
            self._events.append(event)
            if len(self._events) > 2000:
                self._events = self._events[-2000:]

    def state(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "version": __version__,
                "build": load_build_info(),
                "busy": self._busy,
                "operation": self._operation,
                "events": list(self._events),
                "result": self._result,
                "error": self._error,
                "erase_preparation": self._preparation,
            }

    def bundles(self) -> dict[str, Any]:
        return {
            bundle_id: bundle.safe_summary()
            for bundle_id, bundle in self._client.bundles.items()
        }

    def _start(self, name: str, function: Callable[[], Any], *, cleanup: Path | None = None) -> None:
        with self._state_lock:
            if self._busy:
                raise core.FirmwareUpdateError("another firmware operation is already active")
            if self._preparation is not None and name not in {"erase_execute", "erase_prepare"}:
                raise core.FirmwareUpdateError(
                    "advanced recovery is prepared; execute it or restart the client to cancel"
                )
            self._busy = True
            self._operation = name
            self._events = []
            self._result = None
            self._error = None

        def worker() -> None:
            try:
                value = function()
                if isinstance(value, (OperationResult, ErasePreparation)):
                    result = value.safe_summary()
                elif hasattr(value, "safe_summary"):
                    result = value.safe_summary()
                else:
                    result = value
                with self._state_lock:
                    self._result = result
                    if isinstance(value, ErasePreparation):
                        self._preparation = result
                    elif name == "erase_execute":
                        self._preparation = None
            except core.FirmwareUpdateError as error:
                with self._state_lock:
                    self._error = {
                        "message": str(error),
                        "exit_code": error.exit_code,
                        "audit_path": (
                            None if error.audit_path is None else str(error.audit_path)
                        ),
                        "do_not_reflash": bool(getattr(error, "no_app_reflash", False)),
                        "physical_recovery_required": bool(
                            getattr(error, "physical_recovery_required", False)
                        ),
                    }
            except Exception:
                with self._state_lock:
                    self._error = {
                        "message": "Unexpected local client error; no new operation was started",
                        "exit_code": 4,
                        "audit_path": None,
                        "do_not_reflash": False,
                        "physical_recovery_required": False,
                    }
            finally:
                if cleanup is not None:
                    try:
                        cleanup.unlink()
                    except OSError:
                        pass
                with self._state_lock:
                    if name == "erase_execute":
                        self._preparation = None
                    self._busy = False

        threading.Thread(target=worker, name=f"firmware-client-{name}", daemon=True).start()

    def inspect(self) -> None:
        self._start("inspect", self._client.inspect)

    def official(self, document: dict[str, Any]) -> None:
        confirmation = document.get("confirmation")
        reinstall = document.get("reinstall", False)
        if not isinstance(confirmation, str) or not isinstance(reinstall, bool):
            raise core.PackageValidationError("official request is invalid")
        self._start(
            "official",
            lambda: self._client.official_update(
                confirmation=confirmation,
                reinstall=reinstall,
            ),
        )

    def custom(self, image: bytes, filename: str, confirmation: str) -> None:
        if not isinstance(filename, str) or not filename or len(filename) > 255:
            raise core.PackageValidationError("custom filename is invalid")
        if confirmation != "FLASH CUSTOM":
            raise core.UserCancelledError("custom App update requires FLASH CUSTOM")
        stored = write_private_file(
            self.settings.state_dir / "uploads",
            prefix="custom-app",
            suffix=".bin",
            data=image,
        )
        try:
            self._start(
                "custom",
                lambda: self._client.custom_app_update(
                    stored.path,
                    confirmation=confirmation,
                ),
                cleanup=stored.path,
            )
        except Exception:
            try:
                stored.path.unlink()
            except OSError:
                pass
            raise

    def erase_prepare(self, document: dict[str, Any]) -> None:
        bundle_id = document.get("bundle_id")
        confirmation = document.get("confirmation")
        if not isinstance(bundle_id, str) or not isinstance(confirmation, str):
            raise core.PackageValidationError("erase preparation request is invalid")
        with self._state_lock:
            self._preparation = None
        self._start(
            "erase_prepare",
            lambda: self._client.prepare_erase(bundle_id, confirmation=confirmation),
        )

    def erase_execute(self, document: dict[str, Any]) -> None:
        preparation_id = document.get("preparation_id")
        confirmation = document.get("confirmation")
        acknowledge = document.get("acknowledge_non_nvs_loss")
        if (
            not isinstance(preparation_id, str)
            or not isinstance(confirmation, str)
            or not isinstance(acknowledge, bool)
        ):
            raise core.PackageValidationError("erase execution request is invalid")
        with self._state_lock:
            preparation = self._preparation
        if preparation is None or preparation.get("preparation_id") != preparation_id:
            raise core.PackageValidationError("erase preparation is unavailable")
        expected = preparation.get("required_confirmation")
        if not acknowledge or confirmation != expected:
            raise core.UserCancelledError(
                "advanced recovery requires the checkbox and exact second confirmation"
            )
        self._start(
            "erase_execute",
            lambda: self._client.execute_erase(
                preparation_id,
                acknowledge_non_nvs_loss=acknowledge,
                confirmation=confirmation,
            ),
        )


def _static_bytes(name: str) -> bytes:
    return resources.files("osracer_firmware_client").joinpath("static", name).read_bytes()


def handler_factory(application: WebApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "OSRacerFirmwareClient/1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )

        def _send_bytes(self, status: int, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, value: Any) -> None:
            data = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self._send_bytes(status, "application/json; charset=utf-8", data)

        def _authorized(self) -> bool:
            return secrets.compare_digest(
                self.headers.get("X-Session-Token", ""),
                application.token,
            )

        def _require_authorized(self) -> bool:
            if self._authorized():
                return True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid local session token"})
            return False

        def _check_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = self.headers.get("Host", "")
            return origin in {f"http://{host}", f"http://localhost:{host.rsplit(':', 1)[-1]}"}

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise core.PackageValidationError("request length is invalid") from None
            if not 1 <= length <= MAX_JSON_BYTES:
                raise core.PackageValidationError("JSON request size is invalid")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise core.PackageValidationError("request JSON is invalid") from None
            if not isinstance(value, dict):
                raise core.PackageValidationError("request JSON must be an object")
            return value

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", _static_bytes("index.html"))
                return
            if path == "/app.css":
                self._send_bytes(HTTPStatus.OK, "text/css; charset=utf-8", _static_bytes("app.css"))
                return
            if path == "/app.js":
                self._send_bytes(
                    HTTPStatus.OK,
                    "application/javascript; charset=utf-8",
                    _static_bytes("app.js"),
                )
                return
            if path == "/api/state":
                if self._require_authorized():
                    self._send_json(HTTPStatus.OK, application.state())
                return
            if path == "/api/bundles":
                if self._require_authorized():
                    self._send_json(HTTPStatus.OK, application.bundles())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._require_authorized():
                return
            if not self._check_origin():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin rejected"})
                return
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/inspect":
                    application.inspect()
                elif path == "/api/official":
                    application.official(self._read_json())
                elif path == "/api/custom":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        raise core.PackageValidationError("custom image length is invalid") from None
                    if not 1024 <= length <= MAX_APP_BYTES:
                        raise core.PackageValidationError("custom image size is invalid")
                    image = self.rfile.read(length)
                    if len(image) != length:
                        raise core.PackageValidationError("custom image upload was truncated")
                    application.custom(
                        image,
                        self.headers.get("X-Filename", "application.bin"),
                        self.headers.get("X-Confirmation", ""),
                    )
                elif path == "/api/erase/prepare":
                    application.erase_prepare(self._read_json())
                elif path == "/api/erase/execute":
                    application.erase_execute(self._read_json())
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._send_json(HTTPStatus.ACCEPTED, {"accepted": True})
            except core.FirmwareUpdateError as error:
                status = (
                    HTTPStatus.CONFLICT
                    if "already active" in str(error)
                    else HTTPStatus.BAD_REQUEST
                )
                self._send_json(status, {"error": str(error), "exit_code": error.exit_code})

    return Handler


def serve(
    *,
    settings: ClientSettings,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    output_func: Callable[[str], None] = print,
) -> int:
    if host != "127.0.0.1":
        raise core.PackageValidationError("local web interface must bind to 127.0.0.1")
    application = WebApplication(settings)
    server = ThreadingHTTPServer((host, port), handler_factory(application))
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/#{application.token}"
    output_func(f"Local firmware client: {url}")
    output_func("The server is local-only. Press Ctrl-C to stop it.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        output_func("Stopping local firmware client.")
    finally:
        server.server_close()
    return 0
