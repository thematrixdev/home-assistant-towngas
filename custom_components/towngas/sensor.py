from __future__ import annotations

import datetime
import logging

import aiohttp
import async_timeout
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.lock import PLATFORM_SCHEMA
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TIMEOUT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle

DOMAIN = "TownGas"

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_TIMEOUT, default=30): cv.positive_int,
})

MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(seconds=3600)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.101 Safari/537.36"

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    session = aiohttp_client.async_get_clientsession(hass)
    name = config.get(CONF_NAME)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    timeout = config.get(CONF_TIMEOUT)

    async_add_entities(
        [
            TownGasSensor(
                session=session,
                name=name,
                username=username,
                password=password,
                timeout=timeout,
            ),
        ],
        update_before_add=True,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    await async_setup_platform(hass, {}, async_add_entities)


class TownGasSensor(SensorEntity):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        name: str,
        username: str,
        password: str,
        timeout: int,
    ) -> None:
        self._session = session
        self._name = name
        self._username = username
        self._password = password
        self._timeout = timeout

        self._attr_device_class = SensorDeviceClass.GAS
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = 'Megajoules'
        self._attr_state_class = SensorStateClass.TOTAL

        self._account_number = None
        self._readings = []
        self._bills = []

    @property
    def state_class(self) -> SensorStateClass | str | None:
        return SensorStateClass.TOTAL

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def extra_state_attributes(self) -> dict:
        return {
            'readings': self._readings,
            'bills': self._bills,
        }

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self) -> None:
        try:
            async with async_timeout.timeout(self._timeout):
                response = await self._session.request(
                    method="POST",
                    url="https://eservice.towngas.com/EAccount/Login/SignIn",
                    headers={
                        "user-agent": USER_AGENT,
                    },
                    data={
                        "LoginID": self._username,
                        "password": self._password,
                        "Language": "zh-HK",
                    },
                )
                response.raise_for_status()

            async with async_timeout.timeout(self._timeout):
                response = await self._session.request(
                    method="POST",
                    url="https://eservice.towngas.com/Common/GetHostedTGAccountAsync",
                    headers={
                        "user-agent": USER_AGENT,
                    },
                )
                response.raise_for_status()
                json = await response.json()
                self._account_number = json[0]

            async with async_timeout.timeout(self._timeout):
                response = await self._session.request(
                    method="POST",
                    url="https://eservice.towngas.com/Common/GetMeterReadingInfoForChat",
                    headers={
                        "user-agent": USER_AGENT,
                    },
                    data={
                        "accountNo": self._account_number,
                        "language": "zh-HK",
                        "isAccountInfo": "true",
                        "isHousehold": "true",
                    },
                )
                response.raise_for_status()
                json = await response.json()

                for record in json['chartBarList']:
                    if record['strMonth1'] and record['consumption1']:
                        self._readings.append({
                            'time': record['strMonth1'],
                            'mj': record['consumption1'],
                        })

                    if record['strMonth2'] and record['consumption2']:
                        self._readings.append({
                            'time': record['strMonth2'],
                            'mj': record['consumption2'],
                        })

                    if record['isEstimateMonth'] and record['strMonth1'] and record['predictionConsumption']:
                        self._readings.append({
                            'time': record['strMonth1'],
                            'mj': record['predictionConsumption'],
                        })

                        self._attr_native_value = record['predictionConsumption']

                self._readings.reverse()

            async with async_timeout.timeout(self._timeout):
                response = await self._session.request(
                    method="POST",
                    url='https://eservice.towngas.com/EBilling/GetEBillingInfo',
                    headers={
                        "user-agent": USER_AGENT,
                    },
                    data={
                        "accountNo": self._account_number,
                    },
                )
                response.raise_for_status()
                json = await response.json()

                for record in json['list']:
                    self._bills.append({
                        'time': record['strBillDate'],
                        'total': int(record['total'].replace('HK $', '').replace('.00', '')),
                    })
        except Exception as e:
            print(e, flush=True)

