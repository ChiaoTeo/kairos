from __future__ import annotations

import unittest

from kairos.ports import (
    AccountPort,
    ExecutionPort,
    MarketDataPort,
    ReferenceDataPort,
)
from kairos.ports.account import AccountPort as AccountPortModuleExport
from kairos.ports.execution import ExecutionPort as ExecutionPortModuleExport
from kairos.ports.market_data import MarketDataPort as MarketDataPortModuleExport
from kairos.ports.reference_data import ReferenceDataPort as ReferenceDataPortModuleExport
from kairos.adapters.binance import (
    BinanceAccountGateway,
    BinanceExecutionGateway,
    BinanceMarketDataClient,
    BinanceSpotReferenceDataClient,
)
from kairos.adapters.ibkr import (
    IbkrAccountGateway,
    IbkrExecutionGateway,
    IbkrMarketDataClient,
    IbkrReferenceDataClient,
)
from kairos.adapters.ibkr.research import IbkrSpxwResearchProvider
from kairos.adapters.market_data_router import CompositeMarketDataClient
from kairos.adapters.massive import (
    MassiveEquityDailyOhlcvDatasetConnector,
    MassiveEquityDailyOhlcvPipeline,
    MassiveEquityDailyOhlcvProductConfig,
    OptionDailyOhlcvPipeline,
    SpxwDailyOhlcvPipeline,
)
from kairos.adapters.simulated import SimulatedExecutionAccountGateway
from kairos.adapters.transfer import (
    BankTransferGateway,
    BinanceTransferGateway,
)
from kairos.treasury.transfer_gateway import (
    SimulatedTransferGateway,
    TransferGateway,
)


class NamingMigrationTests(unittest.TestCase):
    def test_port_names_are_public_protocol_exports(self) -> None:
        self.assertIs(ReferenceDataPortModuleExport, ReferenceDataPort)
        self.assertIs(MarketDataPortModuleExport, MarketDataPort)
        self.assertIs(ExecutionPortModuleExport, ExecutionPort)
        self.assertIs(AccountPortModuleExport, AccountPort)
        self.assertEqual(ReferenceDataPort.__name__, "ReferenceDataPort")
        self.assertEqual(ExecutionPort.__name__, "ExecutionPort")

    def test_binance_gateway_and_client_names_are_public(self) -> None:
        self.assertEqual(BinanceSpotReferenceDataClient.__name__, "BinanceSpotReferenceDataClient")
        self.assertEqual(BinanceMarketDataClient.__name__, "BinanceMarketDataClient")
        self.assertEqual(BinanceExecutionGateway.__name__, "BinanceExecutionGateway")
        self.assertEqual(BinanceAccountGateway.__name__, "BinanceAccountGateway")

    def test_ibkr_gateway_and_client_names_are_public(self) -> None:
        self.assertEqual(IbkrReferenceDataClient.__name__, "IbkrReferenceDataClient")
        self.assertEqual(IbkrMarketDataClient.__name__, "IbkrMarketDataClient")
        self.assertEqual(IbkrExecutionGateway.__name__, "IbkrExecutionGateway")
        self.assertEqual(IbkrAccountGateway.__name__, "IbkrAccountGateway")

    def test_local_gateway_and_composite_client_names_are_public(self) -> None:
        self.assertEqual(SimulatedExecutionAccountGateway.__name__, "SimulatedExecutionAccountGateway")
        self.assertEqual(CompositeMarketDataClient.__name__, "CompositeMarketDataClient")

    def test_research_and_transfer_gateway_names_are_public(self) -> None:
        self.assertEqual(IbkrSpxwResearchProvider.__name__, "IbkrSpxwResearchProvider")
        self.assertEqual(BinanceTransferGateway.__name__, "BinanceTransferGateway")
        self.assertEqual(BankTransferGateway.__name__, "BankTransferGateway")
        self.assertEqual(TransferGateway.__name__, "TransferGateway")
        self.assertEqual(SimulatedTransferGateway.__name__, "SimulatedTransferGateway")

    def test_daily_ohlcv_names_are_the_massive_public_classes(self) -> None:
        self.assertEqual(OptionDailyOhlcvPipeline.__name__, "OptionDailyOhlcvPipeline")
        self.assertEqual(SpxwDailyOhlcvPipeline.__name__, "SpxwDailyOhlcvPipeline")
        self.assertEqual(MassiveEquityDailyOhlcvPipeline.__name__, "MassiveEquityDailyOhlcvPipeline")
        self.assertEqual(MassiveEquityDailyOhlcvProductConfig.__name__, "MassiveEquityDailyOhlcvProductConfig")
        self.assertEqual(MassiveEquityDailyOhlcvDatasetConnector.__name__, "MassiveEquityDailyOhlcvDatasetConnector")


if __name__ == "__main__":
    unittest.main()
