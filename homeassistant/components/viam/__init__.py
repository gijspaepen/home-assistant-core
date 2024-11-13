"""The viam integration."""

from __future__ import annotations

from viam.app.viam_client import ViamClient
from viam.rpc.dial import Credentials, DialOptions

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_API_ID,
    CONF_CREDENTIAL_TYPE,
    CONF_SECRET,
    CRED_TYPE_API_KEY,
    DOMAIN,
)
from .manager import ViamManager
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Viam services."""

    async_setup_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up viam from a config entry."""
    credential_type = entry.data[CONF_CREDENTIAL_TYPE]
    payload = entry.data[CONF_SECRET]
    auth_entity = entry.data[CONF_ADDRESS]
    if credential_type == CRED_TYPE_API_KEY:
        payload = entry.data[CONF_API_KEY]
        auth_entity = entry.data[CONF_API_ID]

    credentials = Credentials(type=credential_type, payload=payload)
    dial_options = DialOptions(auth_entity=auth_entity, credentials=credentials)
    viam_client = await ViamClient.create_from_dial_options(dial_options=dial_options)
    manager = ViamManager(hass, viam_client, entry.entry_id, dict(entry.data))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    manager: ViamManager = hass.data[DOMAIN].pop(entry.entry_id)
    manager.unload()

    return True
