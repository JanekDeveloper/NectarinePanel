# Architektura

[English](../en/architecture.md) · [Українська](../uk/architecture.md) · [Русский](../ru/architecture.md) · **Polski**

NectarinePanel składa się z klienta Nuxt, API FastAPI, PostgreSQL, Valkey i workera Celery. Nginx przyjmuje ruch TLS i kieruje żądania do klienta oraz API.

- API przechowuje projekty, użytkowników, role, audyt, konfigurację i zaszyfrowane sekrety.
- Worker realizuje wdrożenia, kopie zapasowe i zadania utrzymaniowe.
- System Agent jest jedynym komponentem z podwyższonymi uprawnieniami: zarządza systemd, Dockerem i zewnętrznymi katalogami plików.
- Wydania są przechowywane oddzielnie, a `current` wskazuje aktywne wydanie.

Role globalne i członkostwa projektu ograniczają dostęp. Polityki zasobów wymuszają CPU/RAM dla Dockera i systemd; dysk jest obecnie tylko monitorowany. Worker zbiera metryki i tworzy zduplikowane tylko raz powiadomienia o przekroczeniach limitów.

