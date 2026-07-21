from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from kairospy.connectors import (
    DataPlaneEndpoint, ExecutionService, ExecutionServiceSpec, HistoricalMarketDataService, ProviderCodec,
    ProviderConnector, ProviderDataPlane, ProviderDataPlaneSpec, ProviderEstimate, ProviderEvent, ProviderHealth,
    ProviderResource, ProviderResourceSpec, ProviderService, ProviderServiceSpec, ProviderTransport,
    SourceArtifact, TransportRequest, TransportResponse,
)
from kairospy.data import (
    DataProductBuilderRegistry, DataProductTaskPlan, DatasetBuildResult, EquityOhlcvDataProductBuilder,
    EquityOhlcvSourceBinding, DatasetPublisher, ProductSourceBinding, ProviderRegistry, TaskRangePlan, UniversePlan,
    equity_hourly_ohlcv_rows, equity_ohlcv_schema, equity_symbol,
)
from kairospy.data.products import US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY
from kairospy.connectors.binance.execution_gateway import (
    BinanceExecutionGateway, BinanceOptionsExecutionGateway,
)
from kairospy.connectors.ibkr.execution_gateway import IbkrExecutionGateway
from kairospy.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.trading.identity import AccountKey, AccountType, InstitutionId, VenueId
from kairospy.ports import Environment


class ProviderDataBoundaryTests(unittest.TestCase):
    def test_provider_boundary_artifacts_validate_identity(self):
        artifact = SourceArtifact(
            provider="massive",
            service="historical_market_data",
            resource="equity_ohlcv",
            request_fingerprint="abc",
            coverage_hint={"boundary": "[start,end)"},
        )
        self.assertEqual(artifact.provider, "massive")
        self.assertEqual(ProviderEstimate(1, instruments=2).instruments, 2)
        event = ProviderEvent(
            provider="binance",
            service="live_market_data",
            resource="depth",
            received_at=datetime.now(timezone.utc),
            payload={"bids": []},
            sequence=1,
        )
        self.assertEqual(event.sequence, 1)
        self.assertEqual(ProviderHealth("massive", "ok").status, "ok")
        with self.assertRaises(ValueError):
            SourceArtifact("", "historical_market_data", "equity_ohlcv", "abc")

    def test_data_product_builder_boundary_types_are_user_dataset_oriented(self):
        binding = ProductSourceBinding(
            product_key="market.ohlcv.equity.us.massive.1d.vendor_adjusted",
            provider="massive",
            service="historical_market_data",
            resource="equity_ohlcv",
            params={"interval": "1d"},
        )
        result = DatasetBuildResult(
            dataset_id="market.ohlcv.equity.us.1d",
            status="ready_for_backtest",
            build_id="build-test",
            coverage={"rows": 2},
        )
        self.assertEqual(binding.resource, "equity_ohlcv")
        self.assertEqual(result.dataset_id, "market.ohlcv.equity.us.1d")
        self.assertEqual(result.build_id, "build-test")
        self.assertEqual(DataProductBuilderRegistry().builders(), ())
        self.assertTrue(DatasetPublisher("/tmp/kairospy-test"))
        self.assertTrue(ProviderConnector)
        self.assertTrue(ProviderService)
        self.assertTrue(ProviderResource)
        self.assertTrue(HistoricalMarketDataService)

    def test_provider_service_transport_codec_boundaries_are_provider_neutral(self):
        resource = ProviderResourceSpec(
            "aggregate_bars",
            "historical_market_data",
            path="/v2/aggs/ticker/{symbol}",
            method="GET",
        )
        service = ProviderServiceSpec("historical_market_data", "market_data", resources=(resource,))
        request = TransportRequest("aggregate_bars", "fetch", payload={"symbol": "AAPL"})
        response = TransportResponse("ok", payload={"results": []}, receipt={"request_id": "test"})

        self.assertEqual(service.resources[0].resource_id, "aggregate_bars")
        self.assertEqual(request.payload["symbol"], "AAPL")
        self.assertEqual(response.receipt["request_id"], "test")
        self.assertTrue(ProviderTransport)
        self.assertTrue(ProviderCodec)
        with self.assertRaises(ValueError):
            ProviderResourceSpec("", "historical_market_data")

    def test_provider_data_plane_boundary_is_runtime_neutral(self):
        endpoint = DataPlaneEndpoint(
            "external_process",
            address="unix:///tmp/kairos-massive.sock",
            format="arrow-ipc",
            metadata={"runtime": "cpp"},
        )
        spec = ProviderDataPlaneSpec(
            "massive-ohlcv-plane",
            "historical_market_data",
            endpoint,
            features=("bounded-replay", "zero-copy-ready"),
        )

        class FakeDataPlane:
            plane_id = spec.plane_id
            service_id = spec.service_id

            def describe(self):
                return spec

        self.assertEqual(spec.endpoint.protocol, "external_process")
        self.assertEqual(spec.endpoint.format, "arrow-ipc")
        self.assertFalse(spec.side_effecting)
        self.assertIsInstance(FakeDataPlane(), ProviderDataPlane)
        with self.assertRaises(ValueError):
            DataPlaneEndpoint("")

    def test_data_task_planning_primitives_preserve_cli_shape(self):
        plan = DataProductTaskPlan(
            "massive",
            "rest-paginated-aggregate",
            (TaskRangePlan(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
                tasks=3,
                cached=1,
            ),),
            universe=UniversePlan("bounded", 3),
            metadata={"resume_supported": True},
        ).to_primitive()

        self.assertEqual(plan["provider"], "massive")
        self.assertEqual(plan["total_tasks"], 3)
        self.assertEqual(plan["cached_tasks"], 1)
        self.assertEqual(plan["uncached_tasks"], 2)
        self.assertEqual(plan["universe"], "bounded")
        self.assertEqual(plan["symbols"], 3)
        self.assertEqual(plan["ranges"][0]["uncached"], 2)

    def test_execution_service_boundary_uses_ports_not_provider_dtos(self):
        class FakeExecutionService:
            service_id = "execution"
            service_kind = "execution"
            institution_id = "binance"
            venue_id = "binance"
            environment = "testnet"
            capabilities = object()

            def place_order(self, request):
                raise NotImplementedError

            def cancel_order(self, account, venue_order_id):
                raise NotImplementedError

            def open_orders(self, account):
                return ()

            def recover_order(self, account, request, venue_order_id):
                raise NotImplementedError

        spec = ExecutionServiceSpec("execution", "binance", "binance", "testnet")
        self.assertEqual(spec.service_kind, "execution")
        self.assertIsInstance(FakeExecutionService(), ExecutionService)
        with self.assertRaises(ValueError):
            ExecutionServiceSpec("", "binance", "binance", "testnet")

    def test_existing_execution_gateways_advertise_provider_service_boundary(self):
        class FakeTransport:
            pass

        class FakeSigner:
            pass

        spot = BinanceExecutionGateway(FakeTransport(), FakeSigner(), Environment.TESTNET)
        futures = BinanceExecutionGateway(FakeTransport(), FakeSigner(), Environment.TESTNET, futures=True)
        coinm = BinanceExecutionGateway(
            FakeTransport(), FakeSigner(), Environment.TESTNET, futures=True, inverse=True,
        )
        options = BinanceOptionsExecutionGateway(FakeTransport(), FakeSigner(), Environment.LIVE)
        ibkr = IbkrExecutionGateway(SimpleNamespace(readonly=True, contracts={}, ib=object()), Environment.PAPER)
        simulated = SimulatedExecutionAccountGateway(
            VenueId("simulated"),
            AccountKey(InstitutionId("simulated"), "account-1", AccountType.CRYPTO_SPOT),
        )

        self.assertEqual(spot.service_id, "spot_execution")
        self.assertEqual(futures.service_id, "usdm_futures_execution")
        self.assertEqual(coinm.service_id, "coinm_futures_execution")
        self.assertEqual(options.service_id, "options_execution")
        for service in (spot, futures, coinm, options, ibkr, simulated):
            self.assertEqual(service.service_kind, "execution")
            self.assertIsInstance(service, ExecutionService)

    def test_equity_ohlcv_canonical_helpers_live_in_data_layer(self):
        rows = list(equity_hourly_ohlcv_rows(
            "AAPL",
            "adjusted",
            [{"t": 1767364200000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1000, "n": 5, "vw": 100.25}],
            datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 2, 15, 31, tzinfo=timezone.utc),
        ))
        self.assertEqual(rows[0]["instrument_id"], "equity:us:AAPL")
        self.assertEqual(rows[0]["interval"], "PT1H")
        self.assertEqual(rows[0]["price_view"], "adjusted")
        schema = equity_ohlcv_schema("market.ohlcv.equity.us.1h.v1", "PT1H")
        self.assertEqual(schema["primary_key"], ["venue", "instrument_id", "period_start", "interval"])

    def test_equity_ohlcv_builder_is_data_product_oriented(self):
        binding = EquityOhlcvSourceBinding(
            product=US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
            provider="massive",
            venue="us-securities",
            view="adjusted",
            adjusted=True,
            interval="PT1H",
            timespan="hour",
            aggregate_request=lambda symbol, multiplier, timespan, start, end, adjusted: {
                "symbol": symbol,
                "timespan": timespan,
                "adjusted": adjusted,
            },
            source_dataset="stocks_hourly_aggregates",
            transform_id="massive.equity_hourly_ohlcv",
            producer_transform="massive_equity_hourly_aggregate_to_ohlcv",
            cost_class="entitled-rest-full-market-hourly",
        )
        builder = EquityOhlcvDataProductBuilder(
            "/tmp/kairospy-test",
            market_data_service=object(),
            binding=binding,
            discover_symbols=lambda: ("AAPL",),
            estimate_symbol_count=lambda: 8000,
        )
        request = SimpleNamespace(
            missing=(SimpleNamespace(
                start=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
                end=datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc),
            ),),
            instruments=("equity:us:AAPL",),
        )
        estimate = builder.estimate(request)
        self.assertTrue(builder.supports(str(US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY.key)))
        self.assertEqual(estimate.requests, 1)
        self.assertEqual(equity_symbol("equity:us:AAPL"), "AAPL")

    def test_missing_product_builder_error_uses_user_terms(self):
        with self.assertRaisesRegex(RuntimeError, "no Data Product builder registered"):
            ProviderRegistry().get("massive", "market.ohlcv.equity.us.massive.1d.vendor_adjusted")


if __name__ == "__main__":
    unittest.main()
