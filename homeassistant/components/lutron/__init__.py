"""Component for interacting with a Lutron RadioRA 2 system."""

from dataclasses import dataclass
import logging

from pylutron import Button, Keypad, Led, Lutron, OccupancyGroup, Output, Sysvar
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_REFRESH_DATA,
    CONF_USE_AREA_FOR_DEVICE_NAME,
    CONF_USE_FULL_PATH,
    DOMAIN,
    LUTRON_DATA_FILE,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Attribute on events that indicates what action was taken with the button.
ATTR_ACTION = "action"
ATTR_FULL_ID = "full_id"
ATTR_UUID = "uuid"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_REFRESH_DATA, default=True): cv.boolean,
                vol.Required(CONF_USE_FULL_PATH, default=False): cv.boolean,
                vol.Required(CONF_USE_AREA_FOR_DEVICE_NAME, default=False): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def _async_import(hass: HomeAssistant, base_config: ConfigType) -> None:
    """Import a config entry from configuration.yaml."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=base_config[DOMAIN],
    )
    if (
        result["type"] == FlowResultType.CREATE_ENTRY
        or result["reason"] == "single_instance_allowed"
    ):
        async_create_issue(
            hass,
            HOMEASSISTANT_DOMAIN,
            f"deprecated_yaml_{DOMAIN}",
            breaks_in_ha_version="2024.7.0",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
            translation_placeholders={
                "domain": DOMAIN,
                "integration_title": "Lutron",
            },
        )
        return
    async_create_issue(
        hass,
        DOMAIN,
        f"deprecated_yaml_import_issue_{result['reason']}",
        breaks_in_ha_version="2024.7.0",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=IssueSeverity.WARNING,
        translation_key=f"deprecated_yaml_import_issue_{result['reason']}",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "Lutron",
        },
    )


async def async_setup(hass: HomeAssistant, base_config: ConfigType) -> bool:
    """Set up the Lutron component."""
    if DOMAIN in base_config:
        hass.async_create_task(_async_import(hass, base_config))
    return True


@dataclass(slots=True, kw_only=True)
class LutronData:
    """Storage class for platform global data."""

    client: Lutron
    binary_sensors: list[tuple[str, OccupancyGroup]]
    buttons: list[tuple[str, str, Keypad, Button]]
    covers: list[tuple[str, str, Output]]
    fans: list[tuple[str, str, Output]]
    lights: list[tuple[str, str, Output]]
    leds: list[tuple[str, str, Keypad, Led]]
    scenes: list[tuple[str, str, Keypad, Button, Led]]
    switches: list[tuple[str, str, Output]]
    variables: list[tuple[str, Sysvar]]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the Lutron integration."""

    host = config_entry.data[CONF_HOST]
    uid = config_entry.data[CONF_USERNAME]
    pwd = config_entry.data[CONF_PASSWORD]
    refresh_data = config_entry.data[CONF_REFRESH_DATA]
    use_full_path = config_entry.data[CONF_USE_FULL_PATH]
    use_area_for_device_name = config_entry.data[CONF_USE_AREA_FOR_DEVICE_NAME]

    lutron_data_file = hass.config.path(LUTRON_DATA_FILE)
    lutron_variable_ids = [155, 158]

    lutron_client = Lutron(host, uid, pwd)
    await hass.async_add_executor_job(
        lambda: lutron_client.load_xml_db(
            lutron_data_file, refresh_data, variable_ids=lutron_variable_ids
        )
    )
    lutron_client.connect()
    _LOGGER.info("Connected to main repeater at %s", host)

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entry_data = LutronData(
        client=lutron_client,
        binary_sensors=[],
        buttons=[],
        covers=[],
        fans=[],
        lights=[],
        leds=[],
        scenes=[],
        switches=[],
        variables=[],
    )
    # Sort our devices into types
    _LOGGER.debug("Start adding devices")
    for area in lutron_client.areas:
        area_name = area.name if not use_full_path else area.location + " " + area.name
        _LOGGER.debug("Working on area %s", area.name)
        for output in area.outputs:
            platform = None
            device_name = (
                output.name
                if not use_area_for_device_name
                else area_name + " " + output.name
            )
            _LOGGER.debug("Working on output %s", output.type)
            if output.type in ("SYSTEM_SHADE", "MOTOR"):
                entry_data.covers.append((area_name, device_name, output))
                platform = Platform.COVER
            elif output.type == "CEILING_FAN_TYPE":
                entry_data.fans.append((area_name, device_name, output))
                platform = Platform.FAN
                # Deprecated, should be removed in 2024.8
                entry_data.lights.append((area_name, device_name, output))
            elif output.is_light:
                entry_data.lights.append((area_name, device_name, output))
                platform = Platform.LIGHT
            else:
                entry_data.switches.append((area_name, device_name, output))
                platform = Platform.SWITCH

            _async_check_entity_unique_id(
                hass,
                entity_registry,
                platform,
                output.uuid,
                output.legacy_uuid,
                entry_data.client.guid,
            )
            _async_check_device_identifiers(
                hass,
                device_registry,
                output.uuid,
                output.legacy_uuid,
                entry_data.client.guid,
            )

        for keypad in area.keypads:
            device_name = (
                keypad.name
                if not use_area_for_device_name
                else area_name + " " + keypad.name
            )
            for button in keypad.buttons:
                # If the button has a function assigned to it, add it as a scene
                if button.name != "Unknown Button" and button.button_type in (
                    "SingleAction",
                    "Toggle",
                    "SingleSceneRaiseLower",
                    "MasterRaiseLower",
                    "DualAction",
                    "AdvancedToggle",
                    "AdvancedConditional",
                    "SimpleConditional",
                ):
                    # Associate an LED with a button if there is one
                    led = next(
                        (led for led in keypad.leds if led.number == button.number),
                        None,
                    )
                    entry_data.scenes.append(
                        (area_name, device_name, keypad, button, led)
                    )

                    # Add the LED as a light device if is controlled via integration
                    if led is not None and button.led_logic == 5:
                        entry_data.leds.append((area_name, device_name, keypad, led))

                    platform = Platform.SCENE
                    _async_check_entity_unique_id(
                        hass,
                        entity_registry,
                        platform,
                        button.uuid,
                        button.legacy_uuid,
                        entry_data.client.guid,
                    )
                    if led is not None:
                        platform = Platform.SWITCH
                        _async_check_entity_unique_id(
                            hass,
                            entity_registry,
                            platform,
                            led.uuid,
                            led.legacy_uuid,
                            entry_data.client.guid,
                        )
                if button.button_type:
                    entry_data.buttons.append((area_name, device_name, keypad, button))
        # exclude occupancy_group not linked to an area
        if area.occupancy_group is not None and area.occupancy_group.id != 0:
            entry_data.binary_sensors.append((area_name, area.occupancy_group))
            platform = Platform.BINARY_SENSOR
            _async_check_entity_unique_id(
                hass,
                entity_registry,
                platform,
                area.occupancy_group.uuid,
                area.occupancy_group.legacy_uuid,
                entry_data.client.guid,
            )
            _async_check_device_identifiers(
                hass,
                device_registry,
                area.occupancy_group.uuid,
                area.occupancy_group.legacy_uuid,
                entry_data.client.guid,
            )
    # check variables
    for variable in lutron_client.variables:
        _LOGGER.debug("Working on variable %s", variable.name)
        platform = Platform.SENSOR
        entry_data.variables.append((variable.name, variable))

        _async_check_entity_unique_id(
            hass,
            entity_registry,
            platform,
            "",
            variable.legacy_uuid,
            entry_data.client.guid,
        )
        _async_check_device_identifiers(
            hass,
            device_registry,
            "",
            variable.legacy_uuid,
            entry_data.client.guid,
        )

    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, lutron_client.guid)},
        manufacturer="Lutron",
        name="Main repeater",
    )

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = entry_data

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


def _async_check_entity_unique_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    platform: str,
    uuid: str,
    legacy_uuid: str,
    controller_guid: str,
) -> None:
    """If uuid becomes available update to use it."""

    if not uuid:
        return

    unique_id = f"{controller_guid}_{legacy_uuid}"
    entity_id = entity_registry.async_get_entity_id(
        domain=platform, platform=DOMAIN, unique_id=unique_id
    )

    if entity_id:
        new_unique_id = f"{controller_guid}_{uuid}"
        _LOGGER.debug("Updating entity id from %s to %s", unique_id, new_unique_id)
        entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


def _async_check_device_identifiers(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    uuid: str,
    legacy_uuid: str,
    controller_guid: str,
) -> None:
    """If uuid becomes available update to use it."""

    if not uuid:
        return

    unique_id = f"{controller_guid}_{legacy_uuid}"
    device = device_registry.async_get_device(identifiers={(DOMAIN, unique_id)})
    if device:
        new_unique_id = f"{controller_guid}_{uuid}"
        _LOGGER.debug("Updating device id from %s to %s", unique_id, new_unique_id)
        device_registry.async_update_device(
            device.id, new_identifiers={(DOMAIN, new_unique_id)}
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Clean up resources and entities associated with the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
