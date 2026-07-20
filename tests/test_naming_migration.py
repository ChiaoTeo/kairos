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
from kairos.adapters.base import (
    AccountAdapter,
    ExecutionAdapter,
    MarketDataAdapter,
    ReferenceDataAdapter,
)
from kairos.adapters.binance import (
    BinanceAccountGateway,
    BinanceExecutionGateway,
    BinanceMarketDataClient,
    BinanceSpotReferenceDataClient,
)
from kairos.adapters.binance.adapter import (
    BinanceAccountAdapter,
    BinanceExecutionAdapter,
    BinanceMarketDataAdapter,
    BinanceSpotReferenceAdapter,
)
from kairos.adapters.ibkr import (
    IbkrAccountGateway,
    IbkrExecutionGateway,
    IbkrMarketDataClient,
    IbkrReferenceDataClient,
)
from kairos.adapters.ibkr.adapter import (
    IbkrAccountAdapter,
    IbkrExecutionAdapter,
    IbkrMarketDataAdapter,
    IbkrReferenceAdapter,
)
from kairos.adapters.ibkr.research import IbkrSpxwResearchAdapter, IbkrSpxwResearchProvider
from kairos.adapters.composite import CompositeMarketDataAdapter, CompositeMarketDataClient
from kairos.adapters.massive import (
    MassiveEquityDailyOhlcvDatasetConnector,
    MassiveEquityDailyOhlcvPipeline,
    MassiveEquityDailyOhlcvProductConfig,
    MassiveEquityDayAggDatasetConnector,
    MassiveEquityDayAggPipeline,
    MassiveEquityDayAggProductConfig,
    OptionDailyOhlcvPipeline,
    OptionDayAggPipeline,
    SpxwDailyOhlcvPipeline,
    SpxwDayAggPipeline,
)
from kairos.adapters.simulated import SimulatedExecutionAccountAdapter, SimulatedExecutionAccountGateway
from kairos.adapters.transfer import (
    BankTransferAdapter,
    BankTransferGateway,
    BinanceTransferAdapter,
    BinanceTransferGateway,
)
from kairos.treasury.adapter import (
    SimulatedTransferAdapter,
    SimulatedTransferGateway,
    TransferAdapter,
    TransferGateway,
)


class NamingMigrationTests(unittest.TestCase):
    def test_port_names_alias_legacy_adapter_protocols(self) -> None:
        self.assertIs(ReferenceDataPort, ReferenceDataAdapter)
        self.assertIs(MarketDataPort, MarketDataAdapter)
        self.assertIs(ExecutionPort, ExecutionAdapter)
        self.assertIs(AccountPort, AccountAdapter)
        self.assertIs(ReferenceDataPortModuleExport, ReferenceDataPort)
        self.assertIs(MarketDataPortModuleExport, MarketDataPort)
        self.assertIs(ExecutionPortModuleExport, ExecutionPort)
        self.assertIs(AccountPortModuleExport, AccountPort)
        self.assertEqual(ReferenceDataPort.__name__, "ReferenceDataPort")
        self.assertEqual(ExecutionPort.__name__, "ExecutionPort")

    def test_binance_gateway_and_client_names_alias_legacy_adapters(self) -> None:
        self.assertIs(BinanceSpotReferenceDataClient, BinanceSpotReferenceAdapter)
        self.assertIs(BinanceMarketDataClient, BinanceMarketDataAdapter)
        self.assertIs(BinanceExecutionGateway, BinanceExecutionAdapter)
        self.assertIs(BinanceAccountGateway, BinanceAccountAdapter)
        self.assertEqual(BinanceExecutionGateway.__name__, "BinanceExecutionGateway")

    def test_ibkr_gateway_and_client_names_alias_legacy_adapters(self) -> None:
        self.assertIs(IbkrReferenceDataClient, IbkrReferenceAdapter)
        self.assertIs(IbkrMarketDataClient, IbkrMarketDataAdapter)
        self.assertIs(IbkrExecutionGateway, IbkrExecutionAdapter)
        self.assertIs(IbkrAccountGateway, IbkrAccountAdapter)
        self.assertEqual(IbkrReferenceDataClient.__name__, "IbkrReferenceDataClient")

    def test_local_gateway_and_composite_client_names_alias_legacy_adapters(self) -> None:
        self.assertIs(SimulatedExecutionAccountGateway, SimulatedExecutionAccountAdapter)
        self.assertIs(CompositeMarketDataClient, CompositeMarketDataAdapter)
        self.assertEqual(SimulatedExecutionAccountGateway.__name__, "SimulatedExecutionAccountGateway")
        self.assertEqual(CompositeMarketDataClient.__name__, "CompositeMarketDataClient")

    def test_research_and_transfer_gateway_names_alias_legacy_adapters(self) -> None:
        self.assertIs(IbkrSpxwResearchProvider, IbkrSpxwResearchAdapter)
        self.assertIs(BinanceTransferGateway, BinanceTransferAdapter)
        self.assertIs(BankTransferGateway, BankTransferAdapter)
        self.assertIs(TransferGateway, TransferAdapter)
        self.assertIs(SimulatedTransferGateway, SimulatedTransferAdapter)
        self.assertEqual(IbkrSpxwResearchProvider.__name__, "IbkrSpxwResearchProvider")
        self.assertEqual(BinanceTransferGateway.__name__, "BinanceTransferGateway")

    def test_daily_ohlcv_names_alias_massive_day_aggregate_classes(self) -> None:
        self.assertIs(OptionDailyOhlcvPipeline, OptionDayAggPipeline)
        self.assertIs(SpxwDailyOhlcvPipeline, SpxwDayAggPipeline)
        self.assertIs(MassiveEquityDailyOhlcvPipeline, MassiveEquityDayAggPipeline)
        self.assertIs(MassiveEquityDailyOhlcvProductConfig, MassiveEquityDayAggProductConfig)
        self.assertIs(MassiveEquityDailyOhlcvDatasetConnector, MassiveEquityDayAggDatasetConnector)


if __name__ == "__main__":
    unittest.main()
