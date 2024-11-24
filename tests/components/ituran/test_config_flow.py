"""Test the Ituran config flow."""

from unittest.mock import AsyncMock

from pyituran.exceptions import IturanApiError, IturanAuthError

from homeassistant.components.ituran.const import (
    CONF_ID_OR_PASSPORT,
    CONF_MOBILE_ID,
    CONF_OTP,
    CONF_PHONE_NUMBER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import setup_integration
from .const import MOCK_CONFIG_DATA

from tests.common import MockConfigEntry


async def __do_successful_user_step(
    hass: HomeAssistant, result: ConfigFlowResult, mock_ituran: AsyncMock
):
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "otp"
    assert result2["errors"] == {}

    return result2


async def __do_successful_otp_step(
    hass: HomeAssistant,
    result: ConfigFlowResult,
    mock_ituran: AsyncMock,
    reauth: bool = False,
):
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_OTP: "123456",
        },
    )

    if reauth:
        assert result2["type"] is FlowResultType.ABORT
    else:
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == f"Ituran {MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]}"
        assert (
            result2["data"][CONF_ID_OR_PASSPORT]
            == MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]
        )
        assert result2["data"][CONF_PHONE_NUMBER] == MOCK_CONFIG_DATA[CONF_PHONE_NUMBER]
        assert result2["data"][CONF_MOBILE_ID] is not None
        assert result2["result"].unique_id == MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]
    assert len(mock_ituran.is_authenticated.mock_calls) > 0
    assert len(mock_ituran.authenticate.mock_calls) > 0

    return result2


async def test_full_user_flow(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test the full user configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await __do_successful_user_step(hass, result, mock_ituran)
    await __do_successful_otp_step(hass, result2, mock_ituran)


async def test_invalid_auth(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test invalid credentials configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_ituran.request_otp.side_effect = IturanAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}

    mock_ituran.request_otp.side_effect = None
    result2 = await __do_successful_user_step(hass, result, mock_ituran)
    await __do_successful_otp_step(hass, result2, mock_ituran)


async def test_invalid_otp(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test invalid OTP configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await __do_successful_user_step(hass, result, mock_ituran)

    mock_ituran.authenticate.side_effect = IturanAuthError
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        user_input={
            CONF_OTP: "123456",
        },
    )

    assert result3["type"] is FlowResultType.FORM
    assert result3["errors"] == {"base": "invalid_otp"}

    mock_ituran.authenticate.side_effect = None
    await __do_successful_otp_step(hass, result3, mock_ituran)


async def test_cannot_connect(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test connection errors during configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_ituran.request_otp.side_effect = IturanApiError
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "cannot_connect"}

    mock_ituran.request_otp.side_effect = None
    result3 = await __do_successful_user_step(hass, result2, mock_ituran)

    mock_ituran.authenticate.side_effect = IturanApiError
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        user_input={
            CONF_OTP: "123456",
        },
    )

    assert result4["type"] is FlowResultType.FORM
    assert result4["errors"] == {"base": "cannot_connect"}

    mock_ituran.authenticate.side_effect = None
    await __do_successful_otp_step(hass, result4, mock_ituran)


async def test_unexpected_errors(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test unexpected errors during configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_ituran.request_otp.side_effect = Exception
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "unknown"}

    mock_ituran.request_otp.side_effect = None
    result3 = await __do_successful_user_step(hass, result2, mock_ituran)

    mock_ituran.authenticate.side_effect = Exception
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        user_input={
            CONF_OTP: "123456",
        },
    )

    assert result4["type"] is FlowResultType.FORM
    assert result4["errors"] == {"base": "unknown"}

    mock_ituran.authenticate.side_effect = None
    await __do_successful_otp_step(hass, result4, mock_ituran)


async def test_already_authenticated(
    hass: HomeAssistant, mock_ituran: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Test user already authenticated configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_ituran.is_authenticated.return_value = True
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
            CONF_MOBILE_ID: MOCK_CONFIG_DATA[CONF_MOBILE_ID],
        },
    )

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == f"Ituran {MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]}"
    assert result2["data"][CONF_ID_OR_PASSPORT] == MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]
    assert result2["data"][CONF_PHONE_NUMBER] == MOCK_CONFIG_DATA[CONF_PHONE_NUMBER]
    assert result2["data"][CONF_MOBILE_ID] == MOCK_CONFIG_DATA[CONF_MOBILE_ID]
    assert result2["result"].unique_id == MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT]


async def test_reauth(
    hass: HomeAssistant,
    mock_ituran: AsyncMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reauthenticating."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result2 = await __do_successful_user_step(hass, result, mock_ituran)
    await __do_successful_otp_step(hass, result2, mock_ituran)

    await setup_integration(hass, mock_config_entry)
    result4 = await mock_config_entry.start_reauth_flow(hass)

    assert result4["type"] is FlowResultType.FORM
    assert result4["step_id"] == "user"
    assert result4["errors"] == {}

    result5 = await __do_successful_user_step(hass, result4, mock_ituran)
    await __do_successful_otp_step(hass, result5, mock_ituran, True)


async def test_reauth_when_already_authenticated(
    hass: HomeAssistant,
    mock_ituran: AsyncMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reauthenticating when already authenticated."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result2 = await __do_successful_user_step(hass, result, mock_ituran)
    await __do_successful_otp_step(hass, result2, mock_ituran)

    await setup_integration(hass, mock_config_entry)
    result4 = await mock_config_entry.start_reauth_flow(hass)

    assert result4["type"] is FlowResultType.FORM
    assert result4["step_id"] == "user"
    assert result4["errors"] == {}

    mock_ituran.is_authenticated.return_value = True
    result5 = await hass.config_entries.flow.async_configure(
        result4["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT],
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
            CONF_MOBILE_ID: MOCK_CONFIG_DATA[CONF_MOBILE_ID],
        },
    )

    assert result5["type"] is FlowResultType.ABORT


async def test_reauth_with_different_id(
    hass: HomeAssistant,
    mock_ituran: AsyncMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reauthenticating with a different ID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result2 = await __do_successful_user_step(hass, result, mock_ituran)
    await __do_successful_otp_step(hass, result2, mock_ituran)

    await setup_integration(hass, mock_config_entry)
    result4 = await mock_config_entry.start_reauth_flow(hass)

    assert result4["type"] is FlowResultType.FORM
    assert result4["step_id"] == "user"
    assert result4["errors"] == {}

    result5 = await hass.config_entries.flow.async_configure(
        result4["flow_id"],
        user_input={
            CONF_ID_OR_PASSPORT: MOCK_CONFIG_DATA[CONF_ID_OR_PASSPORT] + "1",
            CONF_PHONE_NUMBER: MOCK_CONFIG_DATA[CONF_PHONE_NUMBER],
        },
    )

    assert result5["type"] is FlowResultType.ABORT
    assert result5["reason"] == "id_mismatch"
