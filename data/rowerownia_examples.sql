-- Zapytania referencyjne uruchamiaj w osobnym narzędziu SQLite; czat nie przyjmuje SQL.
-- Dane są syntetyczne. Kwoty zapisano w groszach; dzielimy je przez 100.0.

-- 1. Przychód i liczba wypożyczeń w kolejnych miesiącach.
SELECT strftime('%Y-%m', started_at) AS month,
       COUNT(*) AS rental_count,
       SUM(charge_cents) / 100.0 AS revenue_pln
FROM rentals
GROUP BY month
ORDER BY month;

-- 2. Popularność, czas wypożyczenia i przychód według typu roweru.
SELECT b.bike_type,
       COUNT(*) AS rental_count,
       ROUND(AVG(r.duration_minutes), 1) AS average_duration_minutes,
       SUM(r.charge_cents) / 100.0 AS revenue_pln
FROM rentals AS r
JOIN bikes AS b ON b.bike_id = r.bike_id
GROUP BY b.bike_type
ORDER BY rental_count DESC;

-- 3. Pięciu klientów, którzy wydali najwięcej.
SELECT c.full_name, c.city,
       COUNT(*) AS rental_count,
       SUM(r.charge_cents) / 100.0 AS spending_pln
FROM customers AS c
JOIN rentals AS r ON r.customer_id = c.customer_id
GROUP BY c.customer_id, c.full_name, c.city
ORDER BY spending_pln DESC, c.customer_id
LIMIT 5;

-- 4. Stacje według liczby rozpoczętych wypożyczeń.
SELECT s.name AS station,
       COUNT(r.rental_id) AS rental_count
FROM stations AS s
LEFT JOIN rentals AS r ON r.pickup_station_id = s.station_id
GROUP BY s.station_id, s.name
ORDER BY rental_count DESC, s.station_id;

-- 5. Klienci, którzy nie wypożyczyli żadnego roweru.
SELECT c.full_name, c.city
FROM customers AS c
WHERE NOT EXISTS (
    SELECT 1 FROM rentals AS r WHERE r.customer_id = c.customer_id
)
ORDER BY c.customer_id;

-- 6. Koszt serwisu według rodzaju pracy.
SELECT service_type,
       COUNT(*) AS service_count,
       SUM(cost_cents) / 100.0 AS service_cost_pln
FROM service_records
GROUP BY service_type
ORDER BY service_cost_pln DESC;

-- 7. Przychód pomniejszony tylko o koszty serwisu (nie pełny zysk firmy).
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

-- 8. Czas i dystans poszczególnych wypożyczeń.
SELECT duration_minutes, distance_km
FROM rentals
ORDER BY rental_id
LIMIT 50;
