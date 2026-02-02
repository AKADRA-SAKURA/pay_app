from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, List

from .context import AdviceContext

Level = Literal["info", "warn", "danger"]


@dataclass
class AdviceResult:
    title: str
    level: Level
    bullets: list[str]


def _yen(n: int) -> str:
    return f"{n:,}円"


def generate_advice_rules(ctx: AdviceContext) -> AdviceResult:
    """
    ルールベースで「今日の一言」を作る
    """
    bullets: list[str] = []
    level: Level = "info"

    # ルール1：最低値が0未満（危険）
    if ctx.min_value < 0:
        level = "danger"
        bullets.append(f"この期間で一番厳しいのは {ctx.min_date}（{_yen(ctx.min_value)}）になりそう。")

    # ルール2：最低値が近い（7日以内）
    if ctx.days_to_min <= 7 and ctx.min_value >= 0:
        level = "warn"
        bullets.append(f"直近 {ctx.days_to_min}日以内（{ctx.min_date}）に底が来そう：最低 {_yen(ctx.min_value)}。")

    # ルール3：期末が悪化
    if ctx.end < ctx.start:
        # danger優先、warn優先
        if level == "info":
            level = "warn"
        bullets.append(f"期末見込みが下がり気味（{_yen(ctx.start)} → {_yen(ctx.end)}）。固定費/単発を一度だけ棚卸しすると効く。")
    else:
        bullets.append(f"期末見込みは維持〜改善（{_yen(ctx.start)} → {_yen(ctx.end)}）。この調子でOK。")

    # ルール4：7日トレンド（補助コメント）
    if ctx.trend_7d < 0:
        bullets.append(f"直近7日で {_yen(ctx.trend_7d)}。少額の連続支出が増えてないかだけチェック。")
    elif ctx.trend_7d > 0:
        bullets.append(f"直近7日で +{_yen(ctx.trend_7d)}。良い流れ。")

    # bullets が多すぎると鬱陶しいので最大3つに
    bullets = bullets[:3]

    return AdviceResult(
        title="今日の一言",
        level=level,
        bullets=bullets,
    )
