from __future__ import annotations

from dataclasses import dataclass
from datetime import date,timedelta


@dataclass(frozen=True,slots=True)
class TimeSplit:
    development: tuple[date,date]
    validation: tuple[date,date]|None
    test: tuple[date,date]
    embargo_days: int=0

    def __post_init__(self):
        windows=[self.development,*(([self.validation]) if self.validation else []),self.test]
        if any(start>=end for start,end in windows):raise ValueError("time splits use increasing [start, end) windows")
        for first,second in zip(windows,windows[1:]):
            if first[1]+timedelta(days=self.embargo_days)>second[0]:raise ValueError("time splits overlap or violate embargo")

    def label(self,value: date) -> str|None:
        if self.development[0]<=value<self.development[1]:return "development"
        if self.validation and self.validation[0]<=value<self.validation[1]:return "validation"
        if self.test[0]<=value<self.test[1]:return "test"
        return None


def chronological_split(start: date,end: date,*,development_fraction: float=.6,validation_fraction: float=.2,embargo_days: int=0) -> TimeSplit:
    if start>=end or not 0<development_fraction<1 or not 0<=validation_fraction<1 or development_fraction+validation_fraction>=1:
        raise ValueError("invalid chronological split configuration")
    total=(end-start).days;dev_end=start+timedelta(days=int(total*development_fraction));val_start=dev_end+timedelta(days=embargo_days)
    if validation_fraction:
        val_end=start+timedelta(days=int(total*(development_fraction+validation_fraction)))
        test_start=val_end+timedelta(days=embargo_days);validation=(val_start,val_end)
    else:test_start=val_start;validation=None
    return TimeSplit((start,dev_end),validation,(test_start,end),embargo_days)


def walk_forward_splits(start: date,end: date,*,development_days: int,test_days: int,step_days: int,embargo_days: int=0):
    if min(development_days,test_days,step_days)<1:raise ValueError("walk-forward windows must be positive")
    cursor=start
    while cursor+timedelta(days=development_days+embargo_days+test_days)<=end:
        dev_end=cursor+timedelta(days=development_days);test_start=dev_end+timedelta(days=embargo_days);test_end=test_start+timedelta(days=test_days)
        yield TimeSplit((cursor,dev_end),None,(test_start,test_end),embargo_days);cursor+=timedelta(days=step_days)
