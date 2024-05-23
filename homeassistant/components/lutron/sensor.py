"""Support for Lutron Variables."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from pylutron import Sysvar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, LutronData
from .entity import LutronDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lutron sensor platform.

    Adds variable from the Main Repeater associated with the
    config_entry as sensor entities.
    """
    entry_data: LutronData = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            LutronVariable("", device, entry_data.client)
            for device_name, device in entry_data.variables
        ],
        True,
    )


class LutronVariable(LutronDevice, SensorEntity):
    """Representation of a Lutron Variable."""

    _lutron_device: Sysvar
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, area_name, lutron_device, controller) -> None:
        """Initialize the occupancy sensor."""
        super().__init__(area_name, lutron_device.name, lutron_device, controller)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes."""
        return {"lutron_integration_id": self._lutron_device.id}

    def _update_attrs(self) -> None:
        """Update the state attributes."""
        self._attr_native_value = self._lutron_device.last_state()

    def _request_state(self) -> None:
        """Request the state from the device."""
        _ = self._lutron_device.state
