"""펌프별 정상 기준선 (F2-3).

정답 라벨을 쓰지 않고, 임계값 기준으로 '정상 범위'에 든 운전 행만으로 추정한다.
  기대값 = 펌프 중앙값 + 시간대(0~22시) 효과 + 날짜별 전 펌프 공통 효과(계절·외기)
  표준편차 = 잔차의 MAD × 1.4826 (하한 min_std)
비율 항목(유량·토출/흡입압력·전류)은 정격 대비 비율 공간에서 계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import RATIO_VARS, VARIABLES


def to_model_space(df: pd.DataFrame, rated: pd.DataFrame) -> pd.DataFrame:
    """측정값 → 모델 공간(비율 항목은 정격 대비). rated: pump 별 정격 (index=pump)."""
    out = pd.DataFrame(index=df.index)
    for v in VARIABLES:
        if v in RATIO_VARS:
            out[v] = df[v] / df["pump"].map(rated[v])
        else:
            out[v] = df[v]
    return out


def running_mask(df: pd.DataFrame) -> pd.Series:
    return df["state"].eq("운전") if "state" in df else pd.Series(True, index=df.index)


@dataclass
class BaselineModel:
    rated: pd.DataFrame                 # index pump, columns VARIABLES 중 비율 항목
    pump_median: pd.DataFrame           # index pump, columns VARIABLES
    hour_effect: pd.DataFrame           # index (pump, hour)
    day_offset: pd.DataFrame            # index date
    std: pd.DataFrame                   # index pump
    recent_offset: pd.Series            # 데이터 이후 시각에 쓸 계절 보정

    @classmethod
    def fit(cls, ctx) -> "BaselineModel":
        cfg = ctx.cfg["baseline"]
        rated = pd.DataFrame({p: info.rated for p, info in ctx.pumps.items()}).T
        ops = ctx.ops[running_mask(ctx.ops)]
        x = to_model_space(ops, rated)
        nr = cfg["normal_range"]
        ok = ((x["vibration"] <= nr["vibration_max"]) & (x["bearing_temp"] <= nr["bearing_temp_max"])
              & (x["current"] <= nr["current_ratio_max"]) & x["discharge"].between(*nr["discharge_ratio"])
              & (x["suction"] >= nr["suction_ratio_min"]) & (x["flow"] >= nr["flow_ratio_min"]))
        x = x[ok]
        meta = ops.loc[x.index, ["pump", "time"]]
        pump_median = x.groupby(meta["pump"]).median()
        r1 = x - pump_median.reindex(meta["pump"]).set_axis(x.index)
        keys = [meta["pump"], meta["time"].dt.hour]
        hour_effect = r1.groupby(keys).median()
        hour_effect.index.names = ["pump", "hour"]
        r2 = r1 - hour_effect.reindex(pd.MultiIndex.from_arrays(keys)).set_axis(x.index)
        date = meta["time"].dt.normalize()
        day_offset = r2.groupby(date).median()
        r3 = r2 - day_offset.reindex(date).set_axis(x.index)
        mad = (r3 - r3.groupby(meta["pump"]).transform("median")).abs().groupby(meta["pump"]).median() * 1.4826
        floor = pd.Series(cfg["min_std"])
        std = mad.clip(lower=floor, axis=1)
        recent = day_offset.tail(int(cfg["future_offset_days"])).mean()
        return cls(rated, pump_median, hour_effect, day_offset, std, recent)

    def expected(self, df: pd.DataFrame) -> pd.DataFrame:
        """df(pump, time) 각 행의 기대값(모델 공간)."""
        pm = self.pump_median.reindex(df["pump"]).set_axis(df.index)
        he = self.hour_effect.reindex(pd.MultiIndex.from_arrays([df["pump"], df["time"].dt.hour])).set_axis(df.index)
        do = self.day_offset.reindex(df["time"].dt.normalize()).set_axis(df.index)
        do = do.fillna(self.recent_offset)
        return pm + he.fillna(0) + do.fillna(0)

    def zscores(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """(모델 공간 값, 기대값, z-score) — 결측 입력은 NaN 유지."""
        x = to_model_space(df, self.rated)
        exp = self.expected(df)[VARIABLES]
        sd = self.std.reindex(df["pump"]).set_axis(df.index)[VARIABLES]
        z = (x[VARIABLES] - exp) / sd
        return x[VARIABLES], exp, z

    def to_value(self, pump: str, variable: str, model_value: float) -> float:
        """모델 공간 값 → 원래 단위."""
        if variable in RATIO_VARS:
            return float(model_value * self.rated.loc[pump, variable])
        return float(model_value)


def anomaly_index(z: pd.DataFrame, deadzone: float) -> pd.Series:
    excess = (z.abs() - deadzone).clip(lower=0)
    return np.sqrt((excess ** 2).sum(axis=1, min_count=1)).fillna(0)
