"""Provides a selector for Home Connect."""

import contextlib
import logging

from homeconnect.api import HomeConnectError

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .api import ConfigEntryAuth, HomeConnectDevice
from .const import (
    APPLIANCES_WITH_PROGRAMS,
    ATTR_VALUE,
    BSH_ACTIVE_PROGRAM,
    BSH_SELECTED_PROGRAM,
    DOMAIN,
)
from .entity import HomeConnectEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Home Connect select entities."""

    def get_entities() -> list[HomeConnectProgramSelectEntity]:
        """Get a list of entities."""
        entities: list[HomeConnectProgramSelectEntity] = []
        hc_api: ConfigEntryAuth = hass.data[DOMAIN][config_entry.entry_id]
        for device in hc_api.devices:
            if device.appliance.type in APPLIANCES_WITH_PROGRAMS:
                with contextlib.suppress(HomeConnectError):
                    programs = device.appliance.get_programs_available()
                    if programs:
                        entities.extend(
                            HomeConnectProgramSelectEntity(
                                device, programs, start_on_select
                            )
                            for start_on_select in (True, False)
                        )
        return entities

    async_add_entities(await hass.async_add_executor_job(get_entities), True)


class HomeConnectProgramSelectEntity(HomeConnectEntity, SelectEntity):
    """Select class for Home Connect programs."""

    def __init__(
        self, device: HomeConnectDevice, programs: list[str], start_on_select: bool
    ) -> None:
        """Initialize the entity."""
        super().__init__(
            device,
            SelectEntityDescription(
                key=BSH_ACTIVE_PROGRAM if start_on_select else BSH_SELECTED_PROGRAM,
                translation_key="active_program"
                if start_on_select
                else "selected_program",
            ),
        )
        self.options_map = {
            self.format_program(program): program for program in programs
        }
        self._attr_options = [key for key in self.options_map if key is not None]
        self.start_on_select = start_on_select

    async def async_update(self) -> None:
        """Update the program selection status."""
        self._attr_current_option = self.format_program(
            self.device.appliance.status.get(self.bsh_key, {}).get(ATTR_VALUE, None)
        )
        _LOGGER.debug("Updated, new program: %s", self._attr_current_option)

    async def async_select_option(self, option: str) -> None:
        """Select new program."""
        bsh_key = self.options_map[option]
        _LOGGER.debug(
            "Tried to start program: %s"
            if self.start_on_select
            else "Tried to select program: %s",
            bsh_key,
        )
        try:
            await self.hass.async_add_executor_job(
                self.device.appliance.start_program
                if self.start_on_select
                else self.device.appliance.select_program,
                bsh_key,
            )
        except HomeConnectError as err:
            _LOGGER.error(
                "Error while trying to select program %s: %s"
                if self.start_on_select
                else "Error while trying to start program %s: %s",
                bsh_key,
                err,
            )
        self.async_entity_update()

    def format_program(self, program: str | None) -> str | None:
        """Format the program for display."""
        if not program:
            return None
        return slugify(program.split(".Program.")[-1])
