<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./nectarinepanel_dark_logo_wordmark.png">
    <img src="./nectarinepanel_logo_wordmark.png" alt="NectarinePanel" width="520">
  </picture>
</p>

<p align="center">
  Самостійно розміщувана панель розгортання та керування застосунками на одному VPS.
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>Українська</strong> ·
  <a href="README.ru.md">Русский</a> · <a href="README.pl.md">Polski</a>
</p>

> [!IMPORTANT]
> NectarinePanel ще не досяг версії 1.0 і має привілейований доступ до сервера.
> Перед оновленням перевіряйте відновлення, зберігайте резервні копії за межами
> VPS та встановлюйте релізи з тегами, а не код із гілки, що постійно змінюється.

## Призначення

NectarinePanel розгортає та обслуговує сайти, API, ботів, Docker-застосунки й
сервери Minecraft Forge на одному Ubuntu VPS. FastAPI відповідає за
автентифікацію та стан, Celery виконує довгі задачі, а локальний системний агент —
лише заздалегідь визначені привілейовані операції.

Основні можливості:

- розгортання з Git або ZIP, приватні репозиторії, вебперехоплювачі GitHub,
  історія релізів, логи та відкат;
- Docker, Docker Compose, статичний Nginx, systemd, PM2 і Minecraft Forge;
- запуск, зупинка, перезапуск, консоль, cron, файловий менеджер, SFTP, домени та
  сертифікати Let's Encrypt;
- PostgreSQL, MySQL/MariaDB, SQLite, Redis/Valkey, дамп, імпорт, відновлення і
  захищений Adminer;
- резервні копії проєктів і панелі з контрольною сумою, маніфестом, строком зберігання та
  опціональним шифруванням;
- моніторинг VPS і проєктів, перевірки працездатності, сповіщення й Telegram;
- зашифроване керування секретами: маскування, аудит перегляду, версії, імпорт
  `.env` і відкат;
- ролі `owner`, `admin`, `maintainer`, `viewer` та доступ на рівні проєкту;
- обмеження процесора й пам'яті та моніторинг ліміту диска.

## Знімки екрана

Інтерфейс виконано в темному стилі панелі керування. Нижче показано основний
сценарій — від входу до створення проєкту та його експлуатації:

<p align="center">
  <img src="docs/images/login.png" alt="Вхід до NectarinePanel" width="48%">
  <img src="docs/images/overview.png" alt="Огляд VPS у NectarinePanel" width="48%">
</p>
<p align="center">
  <img src="docs/images/project-create.png" alt="Крок вибору основи проєкту" width="48%">
  <img src="docs/images/project-and-source-code.png" alt="Проєкт і вихідний код" width="48%">
</p>
<p align="center">
  <img src="docs/images/runtime-and-build.png" alt="Налаштування середовища та збірки" width="48%">
  <img src="docs/images/review-configuration.png" alt="Перевірка конфігурації проєкту" width="48%">
</p>
<p align="center">
  <img src="docs/images/empty-example.png" alt="Огляд проєкту з порожніми станами" width="70%">
</p>

## Архітектура

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker / scheduler
                            -> localhost system agent -> host services / Docker
Telegram -> aiogram -> internal authenticated FastAPI endpoints
```

Серверна частина не надає універсальну оболонку суперкористувача. Системні зміни
проходять через автентифікований агент, типізовані операції, фіксовані аргументи команд і
перевірку меж файлових шляхів.

Докладніше: [архітектура](docs/uk/architecture.md) і
[безпека](docs/uk/security.md).

## Вимоги

Робоче середовище:

- Ubuntu 24.04 LTS на окремому VPS;
- домен з A/AAAA записом на адресу VPS;
- доступ суперкористувача або sudo;
- щонайменше 2 GB RAM і 20 GB вільного диска без урахування застосунків.

Розробка:

- Python 3.12+;
- Node.js 22.22.3 LTS, 24.15+ LTS або 26+;
- npm 10+;
- Docker Engine із Compose v2.

## Встановлення в робочому середовищі

Перегляньте встановлювач, клонуйте реліз із тегом і запустіть від імені
суперкористувача:

```bash
git clone --branch v0.1.0 --depth 1 \
  https://github.com/JanekDeveloper/NectarinePanel.git
cd NectarinePanel
sudo ./installer/install.sh \
  --domain panel.example.com \
  --email admin@example.com
```

Якщо не вказати `--admin-password`, встановлювач згенерує пароль і покаже його
один раз. Він налаштує PostgreSQL/Redis, окремих системних користувачів,
systemd, Nginx, TLS, системний агент і першого власника.

Після публікації тегованого релізу доступне встановлення однією командою:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/NectarinePanel/NectarinePanel/v0.1.0/installer/install.sh \
  | sudo bash -s -- --domain panel.example.com --email admin@example.com
```

Не замінюйте тег на `main` у команді суперкористувача. Усі параметри:
[docs/uk/installation.md](docs/uk/installation.md).

## Перший проєкт

1. Увійдіть з даними власника, які показав встановлювач.
2. Відкрийте **Проєкти → Новий проєкт** і виберіть шаблон або середовище виконання.
3. Додайте URL Git або ZIP. Для приватного GitHub використовуйте токен з
   обмеженим доступом або ключ розгортання лише для читання.
4. Зберігайте секрети у **Змінні**, а постійні дані контейнера — у каталозі,
   змонтованому з
   `shared/`.
5. Запустіть розгортання у вкладці **Розгортання**, додайте домен і TLS.
6. Налаштуйте політику резервного копіювання та обов'язково перевірте відновлення.

## Локальна розробка

```bash
cp .env.example .env
docker compose up --build
docker compose exec backend python -m app.cli create-admin --username admin
```

Панель: `http://localhost:3000`, OpenAPI:
`http://localhost:8000/api/v1/docs`. Порти середовища розробки доступні лише
через локальний інтерфейс. Ця конфігурація не призначена для робочої системи.

Розробка без контейнерів:

```bash
python3 -m venv .venv
make setup
make migrate
make backend
make frontend
```

## Перевірки

```bash
make lint
make test
make build-frontend
make audit-frontend
make compose-check
```

`make check` запускає повну локальну перевірку. GitHub Actions перевіряє Python,
інтерфейс, встановлювач, Compose та образи Docker в кожному запиті на злиття.

## Оновлення та видалення

```bash
sudo /opt/nectarine-panel/installer/update.sh
sudo /opt/nectarine-panel/installer/uninstall.sh
```

Видалення зберігає конфігурацію й дані, доки явно не підтверджено `--purge`.
Програма оновлення створює аварійну резервну копію до міграцій і заміни сервісів.

## Поточні обмеження

- один Ubuntu VPS без багатосерверного керування;
- сховище резервних копій локальне — важливі копії потрібно виносити з VPS;
- політика ресурсів Docker Compose працює лише для моніторингу;
- ліміт диска створює попередження, але не квоту файлової системи;
- автоматизації Cloudflare DNS немає;
- вебінтерфейс та основні посібники користувача й розробника доступні англійською,
  українською, російською та польською мовами.

## Документація

- [Посібник користувача](docs/uk/user-guide.md) — розгортання й керування
  проєктами через вебінтерфейс;
- [Посібник розробника](docs/uk/developer-guide.md) — архітектура, локальне
  середовище, розробка, тести, встановлення, релізи та діагностика;
- інші мови: [English](docs/en/user-guide.md),
  [Русский](docs/ru/user-guide.md), [Polski](docs/pl/user-guide.md).

Технічні довідники:

[Встановлення](docs/uk/installation.md) · [Архітектура](docs/uk/architecture.md) ·
[Безпека](docs/uk/security.md) · [Проєкти](docs/uk/projects.md) ·
[Бази даних](docs/uk/databases.md) · [Резервні копії](docs/uk/backups.md) ·
[Моніторинг](docs/uk/monitoring.md) · [Telegram](docs/uk/telegram.md) ·
[Minecraft Forge](docs/uk/minecraft-forge.md) ·
[Paper / Purpur / Spigot](docs/uk/minecraft-servers.md) ·
[Розробка](docs/uk/development.md) · [Випуск версій](docs/uk/releasing.md)

Перед запитом на злиття прочитайте [CONTRIBUTING.md](CONTRIBUTING.md). Вразливості
надсилайте приватно за правилами [SECURITY.md](SECURITY.md).

Ліцензія: [MIT](LICENSE).
