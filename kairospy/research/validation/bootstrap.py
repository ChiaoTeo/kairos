from __future__ import annotations

import math,random,statistics


def block_bootstrap_mean_ci(values,block_length: int,*,confidence: float=.95,iterations: int=2000,seed: int=7):
    values=[float(value) for value in values if math.isfinite(float(value))]
    if not values:return (math.nan,math.nan)
    if block_length<1 or iterations<100 or not 0<confidence<1:raise ValueError("invalid bootstrap configuration")
    rng=random.Random(seed);n=len(values);means=[]
    for _ in range(iterations):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n);sample.extend(values[(start+offset)%n] for offset in range(block_length))
        means.append(statistics.fmean(sample[:n]))
    means.sort();tail=(1-confidence)/2
    return (_percentile(means,tail),_percentile(means,1-tail))


def newey_west_mean_t(values,max_lag: int):
    values=[float(value) for value in values if math.isfinite(float(value))]
    if len(values)<2:return math.nan
    mean=statistics.fmean(values);centered=[value-mean for value in values];n=len(values)
    variance=sum(value*value for value in centered)/n
    for lag in range(1,min(max_lag,n-1)+1):
        covariance=sum(centered[index]*centered[index-lag] for index in range(lag,n))/n
        variance+=2*(1-lag/(max_lag+1))*covariance
    standard_error=math.sqrt(max(variance,0)/n)
    return mean/standard_error if standard_error else math.nan


def _percentile(values,q):
    position=(len(values)-1)*q;low=int(position);high=min(low+1,len(values)-1);weight=position-low
    return values[low]*(1-weight)+values[high]*weight
