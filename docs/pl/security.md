# Bezpieczeństwo

[English](../en/security.md) · [Українська](../uk/security.md) · [Русский](../ru/security.md) · **Polski**

API, worker i System Agent są rozdzielone: API nie ma bezpośredniego pełnego dostępu do systemd, Dockera ani zewnętrznych katalogów.

- Używaj unikalnych haseł, HTTPS i ograniczonego dostępu do panelu.
- Role i członkostwa projektów powinny stosować zasadę najmniejszych uprawnień.
- Sekrety są szyfrowane i maskowane; ujawnienie wymaga potwierdzenia klucza i jest audytowane bez wartości.
- Nie przekazuj tokenów w URL, logach, opisach ani Git.
- Pliki z chronionych katalogów zewnętrznych przygotowuje Agent w zamkniętym obszarze tymczasowym.

Problemy bezpieczeństwa zgłaszaj według [SECURITY.md](../../SECURITY.md).

