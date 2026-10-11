"""Create a small synthetic bike-rental database: python -m data.seed_bike_rental."""

import argparse
import random
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parent / "rowerownia.db"
FIRST_DAY = date(2026, 7, 1)
LAST_DAY = date(2026, 9, 30)

SCHEMA = """
CREATE TABLE stations (
    station_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    district TEXT NOT NULL
);

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE,
    city TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE bikes (
    bike_id INTEGER PRIMARY KEY,
    bike_code TEXT NOT NULL UNIQUE,
    bike_type TEXT NOT NULL CHECK (bike_type IN ('city', 'electric', 'cargo')),
    hourly_rate_cents INTEGER NOT NULL CHECK (hourly_rate_cents > 0),
    purchased_at TEXT NOT NULL,
    current_station_id INTEGER NOT NULL REFERENCES stations(station_id)
);

CREATE TABLE rentals (
    rental_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    bike_id INTEGER NOT NULL REFERENCES bikes(bike_id),
    pickup_station_id INTEGER NOT NULL REFERENCES stations(station_id),
    return_station_id INTEGER NOT NULL REFERENCES stations(station_id),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL CHECK (ended_at > started_at),
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    distance_km REAL NOT NULL CHECK (distance_km > 0),
    hourly_rate_cents INTEGER NOT NULL CHECK (hourly_rate_cents > 0),
    discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 0 AND 100),
    charge_cents INTEGER NOT NULL CHECK (charge_cents >= 0),
    payment_method TEXT CHECK (payment_method IN ('card', 'cash', 'blik'))
);

CREATE TABLE service_records (
    service_id INTEGER PRIMARY KEY,
    bike_id INTEGER NOT NULL REFERENCES bikes(bike_id),
    service_date TEXT NOT NULL,
    service_type TEXT NOT NULL,
    cost_cents INTEGER NOT NULL CHECK (cost_cents > 0)
);

CREATE INDEX idx_rentals_customer ON rentals(customer_id);
CREATE INDEX idx_rentals_bike ON rentals(bike_id);
CREATE INDEX idx_rentals_started_at ON rentals(started_at);
CREATE INDEX idx_rentals_pickup_station ON rentals(pickup_station_id);
CREATE INDEX idx_service_records_bike ON service_records(bike_id);
"""

STATIONS = [
    (1, "Rynek", "Stare Miasto"),
    (2, "Dworzec Główny", "Krzyki"),
    (3, "Ostrów Tumski", "Śródmieście"),
    (4, "Park Szczytnicki", "Śródmieście"),
    (5, "Nad Odrą", "Stare Miasto"),
    (6, "Kampus Politechniki", "Śródmieście"),
]
FIRST_NAMES = [
    "Anna", "Piotr", "Zofia", "Michał", "Julia", "Jakub", "Natalia", "Adam",
    "Karolina", "Paweł", "Wiktoria", "Kamil", "Martyna", "Łukasz", "Olivia",
]
LAST_NAMES = [
    "Nowak", "Krawczyk", "Król", "Wójcik", "Zając", "Duda",
    "Śliwa", "Lis", "Woźniak", "Pawlak", "Mazur", "Smith",
]
SERVICE_TYPES = [
    ("przegląd", 5000, 9000),
    ("wymiana opony", 8000, 16000),
    ("regulacja hamulców", 3000, 7000),
    ("wymiana łańcucha", 9000, 18000),
]


def create_bike_rental_database(db_path: str | Path = DEFAULT_PATH, seed: int = 2026) -> Path:
    """Create 328 synthetic records without overwriting an existing database.

    Amounts are integer grosze (100 grosze = 1 PLN). Rentals are completed,
    billed in started half-hours, and use the rate saved on each rental.
    Five customers and three recently purchased bikes have no rentals.
    """
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=False)
    rng = random.Random(seed)

    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA)
            with connection:
                connection.executemany("INSERT INTO stations VALUES (?, ?, ?)", STATIONS)
                insert_customers(connection, rng)
                insert_bikes(connection, rng)
                busy_days = insert_rentals(connection, rng)
                insert_service_records(connection, rng, busy_days)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return path


def insert_customers(connection: sqlite3.Connection, rng: random.Random) -> None:
    """Insert invented customers with reserved example.com email addresses."""
    names = rng.sample([(first, last) for first in FIRST_NAMES for last in LAST_NAMES], 40)
    for customer_id, (first, last) in enumerate(names, start=1):
        city = rng.choices(["Wrocław", "Poznań", "Kraków", "Warszawa"], [7, 1, 1, 1])[0]
        registered_at = date(2026, 4, 1) + timedelta(days=rng.randint(0, 85))
        email = None if customer_id % 8 == 0 else f"customer{customer_id:02d}@example.com"
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            (customer_id, f"{first} {last}", email, city, registered_at.isoformat()),
        )


def insert_bikes(connection: sqlite3.Connection, rng: random.Random) -> None:
    """Insert city, electric and cargo bikes with different hourly rates."""
    for bike_id in range(1, 25):
        bike_type = "city" if bike_id <= 16 else "electric" if bike_id <= 22 else "cargo"
        rate = {"city": 1200, "electric": 2000, "cargo": 2600}[bike_type]
        # These three bikes joined after the rental reporting period.
        purchased_at = "2026-10-01" if bike_id in (16, 22, 24) else "2026-03-15"
        station_id = rng.choices(range(1, 7), [4, 2, 1, 3, 2, 1])[0]
        connection.execute(
            "INSERT INTO bikes VALUES (?, ?, ?, ?, ?, ?)",
            (bike_id, f"ROW-{bike_id:03d}", bike_type, rate, purchased_at, station_id),
        )


def insert_rentals(connection: sqlite3.Connection, rng: random.Random) -> set[tuple[int, date]]:
    """Generate non-overlapping rentals, with more demand in August and weekends."""
    bikes = connection.execute(
        "SELECT bike_id, bike_type, hourly_rate_cents, current_station_id "
        "FROM bikes WHERE purchased_at <= ? ORDER BY bike_id", (FIRST_DAY.isoformat(),),
    ).fetchall()
    dates = [FIRST_DAY + timedelta(days=offset) for offset in range((LAST_DAY - FIRST_DAY).days + 1)]
    weights = [{7: 1.0, 8: 1.7, 9: 0.8}[day.month] * (1.8 if day.weekday() >= 5 else 1) for day in dates]
    scheduled = []
    busy_bikes: set[tuple[int, date]] = set()
    busy_customers: set[tuple[int, date]] = set()

    for index in range(240):
        # Guarantee every older bike and every one of the 35 active customers is used.
        bike = bikes[index] if index < len(bikes) else rng.choice(bikes)
        customer_id = index + 1 if index < 35 else rng.choices(range(1, 36), [5] * 4 + [1] * 31)[0]
        available_dates = [day for day in dates if (bike[0], day) not in busy_bikes
                           and (customer_id, day) not in busy_customers]
        available_weights = [weights[(day - FIRST_DAY).days] for day in available_dates]
        rental_date = rng.choices(available_dates, available_weights)[0]
        started_at = datetime.combine(rental_date, time(rng.randint(8, 18), rng.choice([0, 15, 30, 45])))
        scheduled.append((started_at, bike, customer_id))
        busy_bikes.add((bike[0], rental_date))
        busy_customers.add((customer_id, rental_date))

    locations = {bike_id: station_id for bike_id, _, _, station_id in bikes}
    for rental_id, (started_at, bike, customer_id) in enumerate(sorted(scheduled), start=1):
        bike_id, bike_type, rate, _ = bike
        duration = rng.choice([45, 60, 90, 120, 180, 240] if started_at.weekday() >= 5
                              else [20, 30, 45, 60, 90, 120])
        ended_at = started_at + timedelta(minutes=duration)
        speed = rng.uniform(7, 15) if bike_type == "electric" else rng.uniform(4, 10)
        distance = round(duration / 60 * speed, 2)
        pickup = locations[bike_id]
        returned = pickup if rng.random() < 0.65 else rng.randint(1, 6)
        locations[bike_id] = returned
        discount = 100 if rental_id % 61 == 0 else rng.choice([0, 0, 0, 0, 10, 20])
        charge = ((duration + 29) // 30) * (rate // 2) * (100 - discount) // 100
        payment = rng.choices(["card", "cash", "blik"], [5, 1, 4])[0] if charge else None
        connection.execute(
            "INSERT INTO rentals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rental_id, customer_id, bike_id, pickup, returned,
             started_at.isoformat(sep=" "), ended_at.isoformat(sep=" "),
             duration, distance, rate, discount, charge, payment),
        )

    connection.executemany("UPDATE bikes SET current_station_id = ? WHERE bike_id = ?",
                           [(station_id, bike_id) for bike_id, station_id in locations.items()])
    return busy_bikes


def insert_service_records(
    connection: sqlite3.Connection, rng: random.Random, busy_days: set[tuple[int, date]],
) -> None:
    """Schedule service on days without rentals for the serviced bike."""
    bike_ids = [row[0] for row in connection.execute(
        "SELECT bike_id FROM bikes WHERE purchased_at <= ? ORDER BY bike_id", (FIRST_DAY.isoformat(),),
    )]
    dates = [FIRST_DAY + timedelta(days=offset) for offset in range((LAST_DAY - FIRST_DAY).days + 1)]
    used_service_days: set[tuple[int, date]] = set()
    for service_id in range(1, 19):
        bike_id = rng.choice(bike_ids)
        available_dates = [day for day in dates if (bike_id, day) not in busy_days
                           and (bike_id, day) not in used_service_days]
        serviced_on = rng.choice(available_dates)
        service_type, low, high = SERVICE_TYPES[(service_id - 1) % len(SERVICE_TYPES)]
        cost_cents = rng.randrange(low, high + 1, 100)
        connection.execute("INSERT INTO service_records VALUES (?, ?, ?, ?, ?)",
                           (service_id, bike_id, serviced_on.isoformat(), service_type, cost_cents))
        used_service_days.add((bike_id, serviced_on))


def main() -> None:
    """Build a new SQLite file; --seed changes the random records."""
    parser = argparse.ArgumentParser(description="Utwórz małą bazę wypożyczalni rowerów.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    try:
        path = create_bike_rental_database(args.db_path, args.seed)
    except FileExistsError:
        parser.exit(status=1, message="Baza już istnieje. Wybierz nowy plik przez --db-path.\n")
    print(f"Utworzono bazę: {path}")


if __name__ == "__main__":
    main()
