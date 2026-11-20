"""Persistent limits for uncached outbound requests in the hosted shop."""

from fastapi import HTTPException

from buywise_db import connect_db


USAGE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS buywise_provider_usage (
        provider TEXT NOT NULL,
        day DATE NOT NULL,
        visitor UUID NOT NULL,
        attempts INTEGER NOT NULL,
        PRIMARY KEY (provider, day, visitor)
    )
"""


class RequestLimit:
    def __init__(self, password, provider, daily, monthly, visitor_daily):
        self.password = password
        self.provider = provider
        self.daily = daily
        self.monthly = monthly
        self.visitor_daily = visitor_daily

    def claim(self, visitor):
        # Commit each attempt before making a network call, even when the call fails.
        # The advisory lock coordinates all server instances sharing the database.
        with connect_db(self.password) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("buywise:" + self.provider,))
            today = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date"
            daily, monthly, personal = conn.execute(
                f"""
                SELECT COALESCE(SUM(attempts) FILTER (WHERE day = {today}), 0),
                       COALESCE(SUM(attempts), 0),
                       COALESCE(SUM(attempts) FILTER (WHERE day = {today} AND visitor = %s), 0)
                FROM buywise_provider_usage
                WHERE provider = %s AND day >= date_trunc('month', {today})::date
                """,
                (visitor, self.provider),
            ).fetchone()
            if personal >= self.visitor_daily:
                raise HTTPException(429, "You've reached today's limit for new online checks. Try again tomorrow.")
            if daily >= self.daily or monthly >= self.monthly:
                raise HTTPException(429, "The shop has reached its limit for new online checks. Saved results are still available.")
            conn.execute(
                f"""
                INSERT INTO buywise_provider_usage (provider, day, visitor, attempts)
                VALUES (%s, {today}, %s, 1)
                ON CONFLICT (provider, day, visitor) DO UPDATE
                SET attempts = buywise_provider_usage.attempts + 1
                """,
                (self.provider, visitor),
            )
