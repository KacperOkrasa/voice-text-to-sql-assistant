# Rowerownia — pytania kontrolne 11–20

Wyniki policzono z `data/rowerownia.db` (domyślny seed 2026) 2026-10-05.
Każde zapytanie przeszło walidację i wykonanie tylko do odczytu z aplikacji.
Wszystkie odpowiedzi sprawdzono niezależnie w Pythonie na rekordach z bazy, bez API.
Kwoty są w PLN; średnie i procenty zaokrąglono do dwóch miejsc.
Nazwy kolumn i kolejność wierszy przy remisach mogą się różnić; porównuj wartości.

Pierwszy miesiąc w pytaniu 11 nie ma poprzednika, więc zmiana to NULL, nie zero.
Pytanie 12 obejmuje tylko serwisowane rowery; przychód minus serwis nie oznacza pełnego zysku.
W pytaniu 17 przerwa biegnie od końca jednego wypożyczenia do początku następnego tego samego klienta.
W pytaniu 18 wykorzystujemy stawkę z wypożyczenia i każde rozpoczęte pół godziny.

## 11

> Pokaż przychód w każdym miesiącu oraz o ile procent wzrósł lub spadł względem poprzedniego miesiąca.

| Miesiąc | Przychód (zł) | Zmiana (%) |
| --- | --- | --- |
| 2026-07 | 1 170,40 | — |
| 2026-08 | 2 477,40 | 111,67 |
| 2026-09 | 852,00 | -65,61 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH monthly AS (
    SELECT strftime('%Y-%m', started_at) AS month,
           SUM(charge_cents) AS revenue_cents
    FROM rentals
    GROUP BY strftime('%Y-%m', started_at)
), changes AS (
    SELECT month, revenue_cents,
           LAG(revenue_cents) OVER (ORDER BY month) AS previous_revenue_cents
    FROM monthly
)
SELECT month, revenue_cents / 100.0 AS revenue_pln,
       ROUND(100.0 * (revenue_cents - previous_revenue_cents)
             / NULLIF(previous_revenue_cents, 0), 2) AS change_percent
FROM changes
ORDER BY month;
```

</details>

## 12

> Które 5 rowerów spośród tych, które były serwisowane, przyniosło najwięcej przychodu po odjęciu kosztów ich serwisu? Pokaż kod roweru, przychód, koszt serwisu i kwotę, która została. Uwzględnij cały okres w bazie.

| Rower | Przychód (zł) | Serwis (zł) | Po serwisie (zł) |
| --- | --- | --- | --- |
| ROW-019 | 381,00 | 171,00 | 210,00 |
| ROW-014 | 236,40 | 30,00 | 206,40 |
| ROW-001 | 197,40 | 53,00 | 144,40 |
| ROW-011 | 169,20 | 54,00 | 115,20 |
| ROW-015 | 213,00 | 104,00 | 109,00 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH rental_totals AS (
    SELECT bike_id, SUM(charge_cents) AS revenue_cents
    FROM rentals
    GROUP BY bike_id
), service_totals AS (
    SELECT bike_id, SUM(cost_cents) AS cost_cents
    FROM service_records
    GROUP BY bike_id
)
SELECT b.bike_code,
       COALESCE(r.revenue_cents, 0) / 100.0 AS revenue_pln,
       COALESCE(s.cost_cents, 0) / 100.0 AS service_cost_pln,
       (COALESCE(r.revenue_cents, 0) - COALESCE(s.cost_cents, 0)) / 100.0 AS revenue_after_service_pln
FROM bikes AS b
LEFT JOIN rental_totals AS r ON r.bike_id = b.bike_id
JOIN service_totals AS s ON s.bike_id = b.bike_id
ORDER BY revenue_after_service_pln DESC, b.bike_code
LIMIT 5;
```

</details>

## 13

> Którzy klienci wypożyczyli rower w każdym z trzech miesięcy: lipcu, sierpniu i wrześniu 2026? Pokaż ich nazwiska, liczbę wypożyczeń i łączne wydatki w tych miesiącach. Posortuj od największych wydatków.

| Klient | Wypożyczenia | Wydatki (zł) |
| --- | --- | --- |
| Natalia Pawlak | 28 | 562,40 |
| Wiktoria Woźniak | 22 | 410,20 |
| Wiktoria Smith | 20 | 371,20 |
| Zofia Śliwa | 13 | 292,00 |
| Kamil Woźniak | 7 | 196,80 |
| Zofia Zając | 7 | 188,00 |
| Olivia Woźniak | 9 | 182,40 |
| Adam Lis | 7 | 132,00 |
| Karolina Duda | 8 | 123,60 |
| Paweł Zając | 6 | 109,60 |
| Karolina Nowak | 4 | 100,40 |
| Łukasz Pawlak | 7 | 90,60 |
| Zofia Król | 4 | 62,40 |
| Julia Pawlak | 5 | 58,00 |
| Martyna Król | 3 | 34,00 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
SELECT c.full_name, COUNT(*) AS rental_count,
       SUM(r.charge_cents) / 100.0 AS spending_pln
FROM customers AS c
JOIN rentals AS r ON r.customer_id = c.customer_id
WHERE r.started_at >= '2026-07-01' AND r.started_at < '2026-10-01'
GROUP BY c.customer_id, c.full_name
HAVING COUNT(DISTINCT strftime('%Y-%m', r.started_at)) = 3
ORDER BY spending_pln DESC, c.customer_id;
```

</details>

## 14

> Na których stacjach więcej rowerów oddano, niż wypożyczono? Pokaż liczbę rozpoczętych i zakończonych wypożyczeń oraz różnicę. Największy dodatni bilans pokaż na początku.

| Stacja | Rozpoczęte | Zakończone | Bilans |
| --- | --- | --- | --- |
| Ostrów Tumski | 27 | 30 | 3 |
| Nad Odrą | 30 | 33 | 3 |
| Kampus Politechniki | 25 | 28 | 3 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH movements AS (
    SELECT pickup_station_id AS station_id, COUNT(*) AS pickups, 0 AS returns
    FROM rentals
    GROUP BY pickup_station_id
    UNION ALL
    SELECT return_station_id AS station_id, 0 AS pickups, COUNT(*) AS returns
    FROM rentals
    GROUP BY return_station_id
)
SELECT s.name AS station, SUM(m.pickups) AS pickups, SUM(m.returns) AS returns,
       SUM(m.returns) - SUM(m.pickups) AS balance
FROM stations AS s
JOIN movements AS m ON m.station_id = s.station_id
GROUP BY s.station_id, s.name
HAVING SUM(m.returns) > SUM(m.pickups)
ORDER BY balance DESC, s.station_id;
```

</details>

## 15

> Kto wydał najwięcej na wypożyczenia osobno w lipcu, sierpniu i wrześniu 2026? Podaj miesiąc, klienta i kwotę. Jeśli ktoś ma tyle samo, pokaż wszystkie takie osoby.

| Miesiąc | Klient | Wydatki (zł) |
| --- | --- | --- |
| 2026-07 | Wiktoria Smith | 167,60 |
| 2026-08 | Natalia Pawlak | 275,80 |
| 2026-09 | Natalia Pawlak | 135,60 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH monthly_spending AS (
    SELECT strftime('%Y-%m', r.started_at) AS month,
           c.customer_id, c.full_name, SUM(r.charge_cents) AS spending_cents
    FROM rentals AS r
    JOIN customers AS c ON c.customer_id = r.customer_id
    WHERE r.started_at >= '2026-07-01' AND r.started_at < '2026-10-01'
    GROUP BY month, c.customer_id, c.full_name
), ranked AS (
    SELECT *, DENSE_RANK() OVER (PARTITION BY month ORDER BY spending_cents DESC) AS position
    FROM monthly_spending
)
SELECT month, full_name, spending_cents / 100.0 AS spending_pln
FROM ranked
WHERE position = 1
ORDER BY month, customer_id;
```

</details>

## 16

> Porównaj wypożyczenia w soboty i niedziele z tymi od poniedziałku do piątku. Dla obu grup podaj liczbę wypożyczeń, średnią opłatę w złotych i średni czas w minutach.

| Dni | Wypożyczenia | Średnia opłata (zł) | Średni czas (min) |
| --- | --- | --- | --- |
| Sobota–niedziela | 100 | 24,73 | 105,45 |
| Poniedziałek–piątek | 140 | 14,48 | 58,00 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
SELECT CASE WHEN strftime('%w', started_at) IN ('0', '6')
            THEN 'weekend' ELSE 'weekday' END AS day_group,
       COUNT(*) AS rental_count,
       ROUND(AVG(charge_cents) / 100.0, 2) AS average_charge_pln,
       ROUND(AVG(duration_minutes), 2) AS average_duration_minutes
FROM rentals
GROUP BY day_group
ORDER BY CASE day_group WHEN 'weekend' THEN 0 ELSE 1 END;
```

</details>

## 17

> Których 5 klientów wracało najszybciej po kolejne wypożyczenie? Weź osoby z co najmniej 3 wypożyczeniami. Policz średnią przerwę od oddania roweru do ich następnego wypożyczenia i podaj ją w dniach, razem z nazwiskiem i liczbą wypożyczeń.

| Klient | Wypożyczenia | Średnia przerwa (dni) |
| --- | --- | --- |
| Natalia Pawlak | 28 | 3,10 |
| Wiktoria Woźniak | 22 | 3,78 |
| Piotr Lis | 3 | 3,79 |
| Wiktoria Smith | 20 | 4,06 |
| Jakub Krawczyk | 11 | 5,43 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH ordered_rentals AS (
    SELECT customer_id, started_at,
           LAG(ended_at) OVER (PARTITION BY customer_id ORDER BY started_at, rental_id) AS previous_end
    FROM rentals
), customer_gaps AS (
    SELECT customer_id, COUNT(*) AS rental_count,
           AVG(julianday(started_at) - julianday(previous_end)) AS average_gap_days
    FROM ordered_rentals
    GROUP BY customer_id
    HAVING COUNT(*) >= 3
)
SELECT c.full_name, g.rental_count, ROUND(g.average_gap_days, 2) AS average_gap_days
FROM customer_gaps AS g
JOIN customers AS c ON c.customer_id = g.customer_id
ORDER BY g.average_gap_days, c.customer_id
LIMIT 5;
```

</details>

## 18

> Ile łącznie obniżyliśmy ceny przez rabaty 10%, 20% i 100%? Dla każdego rabatu pokaż liczbę wypożyczeń i kwotę rabatów w złotych. Opłata bez rabatu jest naliczana za każde rozpoczęte pół godziny, według stawki zapisanej przy danym wypożyczeniu.

| Rabat (%) | Wypożyczenia | Suma rabatów (zł) |
| --- | --- | --- |
| 10 | 38 | 82,20 |
| 20 | 36 | 142,00 |
| 100 | 3 | 50,00 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
SELECT discount_percent, COUNT(*) AS rental_count,
       SUM(((duration_minutes + 29) / 30) * (hourly_rate_cents / 2)
           - charge_cents) / 100.0 AS discount_value_pln
FROM rentals
WHERE discount_percent IN (10, 20, 100)
GROUP BY discount_percent
ORDER BY discount_percent;
```

</details>

## 19

> Które rowery były wypożyczane, ale nie mają ani jednego zapisanego serwisu? Pokaż ich kody, liczbę wypożyczeń i przychód, od najczęściej wypożyczanych.

| Rower | Wypożyczenia | Przychód (zł) |
| --- | --- | --- |
| ROW-004 | 17 | 279,60 |
| ROW-021 | 15 | 296,00 |
| ROW-010 | 14 | 192,00 |
| ROW-018 | 13 | 328,00 |
| ROW-020 | 10 | 249,00 |
| ROW-008 | 9 | 101,40 |
| ROW-012 | 9 | 138,00 |
| ROW-023 | 8 | 361,40 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
SELECT b.bike_code, COUNT(*) AS rental_count,
       SUM(r.charge_cents) / 100.0 AS revenue_pln
FROM bikes AS b
JOIN rentals AS r ON r.bike_id = b.bike_id
WHERE NOT EXISTS (
    SELECT 1 FROM service_records AS s WHERE s.bike_id = b.bike_id
)
GROUP BY b.bike_id, b.bike_code
ORDER BY rental_count DESC, b.bike_code;
```

</details>

## 20

> Która trasa między dwiema różnymi stacjami była najpopularniejsza? Traktuj przejazd z A do B osobno od B do A. Podaj stację początkową i końcową, liczbę wypożyczeń, średni czas oraz średni dystans. Przy remisie pokaż wszystkie najpopularniejsze trasy.

| Początek | Koniec | Wypożyczenia | Średni czas (min) | Średni dystans (km) |
| --- | --- | --- | --- | --- |
| Dworzec Główny | Nad Odrą | 6 | 60,83 | 9,09 |

<details>
<summary>SQL referencyjny — do sprawdzenia w osobnym narzędziu SQLite</summary>

```sql
WITH routes AS (
    SELECT pickup_station_id, return_station_id, COUNT(*) AS rental_count,
           AVG(duration_minutes) AS average_duration_minutes,
           AVG(distance_km) AS average_distance_km
    FROM rentals
    WHERE pickup_station_id != return_station_id
    GROUP BY pickup_station_id, return_station_id
)
SELECT pickup.name AS pickup_station, returned.name AS return_station,
       route.rental_count,
       ROUND(route.average_duration_minutes, 2) AS average_duration_minutes,
       ROUND(route.average_distance_km, 2) AS average_distance_km
FROM routes AS route
JOIN stations AS pickup ON pickup.station_id = route.pickup_station_id
JOIN stations AS returned ON returned.station_id = route.return_station_id
WHERE route.rental_count = (SELECT MAX(rental_count) FROM routes)
ORDER BY route.pickup_station_id, route.return_station_id;
```

</details>
