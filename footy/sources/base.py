"""Source adapter interface.

Each adapter implements whichever of these it can. The pipeline calls them per league
and writes the results into the database; adapters never touch the DB directly, which
keeps them easy to test with saved payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import League


@dataclass
class MatchRecord:
    league: str
    season: int
    date: str                  # yyyy-mm-dd
    home: str                  # provider's name
    away: str
    home_goals: int | None = None
    away_goals: int | None = None
    kickoff: str | None = None
    home_stats: dict = field(default_factory=dict)   # metric -> value
    away_stats: dict = field(default_factory=dict)
    odds: list[tuple] = field(default_factory=list)  # (market, selection, book, price, line)
    source_id: str | None = None
    referee: str | None = None
    #: missing players: (side 'h'/'a', player name, status 'out'/'doubt', reason, expected return)
    unavailable: list[tuple] = field(default_factory=list)


@dataclass
class PlayerRecord:
    league: str
    season: int
    player_id: str
    player: str
    team: str
    position: str | None = None
    stats: dict = field(default_factory=dict)        # column -> value (see db.players)


class Source:
    name = "base"
    #: does this source need an API key / token to run?
    requires_key: str | None = None

    def available(self) -> bool:
        return True

    def supports(self, lg: League) -> bool:
        return True

    def matches(self, lg: League, season: int) -> list[MatchRecord]:
        return []

    def fixtures(self, lg: League) -> list[MatchRecord]:
        return []

    def players(self, lg: League, season: int) -> list[PlayerRecord]:
        return []
