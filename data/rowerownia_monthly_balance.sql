WITH monthly_totals AS (
    SELECT strftime('%Y-%m', started_at) AS month,
           SUM(charge_cents) AS revenue_cents,
           0 AS service_cost_cents
    FROM rentals
    GROUP BY strftime('%Y-%m', started_at)

    UNION ALL

    SELECT strftime('%Y-%m', service_date) AS month,
           0 AS revenue_cents,
           SUM(cost_cents) AS service_cost_cents
    FROM service_records
    GROUP BY strftime('%Y-%m', service_date)
)
SELECT month,
       SUM(revenue_cents) / 100.0 AS revenue_pln,
       SUM(service_cost_cents) / 100.0 AS service_cost_pln,
       (SUM(revenue_cents) - SUM(service_cost_cents)) / 100.0
           AS revenue_after_service_pln
FROM monthly_totals
GROUP BY month
ORDER BY month;
