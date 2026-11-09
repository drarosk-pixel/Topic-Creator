"""Manages the user-account (userbot) client and its login lifecycle.

Login happens entirely through the bot chat: phone number -> OTP -> optional
2FA password. We never ask the owner to paste a session string. Internally we
persist the authorised session as a Pyrogram session *string* in a local file
so restarts don't require logging in again. Using an in-memory client plus a
string on disk avoids SQLite file-lock issues between the login client and the
working client.
"""
from __future__ import annotations

import logging
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)

from config import Config

log = logging.getLogger(__name__)


class LoginResult(Enum):
    OK = auto()
    NEEDS_PASSWORD = auto()
    CODE_INVALID = auto()
    CODE_EXPIRED = auto()
    PHONE_INVALID = auto()


class UserBot:
    """Holds at most one authorised user client plus a transient login client."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client: Optional[Client] = None       # authorised, long-lived
        self._login_client: Optional[Client] = None  # transient, during login
        self._pending_phone: str = ""
        self._pending_hash: str = ""

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _session_file(self) -> Path:
        return self._cfg.session_string_file

    def has_saved_session(self) -> bool:
        f = self._session_file()
        return f.exists() and f.read_text(encoding="utf-8").strip() != ""

    def _read_saved_session(self) -> Optional[str]:
        if not self.has_saved_session():
            return None
        return self._session_file().read_text(encoding="utf-8").strip()

    def _write_saved_session(self, session_string: str) -> None:
        f = self._session_file()
        f.write_text(session_string, encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass

    def _delete_saved_session(self) -> None:
        f = self._session_file()
        if f.exists():
            f.unlink()

    # ------------------------------------------------------------------ #
    # State queries
    # ------------------------------------------------------------------ #
    @property
    def is_logged_in(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("Userbot is not logged in.")
        return self._client

    # ------------------------------------------------------------------ #
    # Startup — restore a previous session if present
    # ------------------------------------------------------------------ #
    async def restore(self) -> bool:
        """Try to bring up the client from a saved session string. Returns True on success."""
        saved = self._read_saved_session()
        if not saved:
            return False
        try:
            await self._start_working_client(saved)
            me = await self._client.get_me()  # type: ignore[union-attr]
            log.info("Restored userbot session for %s (id=%s)", me.first_name, me.id)
            return True
        except Exception as exc:  # noqa: BLE001 - we want to swallow any restore error
            log.warning("Could not restore saved session: %s", exc)
            await self._safe_stop_working_client()
            return False

    # ------------------------------------------------------------------ #
    # Login flow
    # ------------------------------------------------------------------ #
    async def begin_login(self, phone: str) -> LoginResult:
        """Step 1: connect a fresh client and request the OTP."""
        await self._cleanup_login_client()
        self._login_client = Client(
            name="login",
            api_id=self._cfg.api_id,
            api_hash=self._cfg.api_hash,
            in_memory=True,
        )
        await self._login_client.connect()
        try:
            sent = await self._login_client.send_code(phone)
        except PhoneNumberInvalid:
            await self._cleanup_login_client()
            return LoginResult.PHONE_INVALID
        # stash on the manager; the handler also stores hash in the Session
        self._pending_phone = phone
        self._pending_hash = sent.phone_code_hash
        return LoginResult.OK

    async def submit_code(self, code: str) -> LoginResult:
        """Step 2: send the OTP. May require a 2FA password afterwards."""
        if self._login_client is None:
            raise RuntimeError("No login in progress.")
        try:
            await self._login_client.sign_in(
                self._pending_phone, self._pending_hash, code
            )
        except SessionPasswordNeeded:
            return LoginResult.NEEDS_PASSWORD
        except PhoneCodeInvalid:
            return LoginResult.CODE_INVALID
        except PhoneCodeExpired:
            await self._cleanup_login_client()
            return LoginResult.CODE_EXPIRED
        await self._finalise_login()
        return LoginResult.OK

    async def submit_password(self, password: str) -> LoginResult:
        """Step 3 (optional): send the 2FA cloud password."""
        if self._login_client is None:
            raise RuntimeError("No login in progress.")
        await self._login_client.check_password(password)
        await self._finalise_login()
        return LoginResult.OK

    async def _finalise_login(self) -> None:
        """Export the freshly authorised session and promote it to the working client."""
        assert self._login_client is not None
        session_string = await self._login_client.export_session_string()
        await self._cleanup_login_client()
        self._write_saved_session(session_string)
        await self._start_working_client(session_string)

    # ------------------------------------------------------------------ #
    # Working client lifecycle
    # ------------------------------------------------------------------ #
    async def _start_working_client(self, session_string: str) -> None:
        await self._safe_stop_working_client()
        self._client = Client(
            name="userbot",
            api_id=self._cfg.api_id,
            api_hash=self._cfg.api_hash,
            session_string=session_string,
            in_memory=True,
        )
        await self._client.start()

    async def _safe_stop_working_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def logout(self) -> None:
        """Log the user account out, drop the session, and forget it."""
        if self._client is not None:
            try:
                await self._client.log_out()
            except Exception:  # noqa: BLE001
                await self._safe_stop_working_client()
        self._client = None
        self._delete_saved_session()

    async def shutdown(self) -> None:
        await self._cleanup_login_client()
        await self._safe_stop_working_client()

    async def _cleanup_login_client(self) -> None:
        if self._login_client is not None:
            try:
                await self._login_client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._login_client = None
