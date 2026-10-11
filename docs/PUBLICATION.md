# Publikacja i kontrola repozytorium

## Przygotowane

- README z rzeczywistymi zrzutami, instrukcją instalacji, diagramem i opisem prywatności.
- Generatory powtarzalnych danych syntetycznych i odpowiedzi kontrolne.
- Testy Python oraz JavaScript, które nie wymagają klucza API.
- Workflow GitHub Actions z minimalnymi uprawnieniami i akcjami przypiętymi do SHA.
- `.gitignore` pomijający lokalne konfiguracje, bazy, historię, eksporty i środowisko.
- Instrukcje współtworzenia, zakres bezpieczeństwa i lista dalszych prac.
- Udana instalacja z PyPI w nowym środowisku Python 3.12, bez lokalnego `.env`.
- `pip check`: brak konfliktów; 173 testy Python i 4 testy JavaScript: OK.
- Start czystej aplikacji i wykonanie przykładu bez API sprawdzone w przeglądarce.

Nie wykonano pełnego skanu podatności zależności. Lokalna weryfikacja była na macOS.
[Pierwszy przebieg GitHub Actions](https://github.com/KacperOkrasa/voice-text-to-sql-assistant/actions/runs/38155092758)
zakończył się powodzeniem na Linuxie 11 października 2026.

## Status publikacji

Repozytorium: [KacperOkrasa/voice-text-to-sql-assistant](https://github.com/KacperOkrasa/voice-text-to-sql-assistant).
Widoczność: publiczne. Gałąź główna: `main`.

Publikowany jest wyłącznie katalog projektu, bez nadrzędnego workspace,
klucza API, baz SQLite, historii rozmów i lokalnych eksportów.
Licencja pozostaje niewybrana zgodnie z decyzją autora; pliku LICENSE nie dodano.
Pierwszy push i wynik GitHub Actions zostały potwierdzone.

Pozostaje opcjonalnie przypiąć repozytorium do profilu oraz zdecydować o licencji.
Przy kolejnych zmianach powtarzaj kontrolę plików przed commitem opisaną niżej.

## Opis repozytorium

Voice and text analytics assistant for SQLite: natural-language questions,
local read-only SQL execution, schema-only database context, and a Streamlit chat UI.

Proponowane tematy:
`python`, `sqlite`, `text-to-sql`, `streamlit`, `pandas`, `openai`,
`data-engineering`, `speech-recognition`, `portfolio-project`.

## Kontrola przed pierwszym commitem

Przejrzyj listę dodawanych plików oraz różnice. `.gitignore` nie usuwa plików,
które wcześniej trafiły do historii Gita. Nie dodawaj:

- `.env`, kluczy, `.streamlit/secrets.toml`;
- plików `.db`, `.sqlite`, `.sqlite3`, katalogu `data/uploads/` i historii rozmów;
- lokalnych eksportów, logów i prywatnych zrzutów;
- środowiska `.venv` i plików tymczasowych.

Do repozytorium trafiają generatory danych i trzy zrzuty w `docs/screenshots/`.
Starszy lokalny obraz `assets/api-setup.jpg` nie jest używany przez dokumentację
ani aplikację i został objęty `.gitignore`.

## Automatyczne testy

Workflow `.github/workflows/tests.yml` wykonuje instalację, `pip check`, generator
bazy demo i oba zestawy testów. Nie wymaga GitHub Secrets ani konfiguracji `.env`.
Odwołania: [GitHub — testowanie Pythona](https://docs.github.com/en/actions/tutorials/build-and-test-code/python),
[setup-node](https://github.com/actions/setup-node).
