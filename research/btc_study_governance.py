from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.research.validation import (
    CapitalSpec, DataCapabilities, EvidenceStatus, ExecutionArchetype, OutOfSampleEvidence,
    ProductProtocol, ResearchValidationResult, ReturnDriver, SampleSufficiency,
    StudyRegistration, ValidationArtifactWriter, ValidationLevel, ValidationState,
    approximate_required_samples, build_data_gap_plan,
    TestWindowRegistry, TestWindowUse,
)


@dataclass(frozen=True)
class GovernanceProfile:
    study_id: str
    hypothesis: str
    level: ValidationLevel
    signal: EvidenceStatus
    strategy: EvidenceStatus
    drivers: tuple[ReturnDriver, ...]
    products: tuple[ProductProtocol, ...]
    sample_kind: str
    execution: ExecutionArchetype = ExecutionArchetype.NONE
    source: str = "deribit"
    claim: str = "exploratory evidence"


PROFILES = (
    GovernanceProfile("btc_options_vrp_v1", "BTC option implied volatility exceeds future realized volatility",
        ValidationLevel.L2_SIGNAL, EvidenceStatus.EXPLORATORY, EvidenceStatus.NOT_TESTED,
        (ReturnDriver.VOLATILITY,), (ProductProtocol.OPTION,), "observations", claim="legacy 30D signal evidence"),
    GovernanceProfile("btc_term_vrp_v1", "fixed-maturity BTC ATM IV exceeds same-horizon future realized volatility",
        ValidationLevel.L2_SIGNAL, EvidenceStatus.SUPPORTED, EvidenceStatus.NOT_TESTED,
        (ReturnDriver.VOLATILITY,), (ProductProtocol.OPTION,), "term_7d", claim="7D volatility risk premium is supported"),
    GovernanceProfile("btc_skew_predictability_v1", "high BTC put skew predicts subsequent skew decline",
        ValidationLevel.L2_SIGNAL, EvidenceStatus.SUPPORTED, EvidenceStatus.NOT_TESTED,
        (ReturnDriver.SKEW,), (ProductProtocol.OPTION,), "skew_7d", claim="high put skew predicts mean reversion"),
    GovernanceProfile("btc_skew_cross_validation_v1", "Deribit trade skew and Binance quote skew measure a common signal",
        ValidationLevel.L2_SIGNAL, EvidenceStatus.EXPLORATORY, EvidenceStatus.NOT_TESTED,
        (ReturnDriver.SKEW,), (ProductProtocol.OPTION,), "paired_days", source="mixed", claim="cross-source skew association is descriptive"),
    GovernanceProfile("btc_skew_spread_backtest_v1", "high put skew can be monetized with an executable bull put spread",
        ValidationLevel.L3_MAPPING, EvidenceStatus.SUPPORTED, EvidenceStatus.EXPLORATORY,
        (ReturnDriver.SKEW, ReturnDriver.VOLATILITY), (ProductProtocol.OPTION,), "trades",
        ExecutionArchetype.TAKER, "binance", "short quote sample supports an exploratory spread mapping"),
    GovernanceProfile("btc_deribit_skew_spread_trade_proxy_v1", "high put skew can be monetized with a bull put spread",
        ValidationLevel.L3_MAPPING, EvidenceStatus.SUPPORTED, EvidenceStatus.TRADE_PROXY_ONLY,
        (ReturnDriver.SKEW, ReturnDriver.VOLATILITY), (ProductProtocol.OPTION,), "completed_trades",
        ExecutionArchetype.TAKER, "deribit", "signal maps to a losing long-history trade proxy"),
    GovernanceProfile("btc_deribit_skew_spread_daily_delta_hedged_v1", "daily delta hedging isolates skew and theta edge",
        ValidationLevel.L3_MAPPING, EvidenceStatus.EXPLORATORY, EvidenceStatus.TRADE_PROXY_ONLY,
        (ReturnDriver.SKEW, ReturnDriver.VOLATILITY), (ProductProtocol.OPTION, ProductProtocol.PERPETUAL), "trades",
        ExecutionArchetype.TAKER, "deribit", "delta hedge is an exploratory trade proxy"),
    GovernanceProfile("btc_deribit_skew_spread_delta_threshold_sensitivity_v1", "threshold delta hedging improves tail risk",
        ValidationLevel.L3_MAPPING, EvidenceStatus.EXPLORATORY, EvidenceStatus.TRADE_PROXY_ONLY,
        (ReturnDriver.SKEW, ReturnDriver.VOLATILITY), (ProductProtocol.OPTION, ProductProtocol.PERPETUAL), "trades",
        ExecutionArchetype.TAKER, "deribit", "same-sample threshold sensitivity is exploratory only"),
)


def migrate(root: str | Path = "data") -> tuple[Path, ...]:
    root=Path(root);repository=CanonicalDatasetRepository(root);periods=_periods(repository)
    windows=TestWindowRegistry(root/"studies"/"test_window_registry.jsonl")
    output=[]
    for profile in PROFILES:
        legacy=root/"studies"/profile.study_id
        result_path=legacy/"results.json"
        if not result_path.exists(): continue
        raw=json.loads(result_path.read_text(encoding="utf-8"))
        development,test=periods["binance" if profile.source=="binance" else "deribit"]
        windows.register(TestWindowUse(profile.study_id,"1.0.0",test[0],test[1],"time_oos",False))
        sample_count=_sample_count(profile,raw)
        effective=_effective_count(profile,raw,sample_count)
        required=approximate_required_samples(.5)
        executable=profile.level>=ValidationLevel.L4_EXECUTABLE
        capabilities=_capabilities(profile)
        execution_status=EvidenceStatus.SUPPORTED if executable else EvidenceStatus.DATA_NOT_READY if profile.execution is not ExecutionArchetype.NONE else EvidenceStatus.NOT_TESTED
        state=ValidationState(EvidenceStatus.READY,profile.signal,execution_status,profile.strategy,profile.level,profile.claim)
        capital=_capital() if profile.level>=ValidationLevel.L3_MAPPING else None
        registration=StudyRegistration(profile.study_id,"1.0.0",profile.hypothesis,profile.products,
            _archetypes(profile),profile.drivers,("direction","gamma","vega","liquidity"),
            profile.execution,development,None,test,("governed_legacy_features",),("registered_primary_metric",),(7,),
            "registered_primary_metric",required,"primary confidence interval supports hypothesis","acceptance rule not met",
            _required_capabilities(profile),capital)
        gaps=build_data_gap_plan(_missing(profile),target_samples=required,collection_frequency="hourly") if profile.level>=ValidationLevel.L3_MAPPING else build_data_gap_plan(())
        governed=ResearchValidationResult(registration,state,capabilities,
            SampleSufficiency(sample_count,max(0,int(effective)),effective,required,.5,.80,(),0),
            OutOfSampleEvidence.TIME,raw,tuple(raw.get("limitations",())) if isinstance(raw,dict) else (),gaps)
        report_path=legacy/"REPORT.md"
        report=report_path.read_text(encoding="utf-8") if report_path.exists() else _report(profile,governed)
        extras={"test_usage.json":{"test_period":list(test),"consumed":True,"decision_oos":False,
                "next_confirmatory_version_requires_new_window":True}}
        if profile.execution is not ExecutionArchetype.NONE:
            extras["execution_spec.json"]={"execution_archetype":profile.execution.value,"pricing_type":profile.strategy.value,
                "multi_leg_synchronous":False,"formal_execution_validation":False}
            trade_path=legacy/"trades.json"
            if not trade_path.exists():trade_path=legacy/"trades_by_threshold.json"
            extras["trades.json"]=json.loads(trade_path.read_text()) if trade_path.exists() else []
            risk_path=legacy/"risk_decomposition.json"
            extras["risk_decomposition.json"]=json.loads(risk_path.read_text()) if risk_path.exists() else {
                "status":"NOT_TESTED","reason":"legacy study did not produce an exact risk decomposition"}
            extras["equity_curve.json"]={"status":"DATA_NOT_READY","points":[],
                "reason":"execution capability and fixed-capital account curve are not validated"}
        output.append(ValidationArtifactWriter(root).write(governed,report=report,extra_artifacts=extras,
            extra_audit={"migration":"research.btc_study_governance","legacy_results":str(result_path.relative_to(root))}))
    return tuple(output)


def _periods(repository):
    def split(dataset):
        rows=repository.load_rows(dataset);dates=sorted(row["period_start"][:10] for row in rows);cut=int(len(dates)*.70)
        from datetime import date,timedelta
        return ((dates[0],dates[cut]),(dates[cut],(date.fromisoformat(dates[-1])+timedelta(days=1)).isoformat()))
    return {"deribit":split(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id),
            "binance":split(DataCatalog.BTC_TERM_SKEW_HOURLY.dataset_id)}


def _sample_count(profile,raw):
    if profile.sample_kind=="term_7d": return int(raw["horizons"]["7d"]["observations"])
    if profile.sample_kind=="skew_7d": return int(raw["horizons"]["7d"]["high_skew_test_observations"])
    value=raw.get(profile.sample_kind,0)
    if isinstance(value,(int,float)): return int(value)
    return int(raw.get("test_observations",0))


def _effective_count(profile,raw,count):
    if profile.sample_kind=="term_7d": return count/7
    if profile.sample_kind=="skew_7d": return count/7
    return float(count)


def _capabilities(profile):
    ids=[]
    if profile.source in ("deribit","mixed"): ids += [DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id,DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id]
    if profile.source in ("binance","mixed"): ids += [DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id,DataCatalog.BTC_TERM_SKEW_HOURLY.dataset_id]
    return DataCapabilities(tuple(ids),point_in_time_universe=True,
        synchronous_quotes=profile.source in ("binance","mixed"),top_of_book=profile.source in ("binance","mixed"),
        trade_events=profile.source in ("deribit","mixed"),trade_direction=profile.source in ("deribit","mixed"),
        supported_products=profile.products,maximum_validation_level=profile.level)


def _required_capabilities(profile):
    if profile.level<ValidationLevel.L3_MAPPING:return ("point_in_time_universe",)
    return ("synchronous_quotes","quote_size","settlement_price","lifecycle_events")


def _missing(profile):
    if profile.level<ValidationLevel.L3_MAPPING:return ()
    values=["quote_size","settlement_price","option_lifecycle_events"]
    if profile.source=="deribit":values.insert(0,"synchronous_multi_leg_quotes")
    if ProductProtocol.PERPETUAL in profile.products:values.append("funding")
    return tuple(values)


def _capital():return CapitalSpec(Decimal("100000"),"USD",Decimal(".02"),Decimal(".10"),"research_proxy_v1",True,False,"zero","stop_before_negative_equity")


def _archetypes(profile):
    if profile.level<ValidationLevel.L3_MAPPING:return ("signal_study",)
    values=[]
    if ReturnDriver.VOLATILITY in profile.drivers:values.append("short_volatility")
    if ReturnDriver.SKEW in profile.drivers:values.append("skew")
    if ProductProtocol.PERPETUAL in profile.products:values.append("delta_hedged")
    return tuple(values or ["strategy_mapping"])


def _report(profile,result):
    return f"# {profile.study_id}\n\n最高验证层级：`{profile.level.name}`。\n\n允许声明：{profile.claim}。\n\n状态：`{profile.strategy.value}`。\n"


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));args=parser.parse_args(argv)
    for path in migrate(args.data_root):print(path)


if __name__=="__main__":main()
