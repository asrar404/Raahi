"""Budget and expense schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExpenseCategory(str, Enum):
    TRANSIT = "transit"
    FOOD = "food"
    STAY = "stay"
    MISC = "misc"


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    trip_id: UUID
    amount: float = Field(gt=0, le=1_000_000)
    category: ExpenseCategory = ExpenseCategory.MISC
    description: Optional[str] = Field(default=None, max_length=280)
    leg_id: Optional[UUID] = None


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    leg_id: Optional[UUID] = None
    amount: float
    category: str
    description: Optional[str] = None
    recorded_at: datetime


class BudgetSummary(BaseModel):
    """Everything the ExpenseWidget renders in one payload."""

    trip_id: UUID
    ceiling: float
    planned: float = 0
    spent: float = 0
    remaining: float = 0
    percent_used: float = 0
    over_budget: bool = False
    logs: List[ExpenseOut] = Field(default_factory=list)


class BudgetAlert(BaseModel):
    """Lightweight polling endpoint for the budget warning banner."""

    trip_id: UUID
    over_budget: bool = False
    percent_used: float = 0
    remaining: float = 0
    # Crossed 80% — warn before the ceiling is actually hit, since the
    # traveller may still be able to pick a cheaper remaining leg.
    approaching_limit: bool = False
    severity: str = Field(default="ok", description="ok | warning | critical")
