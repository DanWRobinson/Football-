from .api_football import APIFootball
from .football_data import FootballData
from .fotmob import FotMob
from .understat import Understat

REGISTRY = {s.name: s for s in (FootballData, Understat, FotMob, APIFootball)}

__all__ = ["REGISTRY", "FootballData", "Understat", "FotMob", "APIFootball"]
