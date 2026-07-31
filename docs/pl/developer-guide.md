# Podręcznik dewelopera NectarinePanel

[English](../en/developer-guide.md) · [Українська](../uk/developer-guide.md) ·
[Русский](../ru/developer-guide.md) · **Polski**

To główna dokumentacja dla deweloperów i operatorów, którzy instalują,
modyfikują, testują, wydają lub diagnozują NectarinePanel. Obsługę interfejsu
WWW opisuje [podręcznik użytkownika](user-guide.md). Pozostałe pliki w
`docs/` są pogłębionym materiałem referencyjnym.

## 1. Zakres projektu

NectarinePanel jest modularnym monorepozytorium do wdrażania i utrzymywania
aplikacji na jednym VPS z Ubuntu, a nie harmonogramem wieloserwerowym. FastAPI
odpowiada za walidację, autoryzację i stan, Celery za długie zadania, a
uprzywilejowane zmiany serwera przechodzą przez lokalnego agenta z ograniczonym
zestawem operacji.

Stos: FastAPI, asynchroniczny SQLAlchemy, Alembic, PostgreSQL, Valkey, Celery,
Nuxt 4, Vue 3, TypeScript, Pinia, agent systemowy i opcjonalny bot Telegram
oparty na aiogram. Środowiskiem produkcyjnym jest Ubuntu 24.04 LTS. Rozwój wymaga Python 3.12+,
wersji Node.js LTS obsługiwanej w `frontend/package.json`, npm 10+ oraz Docker
Engine z Compose v2.

## 2. Struktura repozytorium

```text
backend/        API FastAPI, modele, schematy, usługi i migracje Alembic
worker/         zadania Celery i procesor zadań w tle
agent/          walidowane operacje systemowe i lokalna granica HTTP
frontend/       Nuxt, komponenty, funkcje composable, tłumaczenia i testy
telegram-bot/   opcjonalny bot właściciela
installer/      instalator, aktualizator, deinstalator, jednostki systemd i Nginx
docs/           główne podręczniki i referencje techniczne
tests/          testy repozytorium, instalatora i plików kontenerów
scripts/        skrypty utrzymania i weryfikacji
```

Zachowuj obecną pragmatyczną strukturę. Nie dodawaj warstw
`domain/application/infrastructure` bez konkretnej potrzeby. Trasy obsługują
HTTP, usługi zawierają współdzieloną logikę biznesową i infrastrukturalną,
schematy definiują kontrakty API, a modele — strukturę przechowywania danych.

## 3. Architektura i przepływ operacji

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker / scheduler
                            -> local agent -> root-owned helper -> host
Telegram -> authenticated internal FastAPI endpoints
```

Typowa operacja asynchroniczna:

1. FastAPI uwierzytelnia użytkownika, sprawdza RBAC, waliduje dane wejściowe i
   tworzy wpisy zadania oraz audytu.
2. Celery otrzymuje identyfikatory oraz parametry bez sekretów.
3. Procesor ładuje aktualny stan i zaszyfrowane dane uwierzytelniające z magazynu.
4. Uprzywilejowana operacja trafia do typowanego punktu końcowego agenta.
5. Agent i program pomocniczy ponownie walidują żądanie i mapują je na stałą
   listę argumentów lub ograniczoną operację plikową.
6. Procesor zapisuje postęp i wynik; interfejs używa WebSocket z rezerwowym
   odpytywaniem API.

Nigdy nie dodawaj uniwersalnego wykonawcy poleceń do agenta i nie montuj gniazda
Docker w publicznej części serwerowej. Więcej: [architektura](architecture.md),
[bezpieczeństwo](security.md), [projekty](projects.md).

## 4. Środowisko lokalne

Wszystkie polecenia Python muszą korzystać ze środowiska wirtualnego repozytorium.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm ci
cd ..
cp .env.example .env
```

Zmień sekrety deweloperskie w `.env`, zanim udostępnisz środowisko:

```bash
openssl rand -hex 32
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Pełne środowisko Docker

```bash
docker compose up --build
docker compose exec backend python -m app.cli create-admin --username admin
```

Panel: `http://localhost:3000`, OpenAPI:
`http://localhost:8000/api/v1/docs`. Porty deweloperskie są przypisane do
interfejsu lokalnego. Ten Compose nie jest wdrożeniem produkcyjnym.

Profile opcjonalne:

```bash
docker compose --profile telegram up --build
docker compose --profile adminer up --build
```

### Uruchamianie komponentów na hoście

Uruchom PostgreSQL/Valkey lub ustaw zgodne URL w `.env`, a następnie w osobnych
terminalach:

```bash
make migrate
make backend
make worker
make scheduler
make agent
make frontend
```

Część serwerowa uruchamiana bezpośrednio domyślnie używa SQLite, ale procesor,
zadania w tle, ograniczanie częstotliwości i harmonogram wymagają Valkey. W
testach środowiska oraz serwera używaj atrap lub jednorazowego środowiska; nie
kieruj środowiska deweloperskiego do magazynu produkcyjnego.

## 5. Konfiguracja

Źródłem prawdy jest `backend/app/core/config.py`, a typowe wartości opisuje
`.env.example`.

| Zmienna | Przeznaczenie |
| --- | --- |
| `ENVIRONMENT` | `development`, `test` albo wartość produkcyjna; produkcja odrzuca domyślne niebezpieczne sekrety. |
| `DATABASE_URL` | Ciąg połączenia asynchronicznego SQLAlchemy. |
| `REDIS_URL` | Valkey/Redis dla limitów zapytań, Celery i stanu bieżącego. |
| `JWT_SECRET` | Co najmniej 32 znaki poza środowiskiem lokalnym. |
| `FIELD_ENCRYPTION_KEY` | Klucz Fernet dla zapisanych sekretów; wymagany w produkcji. |
| `AGENT_URL`, `AGENT_TOKEN` | Prywatny punkt agenta i współdzielony sekret uwierzytelniania. |
| `STORAGE_ROOT` | Katalog główny projektów, kopii, plików i stanu środowisk. |
| `CONFIG_ROOT`, `NGINX_CONFIG_ROOT` | Konfiguracja panelu i wygenerowanych hostów. |
| `CORS_ORIGINS` | Dozwolone źródła przeglądarki rozdzielone przecinkami. |
| `PUBLIC_BASE_URL` | Publiczny URL panelu dla odnośników i wywołań zwrotnych. |
| `TELEGRAM_*` | Opcjonalna konfiguracja bota i uwierzytelniania wewnętrznego. |

Nie zmieniaj `FIELD_ENCRYPTION_KEY` bez migracji danych — istniejące zaszyfrowane
pola staną się nieczytelne. Nie zapisuj w logach obiektów ustawień, tokenów,
haseł, kluczy prywatnych ani odszyfrowanych DSN.

## 6. Rozwój części serwerowej

Część serwerowa jest asynchroniczna. Korzystaj z `AsyncSession`, schematów
Pydantic na granicy API i stabilnych błędów HTTP zamiast ujawniania wyjątków
wewnętrznych.

Dodając punkt końcowy:

1. utwórz lub zaktualizuj schemat w `backend/app/schemas/`;
2. przenieś logikę wielokrotnego użytku do `backend/app/services/`, jeśli trasa
   miesza HTTP z logiką biznesową lub infrastrukturalną;
3. przed odczytem wrażliwych danych wywołaj `require_global_role(...)` lub
   `require_project_permission(project_id, permission)`;
4. audytuj operacje zmieniające stan i związane z bezpieczeństwem bez jawnych sekretów;
5. dodaj testy sukcesu, walidacji, braku uwierzytelnienia, zabronionej roli,
   dostępu do cudzego projektu i istotnych błędów;
6. nie łam istniejącego kontraktu odpowiedzi bez jawnej niezgodnej zmiany.

Role `owner` i `admin` mają globalny dostęp do projektów. Dostęp ról
`maintainer` i `viewer` wynika z przypisania do projektu i mapy uprawnień w
`backend/app/services/permissions.py`. Ukrycie akcji w interfejsie nie zastępuje
autoryzacji na serwerze.

Wszystkie moduły, funkcje i klasy Python wymagają krótkich angielskich opisów.
Używaj bezpiecznych parametrów sterowników, zwalidowanych identyfikatorów,
stałych list argumentów i ograniczonego wejścia-wyjścia. Nie buduj poleceń
powłoki z danych użytkownika.

## 7. Migracje bazy danych

Każda zmiana przechowywanych danych wymaga migracji Alembic od bieżącej głowicy:

```bash
cd backend
../.venv/bin/alembic heads
../.venv/bin/alembic revision --autogenerate -m "describe change"
../.venv/bin/alembic upgrade head
cd ..
```

Sprawdź DDL, nazwy, indeksy, klucze obce, wartości domyślne, kolejność aktualizacji
i bezpieczeństwo wycofania. Przetestuj aktualizację z poprzedniego schematu. Nie
zmieniaj migracji, która mogła być zastosowana — dodaj migrację naprawczą.
Aktualizator tworzy awaryjną kopię, ale migracja nadal musi być transakcyjna i
zgodna z istniejącymi danymi, o ile pozwala na to silnik.

## 8. Procesor i zadania w tle

Celery służy do wdrożeń, kopii zapasowych, operacji bazodanowych i certyfikatów,
monitoringu oraz innych długich zadań. API powinno kolejkować zadanie zamiast
utrzymywać połączenie HTTP.

Zasady zadań w tle:

- parametry zawierają identyfikatory i dane bez sekretów, nie dane uwierzytelniające;
- procesor ponownie odczytuje bieżący stan bazy przy starcie;
- postęp i błędy mają limit i są bezpieczne dla interfejsu operatora;
- ponowienia są jawne i dotyczą tylko operacji idempotentnych lub wznawialnych;
- sprzątanie wykonuje się w `finally`;
- metadane są zapisywane po sukcesie operacji zewnętrznej;
- ponowne sprzątanie lub usuwanie powinno być idempotentne, gdy to możliwe.

W testach jednostkowych zastępuj granice agenta i klientów atrapami oraz
sprawdzaj sukces i częściową awarię. Nie wymagaj działającego Docker ani usług
publicznych.

## 9. Agent systemowy

Agent jest uprzywilejowaną granicą bezpieczeństwa. Proces HTTP działa jako
`vps-panel-agent` i przesyła jedną zwalidowaną operację przez standardowe wejście
do dokładnie określonego programu pomocniczego należącego do administratora i
dozwolonego w sudoers.

Nowa operacja wymaga:

1. ścisłego, typowanego modelu żądania i limitów;
2. uwierzytelniania tokenem agenta;
3. rozwiązania ścieżki wewnątrz dozwolonego katalogu po obsłudze dowiązań;
4. stałego programu i argumentów bez `shell=True`;
5. limitu czasu i wyjścia oraz przewidywalnych błędów;
6. ponownej walidacji w programie pomocniczym dla operacji administratora;
7. testów bezpieczeństwa dla wyjścia poza katalog, wstrzyknięć, linków,
   nieprawidłowych danych i braku autoryzacji;
8. minimalnych właścicieli i uprawnień systemowych.

Jeżeli działania nie da się bezpiecznie wyrazić jako stałej dozwolonej operacji,
nie należy ono do API agenta.

## 10. Interfejs i lokalizacja

Nuxt odpowiada za prezentację i stan klienta. Autoryzacja, sekrety i logika
systemowa pozostają w części serwerowej. Używaj istniejących komponentów,
zmiennych CSS, ikon Tabler, funkcji composable i wzorców responsywnych; nie
dodawaj zależności dla małej funkcji pomocniczej, którą łatwo zaimplementować i
przetestować lokalnie.

```text
frontend/pages/        strony tras
frontend/components/   komponenty interfejsu wielokrotnego użytku
frontend/composables/  API, lokalizacja, uprawnienia i wspólna logika
frontend/stores/       stan Pinia
frontend/locales/      katalogi EN, UK, RU i PL
frontend/types/        typy interfejsu
frontend/tests/        testy Vitest
```

Każdy tekst widoczny dla użytkownika musi korzystać z funkcji lokalizacji i
istnieć we wszystkich czterech katalogach. Klucze tłumaczeń powinny być
znaczeniowe i stabilne, a daty i liczby formatowane zgodnie z językiem. Strony
wymagają świadomych stanów ładowania, błędu, pustego wyniku, zakazu dostępu i
sukcesu. Nieodwracalne działania wymagają okna potwierdzenia oraz, gdzie jest to
dostępne, potwierdzenia po stronie serwera.

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
cd ..
```

## 11. Testy i kontrola jakości

Podczas pracy uruchamiaj testy ukierunkowane:

```bash
.venv/bin/python -m pytest backend/tests/test_projects.py
.venv/bin/python -m pytest agent/tests/test_security.py
cd frontend && npm run test -- projects-utils && cd ..
```

Przed zatwierdzeniem zmian uruchom pełną kontrolę:

```bash
make check
```

Obejmuje kontrolę i formatowanie Ruff, mypy, ESLint, sprawdzanie typów Nuxt,
testy Python i interfejsu, kompilację produkcyjną, audyt npm, składnię skryptów
powłoki i konfigurację Compose. Dla zmian instalatora lub obrazów dodatkowo:

```bash
docker compose --profile telegram build backend frontend worker agent telegram-bot
sudo ./installer/install.sh --domain panel.example.com --email admin@example.com --dry-run
```

Nowa logika wymaga testów, w tym przypadków brzegowych. Domyślnie preferuj testy
jednostkowe; integracyjne dodawaj, gdy atrapy nie dowodzą zachowania na granicy
bazy danych, kolejki, agenta albo systemu plików.

## 12. Niezmienniki przechowywania i wdrażania

```text
/opt/nectarine-panel/              zainstalowana aplikacja
/etc/nectarine-panel/              konfiguracja usług i sekrety
/etc/nginx/vps-panel/              wygenerowane hosty projektów
/srv/vps-panel/projects/{id}/      wydania, bieżący link, dane wspólne, pliki i logi
/srv/vps-panel/backups/            kopie projektów, baz danych i całego panelu
/srv/vps-panel/minecraft/{id}/     dane środowiska Minecraft
```

Wydania są niezmienne, a aktywacja atomowo zastępuje `current`. Trwałe dane
należą do `shared/`, nie do wydania ani niezamontowanej warstwy kontenera.
Rozpakowywanie odrzuca wyjście poza katalog, linki zewnętrzne, pliki specjalne,
nadmierną liczbę elementów i bomby archiwizacyjne. Odtwarzanie sprawdza sumę
kontrolną, typ, zgodność i cel przed zastąpieniem danych.

## 13. Instalacja i cykl życia produkcji

Instaluj sprawdzone wydanie oznaczone tagiem jako administrator:

```bash
sudo ./installer/install.sh \
  --domain panel.example.com \
  --email admin@example.com
```

Instalator tworzy oddzielnych użytkowników, PostgreSQL/Redis, magazyn, środowisko
Python, kompilację interfejsu, migracje, jednostki systemd, Nginx, program
pomocniczy agenta, pierwszego właściciela oraz TLS. Pominięte hasło administratora
jest generowane i pokazywane raz.

| Opcja | Przeznaczenie |
| --- | --- |
| `--domain`, `--email` | Nazwa hosta panelu i adres e-mail dla Let's Encrypt. |
| `--admin-user`, `--admin-password` | Pierwszy właściciel; hasło minimum 12 znaków. |
| `--storage-root` | Bezwzględny katalog trwałych danych. |
| `--telegram-token`, `--telegram-owner-id` | Opcjonalna konfiguracja bota. |
| `--public-ip` | Jawnie podany adres publiczny. |
| `--source` | Instalacja z zaufanego lokalnego drzewa źródeł. |
| `--repository`, `--ref` | Repozytorium i przypięta rewizja Git. |
| `--max-upload-mb` | Limit przesyłania 1–4096 MiB. |
| `--backend-port`, `--frontend-port`, `--agent-port`, `--adminer-port` | Unikalne porty lokalne 1024–65535. |
| `--non-interactive` | Błąd zamiast pytania o brakujące wartości. |
| `--skip-ssl` | HTTP bez wystawiania certyfikatu. |
| `--dry-run` | Walidacja bez instalacji. |

```bash
sudo /opt/nectarine-panel/installer/update.sh --ref vX.Y.Z
sudo /opt/nectarine-panel/installer/uninstall.sh
```

`update.sh` obsługuje `--source`, `--repository`, `--ref`, `--dry-run`, tworzy
awaryjne kopie, wykonuje migracje i sprawdza stan. `uninstall.sh` zachowuje
trwałe dane bez jawnie potwierdzonego `--purge`; obsługuje też
`--yes` i `--dry-run`.

Nie przesyłaj niesprawdzonej zmiennej gałęzi bezpośrednio do powłoki
administratora. Więcej: [instalacja](installation.md),
[proces wydania](releasing.md).

## 14. Diagnostyka produkcji

Zacznij od stanu usług i ograniczonych, świeżych logów:

```bash
sudo systemctl status \
  vps-panel-backend vps-panel-frontend vps-panel-worker \
  vps-panel-scheduler vps-panel-agent vps-panel-telegram-bot
sudo journalctl -u vps-panel-backend -n 200 --no-pager
sudo journalctl -u vps-panel-worker -n 200 --no-pager
sudo journalctl -u vps-panel-agent -n 200 --no-pager
sudo nginx -t
curl -fsS http://127.0.0.1:8000/api/v1/health
```

Użyj faktycznego portu części serwerowej. Sprawdź PostgreSQL, Redis, Docker,
miejsce i i-węzły dysku, DNS, porty 80/443 oraz właścicieli plików w katalogu
magazynu. Odpowiedź `502` z operacji projektu zwykle oznacza, że część serwerowa
nie ukończyła zwalidowanej operacji agenta; skoreluj logi części serwerowej,
procesora i agenta według czasu oraz identyfikatora zadania.

Nie publikuj pełnych plików środowiskowych ani logów bez usunięcia danych
wrażliwych. Usuń tokeny, pliki cookie, hasła, prywatne adresy URL i klucze SSH,
ciągi połączeń z bazą oraz dane użytkowników. Przed ręczną naprawą utwórz kopię i
ustal właściciela stanu. Nie omijaj agenta doraźnymi zmianami administratora,
których panel nie będzie mógł uzgodnić.

## 15. Żądania scalenia i wydania

Twórz skupione zatwierdzenia i dokumentuj migracje, bezpieczeństwo, zgodność
kopii zapasowych oraz wpływ na eksploatację. Przed żądaniem scalenia:

1. sprawdź `git diff` i `git status`;
2. wykonaj `make check` i wymagane kompilacje kontenerów;
3. upewnij się, że nowy tekst interfejsu istnieje w czterech katalogach językowych;
4. upewnij się, że nie dodano `.env`, baz danych, archiwów, tokenów, kluczy
   prywatnych, wewnętrznych nazw hostów ani wygenerowanych plików;
5. zaktualizuj podręcznik użytkownika przy zmianie sposobu pracy, a podręcznik
   dewelopera lub referencję przy zmianie wnętrza systemu.

Dla wydania zaktualizuj dziennik zmian i wersję, sprawdź migracje, utwórz
podpisany tag `vX.Y.Z`, opublikuj sumy kontrolne oraz instrukcje aktualizacji i
wycofania, sprawdź instalację na czystym Ubuntu 24.04 oraz odtwarzanie na
tymczasowym hoście. Przestrzegaj
[CONTRIBUTING.md](../../CONTRIBUTING.md), [SECURITY.md](../../SECURITY.md) i
[releasing.md](releasing.md).
