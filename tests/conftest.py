"""Réglages communs des tests."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


@pytest.fixture(autouse=True)
async def _stop_mqtt_misc_timer(hass):
    """Le client MQTT simulé (HA >= 2026.10) laisse un minuteur « misc » : on le déclenche pour qu'il s'arrête."""
    yield
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
