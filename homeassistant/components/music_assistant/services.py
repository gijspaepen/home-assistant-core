"""Custom services for the Music Assistant integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from music_assistant_client.helpers import searchresults_as_compact_dict
from music_assistant_models.enums import MediaType
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from music_assistant_client import MusicAssistantClient

    from . import MusicAssistantConfigEntry

SERVICE_SEARCH = "search"
ATTR_MEDIA_TYPE = "media_type"
ATTR_SEARCH_NAME = "name"
ATTR_SEARCH_ARTIST = "artist"
ATTR_SEARCH_ALBUM = "album"
ATTR_LIMIT = "limit"
ATTR_LIBRARY_ONLY = "library_only"


@callback
def get_music_assistant_client(hass: HomeAssistant) -> MusicAssistantClient:
    """Get the (first) Music Assistant client from the (loaded) config entries."""
    entry: MusicAssistantConfigEntry
    for entry in hass.config_entries.async_entries(DOMAIN, False, False):
        if entry.state != ConfigEntryState.LOADED:
            continue
        return entry.runtime_data.mass
    raise HomeAssistantError("Music Assistant is not loaded")


@callback
def register_services(hass: HomeAssistant) -> None:
    """Register custom services."""

    async def handle_search(call: ServiceCall) -> ServiceResponse:
        """Handle queue_command service."""
        mass = get_music_assistant_client(hass)
        search_name = call.data[ATTR_SEARCH_NAME]
        search_artist = call.data.get(ATTR_SEARCH_ARTIST)
        search_album = call.data.get(ATTR_SEARCH_ALBUM)
        if search_album and search_artist:
            search_name = f"{search_artist} - {search_album} - {search_name}"
        elif search_album:
            search_name = f"{search_album} - {search_name}"
        elif search_artist:
            search_name = f"{search_artist} - {search_name}"
        search_results = await mass.music.search(
            search_query=search_name,
            media_types=call.data.get(ATTR_MEDIA_TYPE, MediaType.ALL),
            limit=call.data[ATTR_LIMIT],
            library_only=call.data[ATTR_LIBRARY_ONLY],
        )
        # return limited result to prevent it being too verbose
        return cast(ServiceResponse, searchresults_as_compact_dict(search_results))

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        handle_search,
        schema=vol.Schema(
            {
                vol.Required(ATTR_SEARCH_NAME): cv.string,
                vol.Optional(ATTR_MEDIA_TYPE): vol.All(
                    cv.ensure_list, [vol.Coerce(MediaType)]
                ),
                vol.Optional(ATTR_SEARCH_ARTIST): cv.string,
                vol.Optional(ATTR_SEARCH_ALBUM): cv.string,
                vol.Optional(ATTR_LIMIT, default=5): vol.Coerce(int),
                vol.Optional(ATTR_LIBRARY_ONLY, default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
