"""Общие перечисления для всех слоёв приложения."""

from enum import Enum


class MovementType(str, Enum):
    INCOME = "INCOME"
    TRANSFER = "TRANSFER"
    FBS_RESERVE = "FBS_RESERVE"
    FBS_CANCEL = "FBS_CANCEL"
    FBS_SHIP = "FBS_SHIP"
    FBO_SHIP = "FBO_SHIP"
    RECEIVE = "RECEIVE"


class MovementStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
