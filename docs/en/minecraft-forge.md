# Minecraft Forge

**English** · [Українська](../uk/minecraft-forge.md) · [Русский](../ru/minecraft-forge.md) · [Polski](../pl/minecraft-forge.md)

Minecraft Forge is a dedicated project type. Its runtime directory is
`/srv/vps-panel/minecraft/{project_id}` and must run as an unprivileged user or
container.

The runtime configuration stores Java version, Xms/Xmx limits, server port,
EULA acceptance and optional RCON settings. The panel must not start a server
until the owner explicitly accepts the Minecraft EULA. Console commands use
process stdin or authenticated RCON, never a host shell.

The Minecraft page exposes:

- Java, memory, game port, server JAR and RCON configuration;
- installation and update from an official Forge installer JAR uploaded to the
  server root;
- `server.properties`, `whitelist.json` and `ops.json` editors;
- `mods/` JAR listing;
- start action through the system agent;
- logs and console through the shared runtime pages.

`whitelist.json` and `ops.json` writes are validated as JSON arrays before they
reach disk. Upload large JARs and world files through the project file manager
or SFTP.

Modern Forge installers are executed in an ephemeral, capability-free JDK
container. The generated `run.sh`/`user_jvm_args.txt` launcher is detected and
the selected Xms/Xmx limits remain panel-managed. Older installer layouts fall
back to their generated Forge server JAR.

Backups can include world data, mods, config, `server.properties`, whitelist and
operator files. When the managed server is running, the worker sends
`save-off`, then `save-all flush`, creates the archive, and restores writes with
`save-on`. RCON is therefore required for a live Minecraft backup. A stopped
server can be backed up without RCON.
