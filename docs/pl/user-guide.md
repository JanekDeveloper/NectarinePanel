# Podręcznik użytkownika NectarinePanel

[English](../en/user-guide.md) · [Українська](../uk/user-guide.md) ·
[Русский](../ru/user-guide.md) · **Polski**

Ten podręcznik jest przeznaczony dla osób wdrażających projekty i zarządzających
nimi przez interfejs WWW. Instalację serwera i modyfikowanie kodu źródłowego
opisuje [podręcznik dewelopera](developer-guide.md).

> NectarinePanel wykonuje uprzywilejowane operacje na jednym VPS. Przechowuj
> kopię zapasową poza serwerem, testuj odtwarzanie i przyznawaj każdemu
> użytkownikowi wyłącznie niezbędne uprawnienia.

## Instalacja

Instaluj panel na dedykowanym VPS z Ubuntu 24.04 LTS, domeną z rekordem A/AAAA
wskazującym VPS, co najmniej 2 GB RAM, 20 GB wolnego miejsca oraz dostępem root
lub sudo. Sklonuj sprawdzony tag wydania i uruchom instalator jako root:

```bash
git clone --branch vX.Y.Z --depth 1 https://github.com/JanekDeveloper/NectarinePanel.git
cd NectarinePanel
sudo ./installer/install.sh --domain panel.example.com --email admin@example.com
```

| Opcja | Przeznaczenie |
| --- | --- |
| `--domain`, `--email` | Publiczna domena panelu i e-mail dla Let's Encrypt. |
| `--admin-user`, `--admin-password` | Nazwa i hasło pierwszego właściciela. Hasło lepiej podać interaktywnie lub pobrać z bezpiecznego magazynu. |
| `--storage-root` | Ścieżka do trwałego magazynu panelu i projektów. |
| `--telegram-token`, `--telegram-owner-id` | Opcjonalny token bota Telegram i ID czatu właściciela. |
| `--public-ip` | Publiczny adres IP VPS, gdy automatyczne wykrywanie nie jest właściwe. |
| `--source` | Zaufany lokalny checkout zamiast klonowania. |
| `--repository`, `--ref` | URL repozytorium oraz przypięty tag/ref Git do instalacji. |
| `--max-upload-mb` | Maksymalny rozmiar wysyłanych plików w MB. |
| `--backend-port`, `--frontend-port`, `--agent-port`, `--adminer-port` | Zmiana zajętych portów loopback. |
| `--non-interactive` | Nie zadawaj pytań; zakończ działanie bez wymaganych wartości. |
| `--skip-ssl` | Nie wystawiaj certyfikatu; używaj tylko przy zewnętrznym zakończeniu TLS. |
| `--dry-run` | Sprawdź plan instalacji bez zmiany VPS. |

Instalator skonfiguruje usługi, Nginx, TLS, PostgreSQL, Valkey i System Agent.
Jeżeli nie podano `--admin-password`, początkowe hasło właściciela zostanie
wyświetlone tylko raz; zapisz je w menedżerze haseł. Otwórz
`https://panel.example.com`, zaloguj się, zmień dane tymczasowe i sprawdź
`systemctl status vps-panel-backend vps-panel-worker vps-panel-frontend`.

Używaj wyłącznie przypiętego tagu wydania, a nie niesprawdzonej ruchomej gałęzi.
Opis aktualizacji znajduje się w [Aktualizacja panelu](#aktualizacja-panelu), a
opcje instalatora w [dokumentacji instalacji](installation.md).

## 1. Logowanie i wybór języka

Otwórz adres panelu i zaloguj się kontem utworzonym przez instalator lub
właściciela. Język można zmienić na stronie logowania albo w menu bocznym.
Dostępne są języki polski, angielski, ukraiński i rosyjski. Wybór jest
zapisywany w przeglądarce i pliku cookie.

Jeśli włączono logowanie przez Telegram, rozpocznij logowanie w panelu, otwórz
bota z wyświetlonego odnośnika i zatwierdź krótkotrwałe żądanie. Wróć do tej
samej karty przeglądarki — sesja zostanie ukończona automatycznie. Nigdy nie
zatwierdzaj żądania, którego nie rozpocząłeś.

## 2. Role i dostęp

| Rola | Dostęp |
| --- | --- |
| `owner` | Pełny dostęp, użytkownicy, bezpieczeństwo i wszystkie projekty. |
| `admin` | Wszystkie projekty i operacje, bez zarządzania właścicielem i ustawieniami bezpieczeństwa. |
| `maintainer` | Tylko przypisane projekty: wdrożenia, środowisko uruchomieniowe, pliki i edycja zmiennych. Bez ujawniania sekretów i usuwania projektu. |
| `viewer` | Odczyt przypisanych projektów, logów, plików i zamaskowanych zmiennych. |

Członkostwo w projekcie dotyczy ról `maintainer` i `viewer`. Ukryty przycisk nie
stanowi zabezpieczenia — część serwerowa sprawdza każdą operację.

## 3. Tworzenie projektu

Otwórz **Projekty → Nowy projekt**. Najłatwiej zacząć od szablonu; projekt
niestandardowy pozwala skonfigurować wszystkie parametry środowiska uruchomieniowego.

### Wybór źródła

- **Repozytorium Git**: podaj adres HTTPS i gałąź. Każde wdrożenie klonuje nowe,
  niezmienne wydanie. Można wdrożyć konkretną gałąź, tag lub commit.
- **Pliki / ZIP**: utwórz projekt bez repozytorium, a następnie prześlij `.zip`
  w zakładce **Deploye**. Archiwa z niebezpiecznymi ścieżkami, linkami albo
  nadmiernym rozmiarem po rozpakowaniu są odrzucane.

Dla prywatnego repozytorium GitHub skonfiguruj w ustawieniach projektu jedną z
metod:

1. **Fine-grained token**: utwórz na GitHubie token ograniczony do wybranego
   repozytorium z uprawnieniem **Contents: Read-only**. Zapisz go w
   NectarinePanel jako dane uwierzytelniające typu „token”. Użyj zwykłego adresu
   HTTPS; nie umieszczaj tokenu w adresie.
2. **Deploy key**: utwórz osobną parę kluczy SSH, dodaj klucz publiczny w
   **Repository → Settings → Deploy keys** bez prawa zapisu, a klucz prywatny
   zapisz w NectarinePanel jako klucz wdrożeniowy. Użyj adresu SSH repozytorium.

Dane uwierzytelniające są szyfrowane i nie trafiają do parametrów zadań
wdrożeniowych. Gdy dostęp do repozytorium się zmienia, od razu usuń lub zastąp
klucz.

### Wybór środowiska uruchomieniowego

| Środowisko | Zastosowanie | Ważne zachowanie |
| --- | --- | --- |
| Kontener Docker | Większość API, botów i aplikacji WWW | Buduje `Dockerfile` z repozytorium; zapewnia izolację i egzekwowanie limitów zasobów. |
| Docker Compose | Aplikacje wielokontenerowe | Uruchamia plik Compose; zasady zasobów są tylko monitorowane. |
| Statyczny Nginx | Zbudowane strony statyczne | Publikuje wskazany katalog wyjściowy bez procesu aplikacji. |
| systemd | Aplikacje uruchamiane bezpośrednio na serwerze | Uruchamia polecenie startowe jako oddzielny, nieuprzywilejowany użytkownik. |
| PM2 | Aplikacje Node.js korzystające z PM2 | PM2 działa wewnątrz ograniczonej jednostki systemd projektu. |
| Minecraft Forge | Serwer Forge | Osobna konfiguracja, konsola, pliki i proces tworzenia kopii. |

Zmiana polecenia startowego w panelu nie modyfikuje `Dockerfile` z repozytorium.
Dla Docker polecenie kontenera definiują `CMD` i `ENTRYPOINT` w Dockerfile.
Zmień je albo użyj konfigurowalnego programu uruchamiającego. Polecenie z panelu
jest używane przez środowiska działające bezpośrednio na serwerze i podczas
początkowego generowania szablonu.

### Kontrola przed pierwszym wdrożeniem

- gałąź istnieje, a dane uwierzytelniające mają dostęp do odczytu;
- polecenia budowania i uruchamiania pasują do wybranego środowiska;
- wewnątrz Docker aplikacja nasłuchuje na `0.0.0.0`, nie tylko `127.0.0.1`;
- port proxy odpowiada portowi aplikacji;
- sekrety znajdują się w **Zmienne**, a nie w Git;
- trwałe dane są montowane z `shared/`;
- kontrola stanu HTTP zwraca kod 2xx lub 3xx.

## 4. Wdrożenie i wycofanie

Otwórz projekt i zakładkę **Deploye**.

- Wdrożenie z Git może przyjąć konkretną rewizję. Puste pole używa
  skonfigurowanej gałęzi.
- Wdrożenie z ZIP przyjmuje wyłącznie `.zip` i służy projektom bez repozytorium.
- Aktywne zadanie pokazuje stan i postęp wykonania.
- Historia wydań zawiera rewizję, czas, ścieżkę, wynik i zapisany log.

Udane wdrożenie tworzy wydanie w
`projects/{project_id}/releases/{release_id}` i atomowo kieruje `current` do
niego. Nieudane wydanie nie zastępuje aktywnego.

Aby wycofać wdrożenie, wybierz udane wydanie, potwierdź jego nazwę lub rewizję i
uruchom operację. Wycofanie aktywuje wydanie, ale nie odtwarza baz danych ani
trwałych plików z `shared/`. W razie potrzeby odtwórz je z odpowiadającego
kopii zapasowej.

Jeśli zadanie się nie powiedzie, otwórz log i napraw pierwszy istotny błąd.
Ponowienie wdrożenia bez zmiany źródła lub konfiguracji zazwyczaj da ten sam wynik.

## 5. Obsługa uruchomionego projektu

Przegląd pokazuje stan i dostępne metryki. Przyciski odpowiadają faktycznemu
stanowi: **Uruchom** jest widoczny dla zatrzymanego projektu, a **Zatrzymaj** i
**Uruchom ponownie** dla działającego.

- **Logi** pokazują dzienniki środowiska uruchomieniowego i, jeśli to możliwe,
  aktualizują się na żywo.
- **Konsola** wysyła polecenia do środowiska projektu i nie jest powłoką
  administratora.
  Polecenia Docker działają w kontenerze projektu, Minecraft używa RCON, a
  systemd, PM2 i Compose udostępniają ograniczone operacje.
- **Cron** wykonuje polecenia przez tę samą zabezpieczoną granicę środowiska.
  Projekty statyczne nie obsługują zadań cron.

Komunikat `no job control in this shell` oznacza, że interaktywna powłoka została
uruchomiony bez prawdziwego TTY. Zwykłe polecenia działają, ale `Ctrl+Z`, `fg` i
`bg` są niedostępne. W konsoli WWW używaj poleceń nieinteraktywnych.

## 6. Zmienne i sekrety

Zakładka **Zmienne** jest źródłem zmiennych środowiskowych dla środowiska uruchomieniowego.

- Hasła, tokeny i inne dane uwierzytelniające oznaczaj jako sekrety.
- Sekretne wartości są szyfrowane i maskowane na liście.
- **Ujawnij** wymaga wpisania dokładnego klucza. Zdarzenie trafia do audytu bez
  wartości jawnej.
- **Historia** przechowuje wersje. Wycofanie tworzy nową wersję i nie usuwa
  historii.
- Import `.env` odczytuje dosłowne wpisy `KEY=value` i nie rozwija zmiennych
  powłoki. Tryb **Scal** aktualizuje podane klucze, a **Zastąp** usuwa klucze
  nieobecne w importowanej treści.

Zmiana zmiennych nie uruchamia wdrożenia automatycznie. Uruchom projekt ponownie
lub wdróż go ponownie. Ujawniony sekret traktuj jako odsłonięty: nie wklejaj go
do logów, zrzutów ekranu, systemu zadań ani czatu.

## 7. Bezpieczna praca z plikami

Katalog główny projektu zawiera:

```text
releases/   niezmienne wydania
current     dowiązanie symboliczne do aktywnego wydania
shared/     trwałe pliki zachowywane między wdrożeniami
```

Kliknij folder lub segment ścieżki, aby przejść. Obsługiwane operacje zbiorcze
działają na wielu zaznaczonych elementach. Obliczenie rozmiaru dużego katalogu
może potrwać dłużej.

`current` jest dowiązaniem symbolicznym, nie zwykłym katalogiem. Panel
bezpiecznie rozwiązuje linki wewnątrz katalogu projektu. Jeśli aplikacja zapisze
dane wewnątrz kontenera bez powiązania katalogu z `shared/`, pliki nie pojawią
się w menedżerze i mogą zniknąć po zastąpieniu kontenera. SQLite, przesłane
pliki i pozostały
stan zapisuj na przykład w zamontowanym `shared/data`.

Przed usunięciem lub przenoszeniem zatrzymaj aplikację, która może zapisywać te
pliki. Przed masową albo nieodwracalną zmianą utwórz kopię zapasową.

## 8. Domeny i TLS

Przed dodaniem domeny utwórz rekord DNS A/AAAA wskazujący VPS i poczekaj na jego
publiczne rozwiązywanie. W zakładce **Domeny**:

1. dodaj nazwę hosta i sprawdź port proxy;
2. przetestuj wygenerowaną trasę HTTP;
3. wystaw certyfikat Let's Encrypt;
4. sprawdź HTTPS oraz kontrolę stanu aplikacji.

Nie wymuszaj wielokrotnie wystawienia certyfikatu podczas naprawy DNS lub
trasowania — Let's Encrypt stosuje limity. Odnowienie działa według harmonogramu,
a panel zapisuje termin ważności i odcisk certyfikatu.

## 9. Bazy danych

Serwer bazy danych i baza projektu są oddzielnymi obiektami. Najpierw dodaj dane
uwierzytelniające serwera, następnie utwórz lub podłącz bazę. Obsługiwane są
PostgreSQL, MySQL/MariaDB, SQLite i Redis/Valkey z możliwościami właściwymi dla
danego silnika.

- Ciąg połączenia można dołączyć do zmiennej projektu.
- Tworzenie zrzutu, odtwarzanie i usuwanie działają jako zadania w tle.
- Odtwarzanie wymaga zgodnego zrzutu oraz potwierdzenia.
- Zatrzymaj projekt przed odtwarzaniem lub usuwaniem SQLite.
- Redis/Valkey pokazuje połączenie i stan, ale nie udaje tabel relacyjnych
  ani nieistniejącej izolacji.

Przed nieodwracalną migracją utwórz świeży zrzut i sprawdź aplikację po odtworzeniu.

## 10. Kopie zapasowe i odtwarzanie

Automatyczne kopie są wyłączone do czasu skonfigurowania zasad. Kopia projektu
może obejmować środowisko, aktywne wydanie, dane wspólne, przesłane pliki, logi,
zrzuty baz danych oraz dane Minecraft. Pełna kopia panelu może zawierać bazę
panelu, konfigurację, hosty Nginx, projekty i stan harmonogramu.

Każde archiwum ma manifest i sumę kontrolną SHA-256. Stan „ukończono” potwierdza
utworzenie archiwum, ale nie jego odtwarzalność. W produkcji:

1. ustaw harmonogram i okres przechowywania;
2. kopiuj krytyczne archiwa poza VPS;
3. testuj odtwarzanie na tymczasowym projekcie lub hoście;
4. zapisuj datę ostatniego poprawnego testu.

Odtwarzanie sprawdza typ archiwum, zgodność, sumę kontrolną i projekt docelowy.
Może zastąpić pliki, dlatego najpierw zatrzymaj projekt i utwórz nową kopię.

## 11. Monitorowanie i limity zasobów

Główna strona monitorowania pokazuje użycie procesora, pamięci, dysku i sieci
VPS oraz ostrzeżenia. Strona projektu pokazuje metryki środowiska i dysku tylko wtedy,
gdy istnieje pomiar.
„Brak danych” oznacza brak prawidłowego pomiaru, nie zerowe użycie.

Właściciel i administrator mogą włączyć limity:

- limity procesora i pamięci są egzekwowane dla obsługiwanych środowisk Docker
  i środowisk uruchamianych bezpośrednio na serwerze;
- limity Docker Compose są tylko monitorowane;
- limit dysku tworzy ostrzeżenie, ale nie przydział systemu plików;
- Minecraft wymaga co najmniej 512 MB ponad skonfigurowane JVM `Xmx`.

Kontrola stanu powinna być szybka, nie wymagać uwierzytelnienia i potwierdzać
gotowość usługi. Błąd lub odpowiedź inna niż 2xx/3xx tworzy jedno ostrzeżenie bez
duplikatów.

## 12. Użytkownicy, audyt i Telegram

Właściciel zarządza kontami w **Ustawienia → Użytkownicy**. Administrator ma
globalny dostęp operacyjny. Użytkownicy z rolami `maintainer` i `viewer` muszą
zostać przypisani do projektów w ustawieniach projektu. Ostatniego aktywnego
właściciela nie można wyłączyć.

Sprawdzaj **Audyt** po zmianach uprawnień, ujawnieniu sekretów, odtwarzaniu,
zmianie domen i nieodwracalnych operacjach. Szczegóły audytu nie zawierają haseł ani
sekretnych wartości.

Telegram jest opcjonalny. Właściciel podaje token bota i dozwolony identyfikator
użytkownika Telegram w ustawieniach, po czym restartuje usługę bota po ich zmianie. Bot
dostarcza alerty i wykonuje potwierdzone operacje. Nie traktuj historii czatu
jako magazynu sekretów.

## Aktualizacja panelu

Aktualizuj panel do przypiętego, sprawdzonego wydania w oknie serwisowym. Przed
rozpoczęciem utwórz i sprawdź kopię zapasową panelu oraz ważnych projektów poza VPS.

1. Połącz się z VPS i wybierz tag wydania, a nie niezweryfikowaną ruchomą gałąź.
2. Uruchom `sudo /opt/nectarine-panel/installer/update.sh --ref vX.Y.Z`.
3. Poczekaj na kopię panelu, migracje, build klienta webowego, restart usług i
   test zdrowia.
4. Zaloguj się ponownie i sprawdź projekty, monitoring, wdrożenia oraz trwałe
   dane. Przy błędzie testu sprawdź `systemctl status vps-panel-backend vps-panel-worker`.

Do sprawdzenia użyj najpierw `--dry-run`. `--source` stosuj wyłącznie dla
zaufanego lokalnego checkoutu. Nie przerywaj aktualizatora ani nie zamieniaj
plików ręcznie podczas pracy; w razie potrzeby odtwórz zweryfikowaną kopię.

## 13. Lista kontrolna środowiska produkcyjnego

Codziennie lub po alercie:

- sprawdź projekty z błędami, kontrole stanu, dysk i nieprzeczytane powiadomienia;
- przeanalizuj właściwy log przed wielokrotnym restartem;
- potwierdź, że zaplanowane kopie nadal się wykonują.

Po każdym wydaniu lub zmianie konfiguracji:

- sprawdź stan i logi;
- sprawdź publiczny adres HTTPS;
- potwierdź, że trwałe dane przetrwały wdrożenie;
- szybko wycofaj wdrożenie, jeśli wydanie jest niesprawne.

Regularnie:

- aktualizuj NectarinePanel wyłącznie z wydania oznaczonego tagiem;
- zmieniaj dane uwierzytelniające użytkowników, Git, baz danych, Telegram i aplikacji;
- usuwaj nieużywane konta, przypisania do projektów, domeny i kopie;
- wykonuj i dokumentuj test odtwarzania poza głównym VPS.

## 14. Typowe problemy

| Objaw | Co sprawdzić |
| --- | --- |
| Git nie klonuje repozytorium | Adres, gałąź, dostęp tokenu lub klucza wdrożeniowego do odczytu i ważność danych. |
| Wdrożenie jest udane, ale usługa nie działa | Log środowiska, adres nasłuchiwania, port proxy, zmienne i ścieżkę kontroli stanu. |
| Plik zniknął po ponownym wdrożeniu | Zapisano go w niezmiennym wydaniu lub warstwie kontenera zamiast w `shared/`. |
| Nie można otworzyć `current` | Brak udanego wydania albo usunięto cel linku; sprawdź historię. |
| Brak metryk | Projekt jeszcze nie działał, kolektor lub agent jest niedostępny albo środowisko nie udostępnia metryki. |
| Nie można wystawić TLS | Publiczny DNS, porty 80/443, trasę Nginx i limity zapytań. |
| Odpowiedź 403 | Rola lub przypisanie do projektu nie zezwala na operację. |
| Odpowiedź 502 | Część serwerowa nie ukończyła zwalidowanej operacji agenta; właściciel powinien sprawdzić logi panelu i agenta. |

Diagnostyka serwera, instalacja, aktualizacja i odtwarzanie są opisane w
[podręczniku dewelopera](developer-guide.md). Techniczne materiały
referencyjne pozostają w katalogu [`docs/`](./).
