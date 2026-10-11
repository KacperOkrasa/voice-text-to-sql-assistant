# Rowerownia — mała baza do testowania

`rowerownia.db` to gotowy plik SQLite z syntetycznymi danymi wypożyczalni
rowerów we Wrocławiu. Nazwiska i dane kontaktowe są fikcyjne; adresy e-mail
korzystają z domeny `example.com`. To zestaw do nauki, nie dane rzeczywistej firmy.

## Zawartość

| Tabela | Rekordy | Co zawiera |
| --- | ---: | --- |
| `stations` | 6 | Stacje i dzielnice |
| `customers` | 40 | Klienci, miasta i daty rejestracji |
| `bikes` | 24 | Rowery miejskie, elektryczne i cargo; stawki i aktualne stacje |
| `rentals` | 240 | Wypożyczenia, czas, dystans, rabaty i pobrane opłaty |
| `service_records` | 18 | Przeglądy, naprawy i ich koszty |

Łącznie **328 rekordów**, plik **84 KB**, z wypożyczeniami od lipca do września 2026 roku.
Klucze obce łączą wypożyczenia z klientami, rowerami oraz stacjami,
a serwis z rowerami.

## Jak używać w aplikacji

Utwórz bazę poleceniem `python -m data.seed_bike_rental`, a następnie wgraj
`data/rowerownia.db` przez **Bazy → Dodaj bazę** i wybierz ją dla rozmowy.
Plik bazy jest generowany lokalnie; nie jest dołączany do repozytorium.
Tabele i kolumny zobaczysz pod przyciskiem **O danych**.

Przykładowe pytania do AI:

1. Pokaż przychód i liczbę wypożyczeń w każdym miesiącu.
2. Który typ roweru wypożyczano najczęściej?
3. Pokaż 5 klientów, którzy wydali najwięcej na wypożyczenia.
4. Która stacja miała najwięcej rozpoczętych wypożyczeń?
5. Pokaż klientów, którzy nigdy nie wypożyczyli roweru.
6. Porównaj średni czas wypożyczenia dla różnych typów rowerów.
7. Jaki był koszt serwisu według rodzaju naprawy?
8. Pokaż miesięczny przychód pomniejszony o koszty serwisu.
9. Show monthly rental revenue in PLN for July through September 2026.
10. Pokaż czas i dystans pierwszych 50 wypożyczeń.

Plik `rowerownia_examples.sql` zawiera osiem zapytań referencyjnych.
Zapytania referencyjne można uruchomić w osobnym narzędziu SQLite, aby porównać
wynik AI ze znanym wynikiem bez kolejnego wywołania API. Czat aplikacji przyjmuje
pytania w zwykłym języku i nie wykonuje ręcznie wklejonego SQL.

Dla pytania 8 plik `rowerownia_monthly_balance.sql` zawiera jedno kompletne
zapytanie do uruchomienia w osobnym narzędziu SQLite. Najpierw
sumuje przychód i serwis osobno dla każdego miesiąca, a następnie łączy
miesięczne sumy przez `UNION ALL`. Bezpośrednie połączenie wypożyczeń z naprawami
tego samego roweru mogłoby policzyć każdą naprawę wiele razy. `SUM(DISTINCT kwota)`
też byłoby błędne: różne wypożyczenia lub naprawy mogą mieć identyczną cenę.

## Wyniki kontrolne domyślnej bazy

| Miesiąc | Wypożyczenia | Przychód |
| --- | ---: | ---: |
| Lipiec 2026 | 63 | 1 170,40 zł |
| Sierpień 2026 | 127 | 2 477,40 zł |
| Wrzesień 2026 | 50 | 852,00 zł |
| Razem | 240 | 4 499,80 zł |

Całkowity koszt serwisu: **1 525,00 zł**.
Przychód pomniejszony o serwis: **2 974,80 zł**.
Wyniki dotyczą pliku wygenerowanego z domyślnym seedem `2026`.

`tests/test_bike_analytics.py` sprawdza ten sposób liczenia na małym zestawie
kontrolnym z wieloma wypożyczeniami i naprawami jednego roweru, równymi kwotami
różnych transakcji oraz miesiącami zawierającymi tylko przychód lub tylko koszty.
Test integracji podaje zastępczą odpowiedź modelu; nie wywołuje API i nie
potwierdza, jakie zapytanie wygeneruje rzeczywisty model.

## Jak rozumieć dane

- Wszystkie wypożyczenia są zakończone, a `charge_cents` to faktycznie pobrana
  opłata po rabacie. Przychód licz jako `SUM(charge_cents) / 100.0`.
- Wszystkie kolumny `*_cents` zawierają kwoty w groszach; walutą jest PLN.
- `rentals.hourly_rate_cents` przechowuje stawkę obowiązującą przy wypożyczeniu.
  Rozliczenie obejmuje każde rozpoczęte pół godziny, następnie rabat.
- Typy rowerów to `city`, `electric`, `cargo`. Metody płatności to `card`, `cash`, `blik`.
- Trzy wypożyczenia mają rabat 100%, opłatę 0 i brak metody płatności (`NULL`).
- Pięciu klientów nie ma wypożyczeń; pięciu nie podało adresu e-mail (`NULL`).
- Trzy rowery kupiono 1 października, po okresie raportowania, więc nie mają wypożyczeń.
- Serwis nie przypada na dzień wypożyczenia tego samego roweru. Wypożyczenia
  jednego klienta ani roweru nie nakładają się w czasie.
- `current_station_id` oznacza lokalizację po ostatnim zakończonym wypożyczeniu.
  Ruch historyczny analizuj po `pickup_station_id` i `return_station_id`.
- Przychód minus serwis nie jest pełnym zyskiem: zestaw nie zawiera kosztów
  wynajmu lokalu, wynagrodzeń ani amortyzacji.

## Ponowne generowanie

W katalogu projektu:

```bash
python -m data.seed_bike_rental
```

Generator korzysta wyłącznie z biblioteki standardowej Pythona i nie odczytuje
`.env` ani nie wywołuje API. Domyślny seed `2026` odtwarza ten sam zestaw.
Istniejący plik nie zostanie nadpisany. Inny losowy zestaw utworzysz tak:

```bash
python -m data.seed_bike_rental --db-path data/rowerownia_2.db --seed 123
```

Losowe dane zachowują relacje, ograniczenia oraz spójność dat i opłat.
