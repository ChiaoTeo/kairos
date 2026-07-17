from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from trading import __version__
from trading.adapters.ibkr.research import SpxwResearchProvider
from trading.domain.event import OptionChainDiscovered, envelope
from trading.domain.market_state import MarketState, apply_market_event
from trading.storage.repository import FileResearchRepository, RunManifest, RunStatus, new_manifest

from .analyzer import ResearchResult, analyze
from .selector import select_instruments
from .snapshot import ResearchSnapshot, build_snapshot
from .spec import ResearchSpec


class ResearchService:
    def __init__(self, repository: FileResearchRepository) -> None:
        self.repository = repository

    def capture(self, provider: SpxwResearchProvider, spec: ResearchSpec) -> tuple[ResearchSnapshot, ResearchResult]:
        run_id = uuid4()
        manifest = new_manifest(run_id, spec, __version__)
        self.repository.create(manifest)
        stage = RunStatus.CONNECTING
        events = []
        try:
            manifest = self._status(manifest, stage)
            provider.connect()
            stage = RunStatus.DISCOVERING
            manifest = self._status(manifest, stage)
            underlying = provider.underlying(spec)
            underlying_events = provider.snapshot((underlying,), run_id)
            state = MarketState()
            for event in underlying_events:
                apply_market_event(state, event)
            events.extend(underlying_events)
            price_entry = state.underlying_prices.get(underlying.instrument_id)
            if price_entry is None:
                raise RuntimeError("IBKR returned no valid underlying price")
            chain = provider.discover_option_chain(underlying, spec)
            chain_event = envelope(OptionChainDiscovered(chain), source="ibkr.definition", correlation_id=run_id)
            apply_market_event(state, chain_event)
            events.append(chain_event)
            selected = select_instruments(provider.catalog, chain, price_entry[0], spec)
            qualified = tuple(provider.qualify(selected))
            if not qualified:
                raise RuntimeError("no selected option contract could be qualified")
            stage = RunStatus.COLLECTING
            manifest = self._status(replace(manifest, selected_contract_count=len(qualified)), stage)
            market_events = provider.snapshot(qualified, run_id)
            events.extend(market_events)
            for event in market_events:
                apply_market_event(state, event)
            snapshot = build_snapshot(
                run_id=run_id,
                spec=spec,
                underlying=underlying,
                chain=chain,
                selected=qualified,
                catalog=provider.catalog,
                state=state,
                code_version=__version__,
            )
            self.repository.save_chain(run_id, chain)
            self.repository.save_events(run_id, events)
            self.repository.save_snapshot(snapshot)
            stage = RunStatus.ANALYZING
            manifest = self._status(manifest, stage)
            result = analyze(snapshot)
            self.repository.save_report(run_id, result)
            has_error = any(issue.severity == "error" for issue in snapshot.quality_issues)
            final_status = RunStatus.PARTIAL if has_error or snapshot.quality_issues else RunStatus.COMPLETED
            manifest = replace(
                manifest,
                status=final_status,
                updated_at=datetime.now(timezone.utc),
                collected_event_count=len(events),
                quality_issue_count=len(snapshot.quality_issues),
                offline_analyzable=True,
            )
            self.repository.save_manifest(manifest)
            return snapshot, result
        except Exception as error:
            if events:
                self.repository.save_events(run_id, events)
            failed = replace(
                manifest,
                status=RunStatus.FAILED,
                updated_at=datetime.now(timezone.utc),
                collected_event_count=len(events),
                error_type=type(error).__name__,
                error_message=str(error),
                error_stage=stage.value,
                ibkr_error_code=getattr(error, "errorCode", None) or getattr(error, "code", None),
                offline_analyzable=False,
            )
            self.repository.save_manifest(failed)
            raise
        finally:
            provider.disconnect()

    def analyze_offline(self, run_id: UUID | str) -> ResearchResult:
        snapshot = self.repository.load_snapshot(run_id)
        result = analyze(snapshot)
        self.repository.save_report(run_id, result)
        return result

    def _status(self, manifest: RunManifest, status: RunStatus) -> RunManifest:
        updated = replace(manifest, status=status, updated_at=datetime.now(timezone.utc))
        self.repository.save_manifest(updated)
        return updated
