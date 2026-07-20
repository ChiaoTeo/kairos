from __future__ import annotations

from dataclasses import dataclass
import math,statistics

from .bootstrap import block_bootstrap_mean_ci,newey_west_mean_t


@dataclass(frozen=True,slots=True)
class PredictabilityResult:
    observations: int
    pearson: float
    spearman: float
    conditional_effect: float
    confidence_interval: tuple[float,float]
    newey_west_t: float
    supported: bool


def validate_predictability(feature,target,*,high_threshold: float,expected_sign: int,block_length: int,
                            minimum_observations: int,seed: int=7) -> PredictabilityResult:
    pairs=[(float(x),float(y)) for x,y in zip(feature,target) if math.isfinite(float(x)) and math.isfinite(float(y))]
    x=[a for a,_ in pairs];y=[b for _,b in pairs];conditional=[b for a,b in pairs if a>=high_threshold]
    effect=statistics.fmean(conditional) if conditional else math.nan;ci=block_bootstrap_mean_ci(conditional,block_length,seed=seed)
    supported=len(conditional)>=minimum_observations and math.isfinite(ci[0]) and (ci[0]>0 if expected_sign>0 else ci[1]<0)
    return PredictabilityResult(len(pairs),_correlation(x,y),_correlation(_ranks(x),_ranks(y)),effect,ci,
        newey_west_mean_t(conditional,max(0,block_length-1)),supported)


def _correlation(x,y):
    if len(x)<2:return math.nan
    xm,ym=statistics.fmean(x),statistics.fmean(y);den=(sum((v-xm)**2 for v in x)*sum((v-ym)**2 for v in y))**.5
    return sum((a-xm)*(b-ym) for a,b in zip(x,y))/den if den else math.nan


def _ranks(values):
    ordered=sorted(range(len(values)),key=lambda index:values[index]);ranks=[0.0]*len(values);index=0
    while index<len(ordered):
        end=index+1
        while end<len(ordered) and values[ordered[end]]==values[ordered[index]]:end+=1
        rank=(index+end-1)/2+1
        for position in ordered[index:end]:ranks[position]=rank
        index=end
    return ranks
