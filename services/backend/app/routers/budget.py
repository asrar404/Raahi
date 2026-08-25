"""Budget tracking routes.

`trips.total_actual_cost` is a denormalised running total. It is recomputed
from `expense_logs` inside the same transaction as every write rather than
being incremented, so a retried or concurrent request cannot double-count and
falsely push a traveller over their ceiling.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.dependencies import CurrentUser, DbConn, Paginate
from app.models.budget import BudgetAlert, BudgetSummary, ExpenseCreate, ExpenseOut
from app.services.ws_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()

# Warn at 80% — early enough that the traveller can still choose a cheaper
# option for their remaining legs.
WARN_THRESHOLD_PCT = 80.0


async def _assert_owner(conn, trip_id: str, user_id: str) -> None:
    row = await conn.fetchrow(
        "SELECT id FROM trips WHERE id = $1 AND user_id = $2", trip_id, user_id
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")


async def _budget_status(conn, trip_id: str) -> dict[str, Any]:
    """Read the budget position via fn_trip_budget_status."""
    row = await conn.fetchrow("SELECT * FROM fn_trip_budget_status($1)", trip_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    data = dict(row)
    # PL/pgSQL returns NUMERIC, which asyncpg maps to Decimal
    return {
        "ceiling": float(data["ceiling"] or 0),
        "planned": float(data["planned"] or 0),
        "spent": float(data["spent"] or 0),
        "remaining": float(data["remaining"] or 0),
        "percent_used": float(data["percent_used"] or 0),
        "over_budget": bool(data["over_budget"]),
    }


def _severity(percent_used: float, over_budget: bool) -> str:
    if over_budget:
        return "critical"
    if percent_used >= WARN_THRESHOLD_PCT:
        return "warning"
    return "ok"


@router.post("/log", response_model=BudgetSummary, status_code=status.HTTP_201_CREATED)
async def log_expense(
    payload: ExpenseCreate, user: CurrentUser, conn: DbConn
) -> BudgetSummary:
    """Record an expense and return the updated budget position.

    Broadcasts BUDGET_ALERT when the trip crosses the warning threshold, so
    the map screen can surface it without polling.
    """
    trip_id = str(payload.trip_id)
    await _assert_owner(conn, trip_id, str(user["id"]))

    if payload.leg_id is not None:
        leg = await conn.fetchrow(
            "SELECT id FROM trip_legs WHERE id = $1 AND trip_id = $2",
            str(payload.leg_id), trip_id,
        )
        if leg is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="leg_id does not belong to this trip",
            )

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO expense_logs (trip_id, leg_id, amount, category, description)
            VALUES ($1, $2, $3, $4, $5)
            """,
            trip_id,
            str(payload.leg_id) if payload.leg_id else None,
            payload.amount,
            payload.category.value,
            payload.description,
        )
        # Recompute rather than increment — idempotent under retries.
        await conn.execute(
            """
            UPDATE trips
            SET total_actual_cost = COALESCE(
                (SELECT SUM(amount) FROM expense_logs WHERE trip_id = $1), 0
            )
            WHERE id = $1
            """,
            trip_id,
        )
        # Attribute transit spend to its leg so per-leg actuals stay accurate
        if payload.leg_id is not None and payload.category.value == "transit":
            await conn.execute(
                """
                UPDATE trip_legs
                SET actual_cost = COALESCE(
                    (SELECT SUM(amount) FROM expense_logs WHERE leg_id = $1), 0
                )
                WHERE id = $1
                """,
                str(payload.leg_id),
            )

    status_data = await _budget_status(conn, trip_id)
    logs = await _fetch_logs(conn, trip_id, limit=50, offset=0)

    severity = _severity(status_data["percent_used"], status_data["over_budget"])
    if severity != "ok":
        await manager.send_budget_alert(trip_id, {
            "trip_id": trip_id,
            "severity": severity,
            **status_data,
        })
        log.info("Budget %s for trip %s (%.1f%% of ceiling used)",
                 severity, trip_id, status_data["percent_used"])

    return BudgetSummary(trip_id=payload.trip_id, logs=logs, **status_data)


async def _fetch_logs(conn, trip_id: str, limit: int, offset: int) -> list[ExpenseOut]:
    rows = await conn.fetch(
        """
        SELECT id, trip_id, leg_id,
               amount::DOUBLE PRECISION AS amount,
               category, description, recorded_at
        FROM expense_logs
        WHERE trip_id = $1
        ORDER BY recorded_at DESC
        LIMIT $2 OFFSET $3
        """,
        trip_id, limit, offset,
    )
    return [ExpenseOut(**dict(r)) for r in rows]


@router.get("/{trip_id}", response_model=BudgetSummary)
async def get_budget(
    trip_id: str, user: CurrentUser, conn: DbConn, page: Paginate
) -> BudgetSummary:
    """Ceiling, spend, remaining and the expense log for one trip."""
    await _assert_owner(conn, trip_id, str(user["id"]))
    status_data = await _budget_status(conn, trip_id)
    logs = await _fetch_logs(conn, trip_id, page.limit, page.offset)
    return BudgetSummary(trip_id=trip_id, logs=logs, **status_data)


@router.get("/{trip_id}/alert", response_model=BudgetAlert)
async def get_budget_alert(trip_id: str, user: CurrentUser, conn: DbConn) -> BudgetAlert:
    """Cheap poll for the budget banner — no expense log attached."""
    await _assert_owner(conn, trip_id, str(user["id"]))
    s = await _budget_status(conn, trip_id)
    return BudgetAlert(
        trip_id=trip_id,
        over_budget=s["over_budget"],
        percent_used=s["percent_used"],
        remaining=s["remaining"],
        approaching_limit=s["percent_used"] >= WARN_THRESHOLD_PCT and not s["over_budget"],
        severity=_severity(s["percent_used"], s["over_budget"]),
    )


@router.delete("/log/{expense_id}", response_model=BudgetSummary)
async def delete_expense(
    expense_id: str, user: CurrentUser, conn: DbConn
) -> BudgetSummary:
    """Remove a mis-entered expense and recompute the trip total."""
    row = await conn.fetchrow(
        """
        SELECT e.id, e.trip_id, e.leg_id
        FROM expense_logs e JOIN trips t ON t.id = e.trip_id
        WHERE e.id = $1 AND t.user_id = $2
        """,
        expense_id, user["id"],
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")

    trip_id = str(row["trip_id"])
    async with conn.transaction():
        await conn.execute("DELETE FROM expense_logs WHERE id = $1", expense_id)
        await conn.execute(
            """
            UPDATE trips
            SET total_actual_cost = COALESCE(
                (SELECT SUM(amount) FROM expense_logs WHERE trip_id = $1), 0
            )
            WHERE id = $1
            """,
            trip_id,
        )
        if row["leg_id"] is not None:
            await conn.execute(
                """
                UPDATE trip_legs
                SET actual_cost = COALESCE(
                    (SELECT SUM(amount) FROM expense_logs WHERE leg_id = $1), 0
                )
                WHERE id = $1
                """,
                str(row["leg_id"]),
            )

    status_data = await _budget_status(conn, trip_id)
    logs = await _fetch_logs(conn, trip_id, 50, 0)
    return BudgetSummary(trip_id=trip_id, logs=logs, **status_data)


@router.get("/{trip_id}/breakdown")
async def get_breakdown(trip_id: str, user: CurrentUser, conn: DbConn) -> dict[str, Any]:
    """Spend grouped by category, plus planned-vs-actual per leg."""
    await _assert_owner(conn, trip_id, str(user["id"]))

    by_category = await conn.fetch(
        """
        SELECT category,
               SUM(amount)::DOUBLE PRECISION AS total,
               COUNT(*) AS entries
        FROM expense_logs
        WHERE trip_id = $1
        GROUP BY category
        ORDER BY total DESC
        """,
        trip_id,
    )

    by_leg = await conn.fetch(
        """
        SELECT leg_order, mode, from_name, to_name,
               planned_cost::DOUBLE PRECISION AS planned_cost,
               actual_cost::DOUBLE PRECISION  AS actual_cost
        FROM trip_legs
        WHERE trip_id = $1
        ORDER BY leg_order
        """,
        trip_id,
    )

    return {
        "trip_id": trip_id,
        "by_category": [dict(r) for r in by_category],
        "by_leg": [dict(r) for r in by_leg],
        **await _budget_status(conn, trip_id),
    }
