"""Config flow for Rointe Nexa."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow

try:  # ConfigFlowResult arrived in HA 2024.4
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover - older cores
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RointeAuthError, RointeClient, RointeError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class RointeNexaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the Nexa account, then prove it works before finishing."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_email: str | None = None

    async def _validate(self, email: str, password: str) -> tuple[str | None, int]:
        """Return (error_key, device_count)."""
        client = RointeClient(email, password, async_get_clientsession(self.hass))
        try:
            await client.async_login()
            devices = await client.async_discover()
        except RointeAuthError:
            return "invalid_auth", 0
        except RointeError:
            return "cannot_connect", 0
        except Exception:  # noqa: BLE001
            _LOGGER.exception("unexpected error validating Rointe credentials")
            return "unknown", 0
        return None, len(devices)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            error, count = await self._validate(email, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                if count == 0:
                    _LOGGER.warning(
                        "Rointe account has no paired devices; entities will "
                        "appear once a heater is added in the Nexa app"
                    )
                return self.async_create_entry(
                    title=email, data={CONF_EMAIL: email, **user_input}
                )
        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if user_input is not None and entry is not None:
            email = self._reauth_email or entry.data[CONF_EMAIL]
            error, _ = await self._validate(email, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"email": self._reauth_email or ""},
        )
