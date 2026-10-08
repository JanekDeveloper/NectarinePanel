# Paper, Purpur i Spigot

[English](../en/minecraft-servers.md) · [Українська](../uk/minecraft-servers.md) · [Русский](../ru/minecraft-servers.md) · **Polski**

Panel pozwala zainstalować i skonfigurować serwer Minecraft, dodać wtyczki oraz
zarządzać graczami. Dla serwerów z modami Forge skorzystaj z
[osobnego poradnika](minecraft-forge.md).

## Tworzenie i uruchamianie

1. Otwórz **Projekty → Nowy projekt** i wybierz szablon Paper, Purpur lub
   Spigot. Inny silnik wymaga osobnego projektu.
2. Otwórz stronę **Minecraft** utworzonego projektu. Ustaw pamięć i wolne porty.
   Włącz RCON do obsługi konsoli i zarządzania graczami; podaj własne hasło lub
   pozwól panelowi wygenerować je automatycznie.
3. Przeczytaj i zaakceptuj EULA Minecraft, a następnie zapisz ustawienia.
4. Wybierz wersję Minecraft i kompilację. Panel dobierze Javę automatycznie.
   Rozpocznij instalację i poczekaj na jej zakończenie; instalacja Spigot może
   potrwać dłużej.
5. Kliknij **Uruchom**. Połącz się z Minecraft, używając adresu VPS i portu gry,
   na przykład `play.example.com:25565`.

Jeśli masz już JAR serwera dla wybranego silnika, użyj **Importu JAR serwera**:
wybierz jego wersję Minecraft i prześlij plik.

## Ustawienia i konsola

Na stronie **Minecraft** możesz zmienić pamięć, porty i ustawienia serwera.
`Xms` to początkowy przydział pamięci, a `Xmx` — maksymalny. Zostaw część pamięci
VPS dla panelu i innych projektów.

Przed zmianami zatrzymaj serwer. Edytuj ustawienia gry w sekcji konfiguracji,
zapisz je i uruchom serwer. Do wysyłania poleceń i sprawdzania problemów służą
zakładki **Konsola** i **Logi**.

## Wtyczki i gracze

W sekcji **Wtyczki** prześlij JAR zgodnej wtyczki. Aby ją zastąpić, prześlij
nowy JAR o tej samej nazwie pliku. Wtyczki można włączać, wyłączać, zastępować
i usuwać tylko przy zatrzymanym serwerze. Usunięcie JAR zachowuje dane wtyczki.
Po zmianach uruchom serwer.

W sekcji **Gracze** podaj nazwę gracza i wybierz działanie: dodaj lub usuń
z whitelist, nadaj lub odbierz OP, zablokuj, odblokuj albo wyrzuć.
Serwer musi działać, a RCON musi być włączony.

## Aktualizacje i kopie zapasowe

Wybierz wersję i kompilację, a następnie kliknij **Aktualizuj z kopią**.
Panel utworzy pełną kopię zapasową przed zastąpieniem serwera. Poczekaj na
zakończenie operacji; w razie błędu sprawdź jej wynik i logi. Jeśli dostępny jest
przycisk **Ponów odzyskiwanie**, użyj go do ponownej próby przywrócenia.

Kopie tworzysz i przywracasz w zakładce **Kopie zapasowe**. Kopiowanie działającego
serwera wymaga RCON; w przeciwnym razie najpierw go zatrzymaj. Przywracanie
zastępuje bieżące dane kopią. Aby wrócić do starszej wersji Minecraft,
przywróć kopię z tej wersji. Ważne kopie przechowuj również poza VPS.

## Stan serwera

Strona **Minecraft** pokazuje dostępność, liczbę graczy, opis i wersję serwera.
Zużycie zasobów znajdziesz w przeglądzie projektu. Jeśli bot Telegram jest
połączony, możesz przez niego uruchamiać, zatrzymywać i ponownie uruchamiać
serwer, tworzyć kopie oraz otrzymywać powiadomienia.

Pozostałe funkcje opisuje [poradnik użytkownika](user-guide.md).
