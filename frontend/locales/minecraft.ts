import type { PanelLocale } from "~/locales/messages";

const en = {
  saved: "Settings saved.",
  savedPassword: "Settings saved. New RCON password: {password}",
  started: "Minecraft Forge server started.",
  installed: "Minecraft and Forge installed.",
  installError: "Installation failed{error}.",
  installStarted: "Forge download and installation started.",
  fileSaved:
    "{file} saved. Restart the server if the change requires a reload.",
  description: "Java, memory, EULA, game port, and local-only RCON.",
  start: "Start",
  newRconPassword: "New RCON password",
  generateAutomatically: "Generate automatically",
  enable: "Enable",
  acceptEula: "I accept the Minecraft EULA",
  saveConfiguration: "Save configuration",
  installTitle: "Install / update Forge",
  installDescription:
    "Select versions and the official installer will be downloaded automatically.",
  installedVersion: "Installed: Minecraft {minecraft}, Forge {forge}",
  javaAutomatic: "Java {version} will be selected automatically.",
  installing: "Installing…",
  installForge: "Install Forge",
  installProgress: "Installation progress",
  catalogError: "Could not load the Forge catalog.",
  catalogLoading: "Loading version catalog…",
  serverConfigs: "Server configuration",
  noMods: "No JAR mods found. Upload them to mods/ using the file manager.",
  modsHint:
    "Upload mods to mods/ using the file manager. The project directory is isolated at {path}.",
  serverJar: "Server JAR",
  gamePort: "Game port",
  rconPort: "RCON host port",
  mods: "Mods",
};
type Copy = Record<keyof typeof en, string>;
const ru: Copy = {
  saved: "Настройки сохранены.",
  savedPassword: "Настройки сохранены. Новый пароль RCON: {password}",
  started: "Сервер Minecraft Forge запущен.",
  installed: "Minecraft и Forge установлены.",
  installError: "Установка завершилась ошибкой{error}.",
  installStarted: "Загрузка и установка Forge запущены.",
  fileSaved:
    "{file} сохранён. Перезапустите сервер, если изменение требует перезагрузки.",
  description:
    "Java, память, EULA, игровой порт и RCON только для локального доступа.",
  start: "Запустить",
  newRconPassword: "Новый пароль RCON",
  generateAutomatically: "Сгенерировать автоматически",
  enable: "Включить",
  acceptEula: "Я принимаю Minecraft EULA",
  saveConfiguration: "Сохранить конфигурацию",
  installTitle: "Установка / обновление Forge",
  installDescription:
    "Выберите версии — официальный установщик загрузится автоматически.",
  installedVersion: "Установлено: Minecraft {minecraft}, Forge {forge}",
  javaAutomatic: "Java {version} будет выбрана автоматически.",
  installing: "Установка…",
  installForge: "Установить Forge",
  installProgress: "Прогресс установки",
  catalogError: "Не удалось загрузить каталог Forge.",
  catalogLoading: "Загрузка каталога версий…",
  serverConfigs: "Конфиги сервера",
  noMods: "JAR-моды не найдены. Загружайте их через файловый менеджер в mods/.",
  modsHint:
    "Загружайте моды через файловый менеджер в mods/. Директория проекта изолирована в {path}.",
  serverJar: "Серверный JAR",
  gamePort: "Игровой порт",
  rconPort: "Порт RCON на хосте",
  mods: "Моды",
};
const uk: Copy = {
  saved: "Налаштування збережено.",
  savedPassword: "Налаштування збережено. Новий пароль RCON: {password}",
  started: "Сервер Minecraft Forge запущено.",
  installed: "Minecraft і Forge встановлено.",
  installError: "Встановлення завершилося помилкою{error}.",
  installStarted: "Завантаження та встановлення Forge запущено.",
  fileSaved:
    "{file} збережено. Перезапустіть сервер, якщо зміна потребує перезавантаження.",
  description:
    "Java, пам'ять, EULA, ігровий порт і RCON лише для локального доступу.",
  start: "Запустити",
  newRconPassword: "Новий пароль RCON",
  generateAutomatically: "Згенерувати автоматично",
  enable: "Увімкнути",
  acceptEula: "Я приймаю Minecraft EULA",
  saveConfiguration: "Зберегти конфігурацію",
  installTitle: "Встановлення / оновлення Forge",
  installDescription:
    "Виберіть версії — офіційний інсталятор завантажиться автоматично.",
  installedVersion: "Встановлено: Minecraft {minecraft}, Forge {forge}",
  javaAutomatic: "Java {version} буде вибрано автоматично.",
  installing: "Встановлення…",
  installForge: "Встановити Forge",
  installProgress: "Прогрес встановлення",
  catalogError: "Не вдалося завантажити каталог Forge.",
  catalogLoading: "Завантаження каталогу версій…",
  serverConfigs: "Конфігурація сервера",
  noMods:
    "JAR-моди не знайдено. Завантажуйте їх через файловий менеджер у mods/.",
  modsHint:
    "Завантажуйте моди через файловий менеджер у mods/. Директорію проєкту ізольовано в {path}.",
  serverJar: "Серверний JAR",
  gamePort: "Ігровий порт",
  rconPort: "Порт RCON на хості",
  mods: "Моди",
};
const pl: Copy = {
  saved: "Ustawienia zapisano.",
  savedPassword: "Ustawienia zapisano. Nowe hasło RCON: {password}",
  started: "Serwer Minecraft Forge uruchomiono.",
  installed: "Minecraft i Forge zainstalowano.",
  installError: "Instalacja nie powiodła się{error}.",
  installStarted: "Rozpoczęto pobieranie i instalację Forge.",
  fileSaved:
    "Zapisano {file}. Uruchom serwer ponownie, jeśli zmiana wymaga przeładowania.",
  description: "Java, pamięć, EULA, port gry i RCON dostępny tylko lokalnie.",
  start: "Uruchom",
  newRconPassword: "Nowe hasło RCON",
  generateAutomatically: "Wygeneruj automatycznie",
  enable: "Włącz",
  acceptEula: "Akceptuję Minecraft EULA",
  saveConfiguration: "Zapisz konfigurację",
  installTitle: "Instalacja / aktualizacja Forge",
  installDescription:
    "Wybierz wersje, a oficjalny instalator zostanie pobrany automatycznie.",
  installedVersion: "Zainstalowano: Minecraft {minecraft}, Forge {forge}",
  javaAutomatic: "Java {version} zostanie wybrana automatycznie.",
  installing: "Instalowanie…",
  installForge: "Zainstaluj Forge",
  installProgress: "Postęp instalacji",
  catalogError: "Nie udało się załadować katalogu Forge.",
  catalogLoading: "Ładowanie katalogu wersji…",
  serverConfigs: "Konfiguracja serwera",
  noMods:
    "Nie znaleziono modów JAR. Prześlij je przez menedżer plików do mods/.",
  modsHint:
    "Przesyłaj mody przez menedżer plików do mods/. Katalog projektu jest odizolowany w {path}.",
  serverJar: "JAR serwera",
  gamePort: "Port gry",
  rconPort: "Port RCON hosta",
  mods: "Mody",
};

export const minecraftMessages: Record<PanelLocale, Copy> = { ru, en, uk, pl };
