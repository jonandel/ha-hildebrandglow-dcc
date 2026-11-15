"""Shared fixtures.

Tests that set up the integration through Home Assistant request
``enable_custom_integrations`` themselves. It is not autouse because it pulls
in ``hass`` before ``recorder_mock``, which the recorder tests need first.
"""
