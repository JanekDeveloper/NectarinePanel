# Paper, Purpur and Spigot

**English** · [Українська](../uk/minecraft-servers.md) · [Русский](../ru/minecraft-servers.md) · [Polski](../pl/minecraft-servers.md)

Use the panel to install and configure a Minecraft server, add plugins and
manage players. For servers running Forge mods, see the
[Forge guide](minecraft-forge.md).

## Create and start a server

1. Open **Projects → New project** and choose the Paper, Purpur or Spigot
   template. A different server engine requires a separate project.
2. Open the project's **Minecraft** page. Set the memory and available ports.
   Enable RCON for console commands and player management; enter a password or
   let the panel generate one.
3. Read and accept the Minecraft EULA, then save the settings.
4. Choose a Minecraft version and build. The panel selects Java automatically.
   Start the installation and wait for it to finish; Spigot installation may
   take longer.
5. Click **Start**. Connect from Minecraft using your VPS address and game
   port, such as `play.example.com:25565`.

If you already have a server JAR for the selected engine, use **Import server
JAR**: select its Minecraft version and upload the file.

## Settings and console

The **Minecraft** page lets you change memory, ports and server settings.
`Xms` is the initial memory allocation; `Xmx` is the maximum. Leave some VPS
memory for the panel and other projects.

Stop the server before making changes. Edit game settings in the configuration
section, save them and start the server. Use the **Console** and **Logs** tabs
to send commands and investigate problems.

## Plugins and players

Upload a compatible plugin JAR in **Plugins**. To replace it, upload a new JAR
with the same filename. Plugins can only be enabled, disabled, replaced or
deleted while the server is stopped. Deleting a plugin JAR preserves its data.
Start the server after making changes.

In **Players**, enter a player name and choose an action: add or remove from
the whitelist, grant or revoke operator privileges, ban, unban or kick.
The server must be running with RCON enabled.

## Updates and backups

Choose the version and build you want, then click **Update with backup**.
The panel creates a full backup before replacing the server. Wait for the
operation to finish; if it fails, check its result and logs. If **Retry
recovery** is available, use it to attempt the rollback again.

Create and restore copies in the **Backups** tab. Backing up a running server
requires RCON; otherwise, stop it first. Restoring replaces current data with
the backup. To return to an older Minecraft version, restore a backup from
that version. Keep important copies outside the VPS too.

## Server status

The **Minecraft** page shows availability, player count, description and
version. Resource usage is available in the project overview. If the
Telegram bot is connected, you can use it to start, stop and restart the
server, create backups and receive notifications.

See the [user guide](user-guide.md) for other panel features.
