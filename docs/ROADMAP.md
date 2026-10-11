# Plan poprawek i publikacji

Stan: 11 października 2026. Lista powstała na podstawie przeglądu kodu,
wcześniejszych reprodukcji błędów i ponownego sprawdzenia wskazanych miejsc.
Pozycje oznaczone [x] zostały wdrożone i objęte testami regresji.

## Najpierw: wiarygodność wyników

- [x] Zachować dokładność dużych liczb całkowitych, także w kolumnach z NULL,
  przy przejściu SQLite → Pandas → zapis historii → odczyt. Usunięto pośredni float
  dla liczb całkowitych; test obejmuje pełny cykl zapisu i wyświetlenie.
- [x] Ujednolicić eksport CSV i tabelę. Wartości widoku i eksportu do arkusza
  mają zgodne jednostki. Wbudowany eksport zastąpiono jawnymi wariantami CSV.
- [x] Zdefiniować eksport do arkusza, w którym tekst zaczynający się np. od `=`
  nie zostanie potraktowany jako formuła. Jest oddzielony od eksportu surowych danych.

## Następnie: odporność i prywatność

- [x] Ograniczyć liczbę kolumn, wielkość komórki i całego wyniku w bajtach;
  uzupełnić limity SQLite. Limit wierszy i progress handler nie ograniczają
  wszystkich alokacji. Obsłużyć błąd prezentacji pojedynczego wyniku.
- [x] Sprawdzać integralność importowanej bazy w ograniczonym czasie oraz możliwość
  odczytu jej metadanych i widoków. Sam nagłówek i lista tabel nie wystarczają.
- [x] Izolować wadliwy wpis historii, aby nie blokował pozostałych rozmów.
  Dodać walidację i wersję formatu zapisu, bez automatycznego kasowania uszkodzonego wpisu.
- [ ] Uwzględnić kolumny wygenerowane w schemacie i przeglądarce danych.
  Dodać wybór obiektów udostępnianych AI. Nazwy kolumn, zwłaszcza w widokach,
  mogą zawierać informacje poufne mimo pominięcia rekordów.

## Utrzymanie i wygoda

- [ ] Obsłużyć pustą korektę dyktowania, która ma wyczyścić poprzedni tekst.
  Rozszerzyć testy o asynchroniczne zdarzenia start/stop/error/end.
- [ ] Ładować wyniki historii na żądanie i nie przygotowywać wszystkich eksportów
  przy każdym odświeżeniu. Zmierzyć czas dla dłuższej historii.
- [ ] Wydzielić kilka prostych modułów interfejsu z `app.py` bez rozbudowanej hierarchii klas.
- [ ] Ujednolicić strefę daty odniesienia dla modelu i historii.
- [ ] Zastąpić założenia e-commerce / PLN neutralnymi etykietami lub ustawieniami bazy.

## Przygotowanie GitHuba

- [x] Zaktualizować README: opis, instalacja, architektura, prywatność i ograniczenia.
- [x] Usunąć z dokumentacji danych instrukcje korzystania z wycofanego edytora SQL.
- [x] Sprawdzić instalację z czystego katalogu bez `.env`, baz i lokalnej historii.
- [x] Dodać GitHub Actions: testy Python i JavaScript, bez prawdziwych kluczy API.
- [x] Sprawdzić instalację i zgodność zależności na Pythonie 3.12 (`pip check`).
  Pełny skan podatności nie był częścią tej kontroli.
- [x] Dodać trzy rzeczywiste zrzuty wyłącznie na danych syntetycznych.
- [ ] Wybrać licencję i dodać plik LICENSE — zgodnie z decyzją autora przed publikacją.
- [x] Sprawdzić bieżącą listę plików przeznaczonych do publikacji: bez `.env`, kluczy,
  importowanych baz, historii, eksportów i prywatnych zrzutów. `.gitignore` nie usuwa
  sekretów, które zostały już wcześniej zapisane w historii Git.
- [x] Utworzyć repozytorium i pierwszy commit po kontroli plików.

## Ocena jakości AI

- [ ] Przygotować powtarzalny zestaw pytań PL/EN z oczekiwanymi wynikami,
  uwzględniając JOIN, NULL, daty, doprecyzowania i kontynuacje.
- [ ] Porównywać otrzymane tabele z odpowiedziami referencyjnymi, nie tylko sam tekst SQL.
- [ ] Oddzielić płatną ocenę rzeczywistego modelu od bezpłatnych testów z atrapami.
  Zapisywać model, datę oceny, odsetek poprawnych wyników i przypadki błędne.

## Już wykonane

- [x] Usunąć ręczne wykonywanie SQL, edytor i import `.sql` z interfejsu.
- [x] Blokować rozpoznany SQL przed wywołaniem modelu, również po dyktowaniu
  i przy ponawianiu starych wiadomości; zachować podgląd wygenerowanego kodu.

Publikacja kodu jako projektu portfolio nie jest tym samym co publiczne wdrożenie
aplikacji. Hosting dla wielu osób wymaga dodatkowo uwierzytelniania, izolacji
plików i rozmów oraz kontroli wykorzystania wspólnego API.

### Weryfikacja poprawek 1–4

173 testy Python, w tym przypadki INTEGER + NULL, dokładny zapis REAL, starszy
format historii, uszkodzone rekordy i strony SQLite, błędne widoki, jednostki
CSV, tekst przypominający formuły oraz limity komórek i wyniku. Testy API używają
atrap. Nie wykonywano płatnych wywołań ani testów na prywatnych importach.

Nowe limity: 1000 wierszy, 100 kolumn, 64 KiB na komórkę, 4 MiB danych wyniku;
limit pojedynczej wartości/wiersza SQLite wynosi 1 MiB. Starsze zapisy nie są
przepisywane automatycznie, a wcześniej utracone cyfry wymagają ponownej analizy.

### Przygotowanie publikacji

Sprawdzono świeżą instalację z PyPI, start aplikacji bez klucza, przykład demo,
173 testy Python i 4 testy JavaScript. W README są trzy zrzuty interfejsu oraz
wyróżnione wyjaśnienie, że rekordy i wyniki nie trafiają do modelu generującego SQL.
Przygotowano CI, CONTRIBUTING i SECURITY. Sprawdzono listę 53 plików do publikacji,
reguły pomijania prywatnych plików, lokalne odnośniki i wzorce kluczy w publikowanych
plikach tekstowych. Nie odczytywano lokalnego `.env`. Kontrola wzorców nie zastępuje
pełnego skanera sekretów ani przeglądu plików przed późniejszym commitem.

Repozytorium publiczne: [voice-text-to-sql-assistant](https://github.com/KacperOkrasa/voice-text-to-sql-assistant).
Wynik pierwszego uruchomienia CI wymaga sprawdzenia po pushu.
Licencja pozostaje niewybrana. [Status publikacji](PUBLICATION.md).
