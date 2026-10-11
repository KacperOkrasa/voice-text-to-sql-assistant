# Praca nad projektem

Projekt ma zachować prostą strukturę odpowiednią dla portfolio Python / Data
Engineering: małe funkcje, czytelne nazwy, podstawowe type hinty i docstringi.

## Środowisko

Użyj Python 3.12 i zależności z `requirements.txt`; pełna instrukcja znajduje się
w [README](README.md). Do testów komponentu dyktowania potrzebny jest również Node.js
(w CI używana jest wersja 22).

W świeżej kopii wygeneruj dane demo:

```bash
python -m data.seed
```

Generator nie nadpisuje istniejącej bazy. Jeśli demo już istnieje, pomiń ten krok.

```bash
python -m pip check
python -m unittest discover -s tests -v
node --test tests/test_dictation.mjs
```

Testy API używają atrap. Nie dodawaj do domyślnego zestawu testów płatnych wywołań.
Testy danych twórz na tymczasowych bazach z syntetycznymi rekordami.

## Zakres zmian

- Dodaj test odtwarzający naprawiany błąd, szczególnie dla precyzji, prywatności i SQL.
- Utrzymuj walidację zapytań oraz połączenie analityczne tylko do odczytu.
- Do żądań modelu nie dołączaj rekordów, DataFrame ani surowych błędów bazy.
- Zmiana kontekstu rozmowy wymaga sprawdzenia całego wysyłanego payloadu w testach.
- Dokumentuj zmianę formatu historii i zachowuj odczyt wcześniejszych zapisów.
- Przy zmianach interfejsu aktualizuj zrzuty wyłącznie na danych demonstracyjnych.

Przed commitem sprawdź listę plików, szczególnie `.env`, lokalne bazy, historię,
eksporty i zrzuty ekranu. [Lista publikacji](docs/PUBLICATION.md) opisuje ostatnie kroki.
