"""Support for Lutron lights."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from pylutron import Led, Output

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_FLASH,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.script import scripts_with_entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    create_issue,
)

from . import DOMAIN, LutronData
from .entity import LutronDevice, LutronKeypad

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lutron light platform.

    Adds dimmers from the Main Repeater associated with the config_entry as
    light entities.

    Set up the lutron leds for the keypads
    """
    ent_reg = er.async_get(hass)
    entry_data: LutronData = hass.data[DOMAIN][config_entry.entry_id]
    lights = []
    leds = []

    for area_name, device_name, device in entry_data.lights:
        if device.type == "CEILING_FAN_TYPE":
            # If this is a fan, check to see if this entity already exists.
            # If not, do not create a new one.
            entity_id = ent_reg.async_get_entity_id(
                Platform.LIGHT,
                DOMAIN,
                f"{entry_data.client.guid}_{device.uuid}",
            )
            if entity_id:
                entity_entry = ent_reg.async_get(entity_id)
                assert entity_entry
                if entity_entry.disabled:
                    # If the entity exists and is disabled then we want to remove
                    # the entity so that the user is using the new fan entity instead.
                    ent_reg.async_remove(entity_id)
                else:
                    lights.append(
                        LutronLight(area_name, device_name, device, entry_data.client)
                    )
                    entity_automations = automations_with_entity(hass, entity_id)
                    entity_scripts = scripts_with_entity(hass, entity_id)
                    for item in entity_automations + entity_scripts:
                        async_create_issue(
                            hass,
                            DOMAIN,
                            f"deprecated_light_fan_{entity_id}_{item}",
                            breaks_in_ha_version="2024.8.0",
                            is_fixable=True,
                            is_persistent=True,
                            severity=IssueSeverity.WARNING,
                            translation_key="deprecated_light_fan_entity",
                            translation_placeholders={
                                "entity": entity_id,
                                "info": item,
                            },
                        )
        else:
            lights.append(
                LutronLight(area_name, device_name, device, entry_data.client)
            )

    async_add_entities(
        lights,
        True,
    )

    for area_name, device_name, keypad, device in entry_data.leds:
        leds.append(
            LutronLedLight(area_name, device_name, keypad, device, entry_data.client)
        )

    async_add_entities(
        leds,
        True,
    )


def to_lutron_level(level):
    """Convert the given Home Assistant light level (0-255) to Lutron (0.0-100.0)."""
    return float((level * 100) / 255)


def to_hass_level(level):
    """Convert the given Lutron (0.0-100.0) light level to Home Assistant (0-255)."""
    return int((level * 255) / 100)


class LutronLight(LutronDevice, LightEntity):
    """Representation of a Lutron Light, including dimmable."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _lutron_device: Output
    _prev_brightness: int | None = None
    _attr_name = None

    def __init__(self, area_name, device_name, lutron_device, controller) -> None:
        """Initialize the light."""
        super().__init__(area_name, device_name, lutron_device, controller)
        self._is_fan = lutron_device.type == "CEILING_FAN_TYPE"
        if self._lutron_device.is_dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_supported_features = (
                LightEntityFeature.TRANSITION | LightEntityFeature.FLASH
            )

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if self._is_fan:
            create_issue(
                self.hass,
                DOMAIN,
                "deprecated_light_fan_on",
                breaks_in_ha_version="2024.8.0",
                is_fixable=True,
                is_persistent=True,
                severity=IssueSeverity.WARNING,
                translation_key="deprecated_light_fan_on",
            )
        if flash := kwargs.get(ATTR_FLASH):
            self._lutron_device.flash(0.5 if flash == "short" else 1.5)
        else:
            if ATTR_BRIGHTNESS in kwargs and self._lutron_device.is_dimmable:
                brightness = kwargs[ATTR_BRIGHTNESS]
            elif self._prev_brightness == 0:
                brightness = 255
            else:
                brightness = self._prev_brightness
            self._prev_brightness = brightness
            args = {"new_level": to_lutron_level(brightness)}
            if ATTR_TRANSITION in kwargs:
                args["fade_time_seconds"] = kwargs[ATTR_TRANSITION]
            self._lutron_device.set_level(**args)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._is_fan:
            create_issue(
                self.hass,
                DOMAIN,
                "deprecated_light_fan_off",
                breaks_in_ha_version="2024.8.0",
                is_fixable=True,
                is_persistent=True,
                severity=IssueSeverity.WARNING,
                translation_key="deprecated_light_fan_off",
            )
        args = {"new_level": 0}
        if ATTR_TRANSITION in kwargs:
            args["fade_time_seconds"] = kwargs[ATTR_TRANSITION]
        self._lutron_device.set_level(**args)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes."""
        return {"lutron_integration_id": self._lutron_device.id}

    def _request_state(self) -> None:
        """Request the state from the device."""
        _ = self._lutron_device.level

    def _update_attrs(self) -> None:
        """Update the state attributes."""
        level = self._lutron_device.last_level()
        self._attr_is_on = level > 0
        hass_level = to_hass_level(level)
        self._attr_brightness = hass_level
        if self._prev_brightness is None or hass_level != 0:
            self._prev_brightness = hass_level


class LutronLedLight(LutronKeypad, LightEntity):
    """Representation of a Lutron Led."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_supported_features = LightEntityFeature.FLASH
    _lutron_device: Led
    _attr_name = None

    def __init__(self, area_name, device_name, keypad, lutron_device, controller):
        """Initialize the light."""
        super().__init__(area_name, device_name, lutron_device, controller, keypad)
        self._keypad_name = device_name
        self._attr_name = lutron_device.name
        # self._attr_name = f"{self._area_name} {self._keypad_name}: {self._lutron_device.name}"

    def turn_on(self, **kwargs):
        """Turn the light on."""
        self._lutron_device.state = 1

    def turn_off(self, **kwargs):
        """Turn the light off."""
        self._lutron_device.state = 0

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes."""
        return {
            "keypad": self._keypad_name,
            "led": self._lutron_device.name,
        }

    def _request_state(self) -> None:
        """Request the state from the device."""
        _ = self._lutron_device.state

    def _update_attrs(self) -> None:
        """Update the state attributes."""
        self._attr_is_on = self._lutron_device.last_state
