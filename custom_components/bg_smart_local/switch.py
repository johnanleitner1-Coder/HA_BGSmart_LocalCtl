"""Support for BG Smart Local Control switches (sockets / power-only outlets).

This platform creates one Home Assistant ``switch`` entity per device that
exposes a writable ``Power`` parameter but no ``brightness`` (i.e. sockets,
not dimmers). On a BG Smart Double Socket the device reports two such
entries, ``Left Socket`` and ``Right Socket``, so two independent switches
are created.
"""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Param names that are device/service metadata rather than controllable
# outlets. These can contain a "Power"-looking key in some firmwares and must
# never be turned into switch entities.
_NON_OUTLET_KEYS = {
    "SocketName",
    "cycle_timer",
    "Time",
    "Schedule",
    "Scenes",
    "System",
    "ui-identify",
}


def _user_socket_name(params: dict) -> str | None:
    """Return the user-assigned socket name from the SocketName service, if any."""
    try:
        return params.get("SocketName", {}).get("Name")
    except AttributeError:
        return None


def _is_power_only_switch(device_name: str, device_params: Any) -> bool:
    """Return True for socket-style outlets: a writable Power, no brightness.

    Service/metadata blocks (Time, Schedule, System, ...) are excluded by name
    so we never expose them as switches even if a future firmware adds a
    Power-like field to them.
    """
    if device_name in _NON_OUTLET_KEYS:
        return False
    return (
        isinstance(device_params, dict)
        and "Power" in device_params
        and "brightness" not in device_params
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BG Smart socket switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device = data["device"]
    coordinator = data["coordinator"]

    _LOGGER.info("Setting up BG Smart Local switches v0.2.0")

    try:
        params = coordinator.data
        _LOGGER.debug("Switch setup sees device params: %s", params)

        if not params:
            _LOGGER.error("No params found in device properties")
            return

        socket_label = _user_socket_name(params)

        entities = []
        for device_name, device_params in params.items():
            if _is_power_only_switch(device_name, device_params):
                _LOGGER.info("Creating switch entity for socket outlet: %s", device_name)
                entities.append(
                    BGSmartSwitch(
                        coordinator, device, device_name, device_params, entry, socket_label
                    )
                )
            else:
                _LOGGER.debug("Skipping non-switch device: %s", device_name)

        if entities:
            _LOGGER.info("Adding %s switch entities", len(entities))
            async_add_entities(entities)
        else:
            _LOGGER.info("No socket (power-only) devices found in parameters")

    except Exception as ex:  # noqa: BLE001 - log and continue, never crash setup
        _LOGGER.error("Failed to set up switches: %s", ex, exc_info=True)


class BGSmartSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a single BG Smart socket outlet."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device,
        device_name: str,
        device_params: dict,
        entry: ConfigEntry,
        socket_label: str | None = None,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)

        self._device = device
        self._device_name = device_name

        # Prefer an explicit per-outlet Name if the firmware provides one,
        # then fall back to "<user socket name> - <outlet>" so a user who
        # named the socket (e.g. "Piano Room") sees recognisable entities,
        # finally to the raw outlet key.
        per_outlet_name = device_params.get("Name")
        if per_outlet_name:
            friendly_name = per_outlet_name
        elif socket_label:
            friendly_name = f"{socket_label} - {device_name}"
        else:
            friendly_name = device_name

        self._attr_unique_id = f"{entry.entry_id}_{device_name}_switch"
        self._attr_name = friendly_name

        self._update_from_params(device_params)

        _LOGGER.info(
            "Initialized switch: %s (device key: %s) - Power: %s",
            friendly_name,
            device_name,
            self._attr_is_on,
        )

    def _update_from_params(self, device_params: dict) -> None:
        """Update entity state from device parameters."""
        self._attr_is_on = bool(device_params.get("Power", False))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data and self._device_name in self.coordinator.data:
            device_params = self.coordinator.data[self._device_name]
            self._update_from_params(device_params)
            _LOGGER.debug(
                "%s updated from coordinator - Power: %s",
                self._device_name,
                self._attr_is_on,
            )
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the socket outlet."""
        _LOGGER.debug("Turn on %s", self._device_name)
        try:
            success = await self._device.set_param(self._device_name, "Power", True)
            if success:
                self._attr_is_on = True
                self.async_write_ha_state()
                await self.coordinator.async_request_refresh()
                _LOGGER.info("Successfully turned on %s", self._device_name)
            else:
                _LOGGER.error("Failed to turn on %s", self._device_name)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error turning on %s: %s", self._device_name, ex, exc_info=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the socket outlet."""
        _LOGGER.debug("Turn off %s", self._device_name)
        try:
            success = await self._device.set_param(self._device_name, "Power", False)
            if success:
                self._attr_is_on = False
                self.async_write_ha_state()
                await self.coordinator.async_request_refresh()
                _LOGGER.info("Successfully turned off %s", self._device_name)
            else:
                _LOGGER.error("Failed to turn off %s", self._device_name)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error turning off %s: %s", self._device_name, ex, exc_info=True)
