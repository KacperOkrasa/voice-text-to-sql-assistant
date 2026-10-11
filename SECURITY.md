# Zakres bezpieczeństwa

Projekt jest lokalną aplikacją dla jednej osoby. Nie jest gotowym serwisem dla
wielu użytkowników ani środowiskiem izolującym dowolne niezaufane zapytania.

## Granice zaufania

- Model otrzymuje schemat, pytanie i ograniczony kontekst. Rekordy i wyniki nie
  są dołączane do żądań generowania SQL. Pytania i metadane nie są anonimizowane.
- SQL przechodzi walidację, autoryzator SQLite i połączenie tylko do odczytu.
- Limity czasu, komórek i wyniku zmniejszają ryzyko nadmiernego wykorzystania
  zasobów, lecz nie zastępują izolacji procesu i limitu pamięci systemu.
- Bazy i historia są lokalnymi, nieszyfrowanymi plikami wspólnymi dla instalacji.
- Domyślny adres serwera to `127.0.0.1`. Publiczne udostępnienie wymaga osobnego
  przygotowania uwierzytelniania, izolacji danych i kontroli kosztów API.
- Dyktowanie może wysyłać głos do dostawcy przeglądarki; opcja Whisper wysyła
  nagranie do OpenAI. Szczegóły przepływu opisuje [README](README.md).

## Zgłaszanie problemów

Przygotuj minimalny przykład na syntetycznej bazie, wersję Pythona i opis zachowania.
Nie publikuj kluczy API, `.env`, prywatnych baz, historii ani surowych odpowiedzi
błędów dostawcy.

Znane ograniczenia i plan prac znajdują się w [ROADMAP](docs/ROADMAP.md).
