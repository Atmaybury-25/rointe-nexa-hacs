"""Client for the Rointe Nexa cloud platform.

Rointe publish no API. Everything here was established by reading the Nexa web
app and watching a live Onyx storage heater. The two things that are not
guessable, and that every existing community integration gets wrong:

1. Firebase is not signed into with the account password. The web app derives a
   synthetic credential from the Rointe user id::

       firebaseStore().login(userStore().user.id)
       signInWithEmailAndPassword(auth, `${id}@rointe.com`, id)

2. There are two mirrored nodes carrying identical field names, and only one of
   them is control::

       /zones/{zone_id}/data      commanded state  - write here
       /devices/{serial}/data     reported state   - read only

   A write to the device node returns 200, reads back correctly, and is then
   silently re-asserted by the heater minutes later.

Also: never write ``last_sync_datetime_device``. It is the heater's own sync
marker; the app echoes back whatever the device wrote and never sets it.
Bumping it makes the heater discard the change.

This module deliberately has no Home Assistant imports so it can be exercised
on its own.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REST_BASE = "https://rointenexa.com/api"
LOGIN_URL = f"{REST_BASE}/user/login"

# From the Nexa web app's Nuxt runtime config. The v4 REST API and the Firebase
# realtime layer are separate systems; the v4 front end still uses the v3
# Firebase project.
FIREBASE_API_KEY = "AIzaSyC0aaLXKB8Vatf2xSn1QaFH1kw7rADZlrY"
RTDB_BASE = "https://rointe-v3-prod-default-rtdb.europe-west1.firebasedatabase.app"

SIGNIN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    f"?key={FIREBASE_API_KEY}"
)
REFRESH_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"

FIREBASE_EMAIL_SUFFIX = "@rointe.com"

# Firebase ID tokens last an hour. Refresh early enough that a slow request
# cannot straddle the expiry.
TOKEN_MARGIN = 300.0

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://rointenexa.com",
    "Referer": "https://rointenexa.com/login",
}

# Firebase web keys are commonly referrer-restricted.
FIREBASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://rointenexa.com",
    "Referer": "https://rointenexa.com/",
}

DAYS = 7
HOURS = 24

# Heating schedule alphabet. The app names the lowest level OFF with a STANDBY
# colour; the UI presents it as anti-frost. Same character either way.
SCHED_COMFORT = "C"
SCHED_ECO = "E"
SCHED_OFF = "O"
HEATING_CHARS = frozenset({SCHED_COMFORT, SCHED_ECO, SCHED_OFF})

CHARGE_ON = "1"
CHARGE_OFF = "0"
CHARGE_CHARS = frozenset({CHARGE_ON, CHARGE_OFF})

# Never send this to the cloud - see the module docstring.
FORBIDDEN_WRITE_KEYS = frozenset({"last_sync_datetime_device"})


class RointeError(Exception):
    """Any failure talking to Rointe."""


class RointeAuthError(RointeError):
    """Credentials rejected. Not retryable without user action."""


class RointeConnectionError(RointeError):
    """Transport failure. Retryable."""


def decode_jwt_exp(token: str) -> float | None:
    """Read a JWT's exp claim. No signature check - we only want the timing."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        return float(json.loads(base64.urlsafe_b64decode(seg))["exp"])
    except Exception:  # noqa: BLE001 - a malformed token just means "unknown"
        return None


def hours_to_schedule(hours: list[int] | set[int], *, on: str = CHARGE_ON,
                      off: str = CHARGE_OFF) -> str:
    """Build a 24-character day string from a set of hour numbers.

    The end hour is exclusive in the app's own editor: a 02:00-03:00 window
    sets exactly one character, at index 2.
    """
    wanted = {int(h) for h in hours}
    bad = {h for h in wanted if not 0 <= h < HOURS}
    if bad:
        raise ValueError(f"hours must be 0-23, got {sorted(bad)}")
    return "".join(on if h in wanted else off for h in range(HOURS))


def schedule_to_hours(day: str, *, off: str = CHARGE_OFF) -> list[int]:
    """Inverse of hours_to_schedule."""
    return [h for h, c in enumerate(day) if c != off]


def validate_day(day: str, allowed: frozenset[str]) -> str:
    """Reject a malformed day string before it reaches the heater."""
    if not isinstance(day, str):
        raise ValueError(f"schedule day must be a string, got {type(day).__name__}")
    if len(day) != HOURS:
        raise ValueError(f"schedule day must be exactly {HOURS} chars, got {len(day)}")
    bad = sorted(set(day) - allowed)
    if bad:
        raise ValueError(
            f"invalid character(s) {bad} - allowed: {sorted(allowed)}"
        )
    return day


@dataclass
class RointeDevice:
    """One heater, as the platform describes it."""

    device_id: str
    serial: str
    name: str
    zone_id: str
    zone_name: str
    installation_id: str
    installation_name: str
    online: bool = True
    # commanded state, from /zones/{zone_id}/data
    zone: dict[str, Any] = field(default_factory=dict)
    # reported state, from /devices/{serial}/data
    reported: dict[str, Any] = field(default_factory=dict)
    firmware: dict[str, Any] = field(default_factory=dict)

    # Boost forces the heater out of its weekly programme, and nothing puts it
    # back - not switching Boost off, not the timer running out. These two
    # remember what it was doing so the programme can be handed back when the
    # boost ends. `boost_acknowledged` guards the several minutes between
    # commanding a boost and the heater reporting it, during which a reported
    # `timer_mode` of False means "not yet", not "finished".
    mode_before_boost: int | None = None
    boost_acknowledged: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        """What the heater REPORTS. Use for physical readings.

        The reported node is the truth about the hardware - temperatures,
        charge level, whether it is charging right now.
        """
        if key in self.reported:
            return self.reported[key]
        return self.zone.get(key, default)

    def get_commanded(self, key: str, default: Any = None) -> Any:
        """What has been ASKED FOR. Use for anything writable.

        The zone node leads the device by up to a sync interval, so a setting
        the user just changed reads back correctly here while the reported
        node still holds the old value. Reading `get` for a writable control
        makes the UI snap back to the previous value for minutes, which looks
        like the write failed. The Nexa app displays this node for the same
        reason.
        """
        if key in self.zone:
            return self.zone[key]
        return self.reported.get(key, default)

    @property
    def model(self) -> str:
        kind = self.get("type") or "heater"
        return {"storageheater": "Onyx storage heater"}.get(kind, str(kind))

    @property
    def unique_id(self) -> str:
        return self.serial


class RointeClient:
    """Talks to Rointe's REST inventory and Firebase realtime database."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        rtdb_base: str = RTDB_BASE,
    ) -> None:
        self._email = email
        self._password = password
        self._session = session
        self._rtdb = rtdb_base.rstrip("/")

        self._rest_token: str | None = None
        self._user_id: str | None = None

        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._id_token_expiry: float = 0.0

        self._auth_lock = asyncio.Lock()

    # -- plumbing ---------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        try:
            async with self._session.request(
                method, url, timeout=REQUEST_TIMEOUT, **kwargs
            ) as resp:
                text = await resp.text()
                try:
                    body: Any = json.loads(text) if text else None
                except ValueError:
                    body = text
                return resp.status, body
        except asyncio.TimeoutError as err:
            raise RointeConnectionError(f"timeout calling {url}") from err
        except aiohttp.ClientError as err:
            raise RointeConnectionError(f"{type(err).__name__} calling {url}") from err

    # -- authentication ---------------------------------------------------

    async def async_login(self) -> None:
        """Log in to both systems. Safe to call repeatedly."""
        async with self._auth_lock:
            await self._login_rest()
            await self._login_firebase()

    async def _login_rest(self) -> None:
        status, body = await self._request(
            "POST",
            LOGIN_URL,
            json={
                "email": self._email,
                "password": self._password,
                "push": "",
                "migrate": False,
            },
            headers=BROWSER_HEADERS,
        )
        # Rointe answer bad credentials with 418, not 401.
        if status in (400, 401, 403, 418):
            raise RointeAuthError(f"Rointe rejected the credentials (HTTP {status})")
        if status != 200 or not isinstance(body, dict):
            raise RointeConnectionError(f"login failed: HTTP {status}")

        data = body.get("data") or {}
        self._rest_token = data.get("token")
        self._user_id = (data.get("user") or {}).get("id")
        if not self._rest_token or not self._user_id:
            raise RointeAuthError("login response carried no token or user id")

    async def _login_firebase(self) -> None:
        """Sign in with the credential the web app derives from the user id."""
        if not self._user_id:
            raise RointeAuthError("cannot derive a Firebase credential without a user id")

        status, body = await self._request(
            "POST",
            SIGNIN_URL,
            json={
                "email": f"{self._user_id}{FIREBASE_EMAIL_SUFFIX}",
                "password": self._user_id,
                "returnSecureToken": True,
            },
            headers=FIREBASE_HEADERS,
        )
        if status != 200 or not isinstance(body, dict):
            msg = ""
            if isinstance(body, dict):
                msg = (body.get("error") or {}).get("message", "")
            raise RointeAuthError(f"Firebase sign-in failed (HTTP {status}) {msg}".strip())

        self._store_tokens(body)

    def _store_tokens(self, body: dict[str, Any]) -> None:
        token = body.get("idToken") or body.get("id_token")
        if not token:
            raise RointeAuthError("no ID token in the Firebase response")
        self._id_token = token
        self._refresh_token = (
            body.get("refreshToken") or body.get("refresh_token") or self._refresh_token
        )
        exp = decode_jwt_exp(token)
        if exp is None:
            # expires_in is seconds, as a string on the refresh endpoint
            try:
                exp = time.time() + float(body.get("expiresIn") or body.get("expires_in") or 3600)
            except (TypeError, ValueError):
                exp = time.time() + 3600
        self._id_token_expiry = exp

    async def _async_token(self) -> str:
        """A valid ID token, refreshed ahead of expiry."""
        async with self._auth_lock:
            if self._id_token and time.time() < self._id_token_expiry - TOKEN_MARGIN:
                return self._id_token

            if self._refresh_token:
                status, body = await self._request(
                    "POST",
                    REFRESH_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                    },
                )
                if status == 200 and isinstance(body, dict):
                    self._store_tokens(body)
                    return self._id_token  # type: ignore[return-value]
                _LOGGER.debug("token refresh failed (HTTP %s); signing in again", status)

            await self._login_rest()
            await self._login_firebase()
            if not self._id_token:
                raise RointeAuthError("could not obtain a Firebase token")
            return self._id_token

    # -- realtime database ------------------------------------------------

    async def _rtdb_get(self, path: str) -> Any:
        token = await self._async_token()
        url = f"{self._rtdb}/{path.strip('/')}.json"
        status, body = await self._request("GET", url, params={"auth": token})
        if status == 401:
            raise RointeAuthError(f"RTDB refused the token for {path}")
        if status != 200:
            raise RointeConnectionError(f"RTDB GET {path}: HTTP {status}")
        return body

    async def _rtdb_patch(self, path: str, payload: dict[str, Any]) -> None:
        token = await self._async_token()
        url = f"{self._rtdb}/{path.strip('/')}.json"
        status, body = await self._request(
            "PATCH",
            url,
            params={"auth": token},
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if status == 401:
            raise RointeAuthError(f"RTDB refused the token for {path}")
        if status != 200:
            raise RointeConnectionError(f"RTDB PATCH {path}: HTTP {status} {body}")

    # -- inventory --------------------------------------------------------

    async def async_discover(self) -> list[RointeDevice]:
        """Walk installations -> zones -> devices.

        The Onyx is a family neither community integration has seen, so this
        does not assume a device sits exactly where a radiator sits.
        """
        if not self._rest_token:
            await self.async_login()

        status, body = await self._request(
            "GET", f"{REST_BASE}/installations", headers={"token": self._rest_token}
        )
        if status == 401:
            await self.async_login()
            status, body = await self._request(
                "GET", f"{REST_BASE}/installations", headers={"token": self._rest_token}
            )
        if status != 200:
            raise RointeConnectionError(f"/installations: HTTP {status}")

        root = body.get("data", body) if isinstance(body, dict) else body
        found: list[RointeDevice] = []
        for inst in _as_items(root):
            if not isinstance(inst, dict):
                continue
            for zone in _as_items(inst.get("zones")):
                if not isinstance(zone, dict):
                    continue
                for dev in _as_items(zone.get("devices")):
                    if not isinstance(dev, dict) or not dev.get("serialNumber"):
                        continue
                    found.append(
                        RointeDevice(
                            device_id=str(dev.get("id") or dev["serialNumber"]),
                            serial=str(dev["serialNumber"]),
                            name=str(dev.get("name") or zone.get("name") or "Rointe"),
                            zone_id=str(zone.get("id") or ""),
                            zone_name=str(zone.get("name") or ""),
                            installation_id=str(inst.get("id") or ""),
                            installation_name=str(inst.get("name") or ""),
                            online=dev.get("deviceStatus") == 1,
                        )
                    )
        return found

    async def async_refresh(self, devices: list[RointeDevice]) -> list[RointeDevice]:
        """Populate each device with both its commanded and reported state."""
        zone_ids = {d.zone_id for d in devices if d.zone_id}
        zones: dict[str, Any] = {}
        for zone_id in zone_ids:
            try:
                zones[zone_id] = await self._rtdb_get(f"/zones/{zone_id}/data") or {}
            except RointeConnectionError as err:
                _LOGGER.debug("zone %s unreadable: %s", zone_id, err)
                zones[zone_id] = {}

        for dev in devices:
            dev.zone = zones.get(dev.zone_id, {}) or {}
            try:
                node = await self._rtdb_get(f"/devices/{dev.serial}") or {}
            except RointeConnectionError as err:
                _LOGGER.debug("device %s unreadable: %s", dev.serial, err)
                continue
            if isinstance(node, dict):
                dev.reported = node.get("data") or {}
                dev.firmware = node.get("firmware") or {}
                # is_alive is the heater's own word for it; deviceStatus is the
                # inventory's, and goes stale.
                if "is_alive" in dev.reported:
                    dev.online = bool(dev.reported["is_alive"])
        return devices

    # -- control ----------------------------------------------------------

    async def async_command(self, device: RointeDevice, payload: dict[str, Any]) -> None:
        """Send a command. Always to the zone node - see the module docstring."""
        if not device.zone_id:
            raise RointeError(f"{device.name} has no zone; cannot be commanded")

        bad = FORBIDDEN_WRITE_KEYS & set(payload)
        if bad:
            raise ValueError(
                f"refusing to write {sorted(bad)}: it is the heater's own sync "
                "marker, and writing it makes the heater discard the change"
            )

        _LOGGER.debug("command %s (zone %s): %s", device.name, device.zone_id, payload)
        await self._rtdb_patch(f"/zones/{device.zone_id}/data", payload)
        # Optimistic: the coordinator will overwrite on the next poll.
        device.zone.update(payload)

    async def async_set_charge_schedule(
        self, device: RointeDevice, days: dict[int, str]
    ) -> None:
        """Set charging hours for one or more days.

        ``days`` maps a weekday index (0 = Monday) to a 24-character string.
        RTDB honours slash-separated keys in a PATCH body, so a single day can
        be replaced without resending the whole week.
        """
        payload: dict[str, Any] = {}
        for index, day in days.items():
            if not 0 <= int(index) < DAYS:
                raise ValueError(f"day index must be 0-6, got {index}")
            payload[f"schedule_charging/{int(index)}"] = validate_day(day, CHARGE_CHARS)
        if payload:
            await self.async_command(device, payload)

    async def async_set_heating_schedule(
        self, device: RointeDevice, days: dict[int, str]
    ) -> None:
        """Set the heating programme. Characters are C, E and O."""
        payload: dict[str, Any] = {}
        for index, day in days.items():
            if not 0 <= int(index) < DAYS:
                raise ValueError(f"day index must be 0-6, got {index}")
            payload[f"schedule/{int(index)}"] = validate_day(day, HEATING_CHARS)
        if payload:
            await self.async_command(device, payload)


def _as_items(container: Any) -> list[Any]:
    """The API returns lists, but Firebase-backed payloads can be id-keyed
    dicts. Accept either."""
    if isinstance(container, list):
        return container
    if isinstance(container, dict):
        return list(container.values())
    return []
