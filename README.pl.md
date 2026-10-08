<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./nectarinepanel_dark_logo_wordmark.png">
    <img src="./nectarinepanel_logo_wordmark.png" alt="NectarinePanel" width="520">
  </picture>
</p>

<p align="center">
  Samodzielnie hostowany panel wdrażania i zarządzania aplikacjami na jednym VPS.
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.uk.md">Українська</a> ·
  <a href="README.ru.md">Русский</a> · <strong>Polski</strong>
</p>

> [!IMPORTANT]
> NectarinePanel nie osiągnął jeszcze wersji 1.0 i ma uprzywilejowany dostęp do
> serwera. Przed aktualizacją sprawdź, czy przywracanie działa, przechowuj kopie
> zapasowe poza VPS-em i instaluj wydania oznaczone tagiem zamiast kodu z ciągle
> zmieniającej się gałęzi.

## Zastosowanie

NectarinePanel wdraża i obsługuje strony, API, boty, aplikacje Docker oraz
serwery Minecraft Forge na jednym VPS z Ubuntu. FastAPI odpowiada za
uwierzytelnianie i stan, Celery wykonuje długie zadania, a lokalny agent systemowy
realizuje wyłącznie wcześniej zdefiniowane operacje uprzywilejowane.

Najważniejsze funkcje:

- wdrożenia Git i ZIP, prywatne repozytoria, webhooki GitHub, historia wydań,
  logi i wycofanie;
- Docker, Docker Compose, statyczny Nginx, systemd, PM2 i Minecraft Forge;
- uruchamianie, zatrzymywanie, restart, konsola, cron, menedżer plików, SFTP,
  domeny i certyfikaty
  Let's Encrypt;
- PostgreSQL, MySQL/MariaDB, SQLite, Redis/Valkey, zrzuty, import, odtwarzanie oraz
  chroniony Adminer;
- kopie projektów i całego panelu z sumą kontrolną, manifestem, okresem
  przechowywania i opcjonalnym
  szyfrowaniem;
- monitorowanie VPS i projektów, kontrole stanu, ostrzeżenia i powiadomienia Telegram;
- szyfrowane zarządzanie sekretami: maskowanie, audyt ujawnienia, wersje, import
  `.env` i wycofanie;
- role `owner`, `admin`, `maintainer`, `viewer` i dostęp na poziomie projektu;
- limity CPU/RAM oraz monitorowanie limitu dysku.

## Zrzuty ekranu

Interfejs korzysta z ciemnego, przejrzystego układu centrum sterowania. Poniżej
przedstawiono główny przepływ — od logowania do utworzenia i obsługi projektu:

<p align="center">
  <img src="docs/images/login.png" alt="Logowanie do NectarinePanel" width="48%">
  <img src="docs/images/overview.png" alt="Przegląd VPS w NectarinePanel" width="48%">
</p>
<p align="center">
  <img src="docs/images/project-create.png" alt="Wybór podstawy projektu" width="48%">
  <img src="docs/images/project-and-source-code.png" alt="Projekt i kod źródłowy" width="48%">
</p>
<p align="center">
  <img src="docs/images/runtime-and-build.png" alt="Konfiguracja środowiska i kompilacji" width="48%">
  <img src="docs/images/review-configuration.png" alt="Przegląd konfiguracji projektu" width="48%">
</p>
<p align="center">
  <img src="docs/images/empty-example.png" alt="Widok projektu z pustymi stanami" width="70%">
</p>

## Architektura

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker / scheduler
                            -> localhost system agent -> host services / Docker
Telegram -> aiogram -> internal authenticated FastAPI endpoints
```

Część serwerowa nie udostępnia uniwersalnej powłoki administratora. Zmiany
systemowe przechodzą przez uwierzytelnionego agenta, typowane operacje, stałe argumenty poleceń i
kontrolę granic ścieżek plików.

Więcej: [architektura](docs/pl/architecture.md) i
[bezpieczeństwo](docs/pl/security.md).

## Wymagania

Produkcja:

- Ubuntu 24.04 LTS na dedykowanym VPS;
- domena z rekordem A/AAAA wskazującym VPS;
- dostęp administratora lub sudo;
- minimum 2 GB RAM i 20 GB wolnego miejsca, poza zasobami aplikacji.

Rozwój:

- Python 3.12+;
- Node.js 22.22.3 LTS, 24.15+ LTS lub 26+;
- npm 10+;
- Docker Engine i Compose v2.

## Instalacja produkcyjna

Sprawdź instalator, sklonuj wydanie oznaczone tagiem i uruchom jako administrator:

```bash
git clone --branch v0.1.0 --depth 1 \
  https://github.com/JanekDeveloper/NectarinePanel.git
cd NectarinePanel
sudo ./installer/install.sh \
  --domain panel.example.com \
  --email admin@example.com
```

Bez `--admin-password` instalator wygeneruje hasło i pokaże je jeden raz.
Skonfiguruje PostgreSQL/Redis, osobnych użytkowników systemowych, systemd,
Nginx, TLS, agenta systemowego oraz pierwsze konto właściciela.

Po opublikowaniu tagowanego wydania można użyć jednej komendy:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/NectarinePanel/NectarinePanel/v0.1.0/installer/install.sh \
  | sudo bash -s -- --domain panel.example.com --email admin@example.com
```

Nie zastępuj tagu przez `main` w poleceniu administratora. Wszystkie
opcje opisuje [docs/pl/installation.md](docs/pl/installation.md).

## Pierwszy projekt

1. Zaloguj się danymi właściciela wyświetlonymi przez instalator.
2. Otwórz **Projekty → Nowy projekt** i wybierz szablon lub środowisko.
3. Dodaj adres Git lub ZIP. Dla prywatnego GitHub użyj tokenu z ograniczonym
   dostępem albo klucza wdrożeniowego tylko do odczytu.
4. Przechowuj sekrety w **Zmienne**, a trwałe dane kontenera w katalogu
   zamontowanym z
   `shared/`.
5. Uruchom wdrożenie w **Wdrożenia**, następnie dodaj domenę i TLS.
6. Ustaw zasady kopii zapasowych i sprawdź odtworzenie kopii.

## Lokalne środowisko

```bash
cp .env.example .env
docker compose up --build
docker compose exec backend python -m app.cli create-admin --username admin
```

Panel: `http://localhost:3000`, OpenAPI:
`http://localhost:8000/api/v1/docs`. Porty deweloperskie są dostępne wyłącznie
przez interfejs lokalny. Ta konfiguracja nie jest wdrożeniem produkcyjnym.

Praca bez kontenerów:

```bash
python3 -m venv .venv
make setup
make migrate
make backend
make frontend
```

## Weryfikacja

```bash
make lint
make test
make build-frontend
make audit-frontend
make compose-check
```

`make check` uruchamia pełną lokalną kontrolę. GitHub Actions sprawdza Python,
interfejs, instalator, Compose oraz obrazy Docker w każdym żądaniu scalenia.

## Aktualizacja i usunięcie

```bash
sudo /opt/nectarine-panel/installer/update.sh
sudo /opt/nectarine-panel/installer/uninstall.sh
```

Usunięcie zachowuje konfigurację i dane, dopóki `--purge` nie zostanie jawnie
potwierdzone. Aktualizator tworzy awaryjną kopię przed migracjami i wymianą usług.

## Aktualne ograniczenia

- jeden VPS z Ubuntu bez wieloserwerowego zarządzania;
- lokalny magazyn kopii — ważne kopie trzeba przenosić poza VPS;
- zasady zasobów Docker Compose służą tylko do monitorowania;
- limit dysku tworzy ostrzeżenie, ale nie przydział systemu plików;
- brak automatyzacji Cloudflare DNS;
- interfejs WWW oraz główne podręczniki użytkownika i dewelopera są dostępne po
  angielsku, ukraińsku, rosyjsku i polsku.

## Dokumentacja

- [Podręcznik użytkownika](docs/pl/user-guide.md) — wdrażanie projektów i
  zarządzanie nimi przez interfejs WWW;
- [Podręcznik dewelopera](docs/pl/developer-guide.md) — architektura, środowisko
  lokalne, rozwój, testy, instalacja, wydania i diagnostyka;
- inne języki: [English](docs/en/user-guide.md),
  [Українська](docs/uk/user-guide.md), [Русский](docs/ru/user-guide.md).

Dokumentacja techniczna:

[Instalacja](docs/pl/installation.md) · [Architektura](docs/pl/architecture.md) ·
[Bezpieczeństwo](docs/pl/security.md) · [Projekty](docs/pl/projects.md) ·
[Bazy danych](docs/pl/databases.md) · [Kopie zapasowe](docs/pl/backups.md) ·
[Monitorowanie](docs/pl/monitoring.md) · [Telegram](docs/pl/telegram.md) ·
[Minecraft Forge](docs/pl/minecraft-forge.md) ·
[Paper / Purpur / Spigot](docs/pl/minecraft-servers.md) ·
[Rozwój](docs/pl/development.md) · [Wydawanie wersji](docs/pl/releasing.md)

Przed żądaniem scalenia przeczytaj [CONTRIBUTING.md](CONTRIBUTING.md). Podatności
zgłaszaj prywatnie zgodnie z [SECURITY.md](SECURITY.md).

Licencja: [MIT](LICENSE).
