"""Project template registry and filesystem materialization."""

from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

from app.models.entities import Project
from app.services.paths import runtime_root


@dataclass(frozen=True)
class ProjectTemplate:
    """Immutable project template preset."""

    id: str
    name: str
    description: str
    project_type: str
    runtime_type: str
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    healthcheck_url: str | None = None
    runtime_config: dict[str, Any] = field(default_factory=dict)
    suggested_env: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)

    def as_response(self) -> dict[str, Any]:
        """Return a JSON-serializable template descriptor."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "project_type": self.project_type,
            "runtime_type": self.runtime_type,
            "install_command": self.install_command,
            "build_command": self.build_command,
            "start_command": self.start_command,
            "output_directory": self.output_directory,
            "healthcheck_url": self.healthcheck_url,
            "runtime_config": self.runtime_config,
            "suggested_env": list(self.suggested_env),
        }


def _text(value: str) -> str:
    """Normalize indented template content."""
    return dedent(value).lstrip()


TEMPLATES: dict[str, ProjectTemplate] = {
    "fastapi": ProjectTemplate(
        id="fastapi",
        name="FastAPI app",
        description="Python API with uvicorn and a production Dockerfile.",
        project_type="backend",
        runtime_type="docker",
        start_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
        runtime_config={"internal_port": 8000},
        suggested_env=("DATABASE_URL", "SECRET_KEY"),
        files={
            "requirements.txt": "fastapi\nuvicorn[standard]\n",
            "app/main.py": _text(
                """
                from fastapi import FastAPI

                app = FastAPI()


                @app.get("/health")
                def health() -> dict[str, str]:
                    return {"status": "ok"}
                """
            ),
            "Dockerfile": _text(
                """
                FROM python:3.12-slim
                WORKDIR /app
                COPY requirements.txt .
                RUN pip install --no-cache-dir -r requirements.txt
                COPY . .
                CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
                """
            ),
        },
    ),
    "nuxt": ProjectTemplate(
        id="nuxt",
        name="Nuxt app",
        description="Nuxt frontend built and served through Docker.",
        project_type="web",
        runtime_type="docker",
        start_command="node .output/server/index.mjs",
        runtime_config={"internal_port": 3000},
        suggested_env=("NUXT_PUBLIC_API_BASE",),
        files={
            "package.json": _text(
                """
                {
                  "scripts": {
                    "build": "nuxt build",
                    "start": "node .output/server/index.mjs"
                  },
                  "dependencies": {
                    "nuxt": "^4.0.0",
                    "vue": "^3.5.0"
                  },
                  "devDependencies": {}
                }
                """
            ),
            "app.vue": _text(
                """
                <template>
                  <main><h1>Nectarine Nuxt app</h1></main>
                </template>
                """
            ),
            "Dockerfile": _text(
                """
                FROM node:22-slim AS build
                WORKDIR /app
                COPY package*.json ./
                RUN npm ci
                COPY . .
                RUN npm run build

                FROM node:22-slim
                WORKDIR /app
                COPY --from=build /app/.output ./.output
                CMD ["node", ".output/server/index.mjs"]
                """
            ),
        },
    ),
    "vue-vite": ProjectTemplate(
        id="vue-vite",
        name="Vue/Vite app",
        description="Static Vue build served by Nginx.",
        project_type="web",
        runtime_type="static",
        install_command="npm ci",
        build_command="npm run build",
        output_directory="dist",
        files={
            "package.json": _text(
                """
                {
                  "scripts": {
                    "build": "vite --host 0.0.0.0"
                  },
                  "dependencies": {
                    "@vitejs/plugin-vue": "latest",
                    "vite": "latest",
                    "vue": "latest"
                  },
                  "devDependencies": {}
                }
                """
            ),
            "index.html": (
                '<div id="app"></div><script type="module" src="/src/main.js"></script>\n'
            ),
            "src/main.js": _text(
                """
                import { createApp } from 'vue'

                createApp({ template: '<h1>Nectarine Vue app</h1>' }).mount('#app')
                """
            ),
            "Dockerfile": _text(
                """
                FROM node:22-slim AS build
                WORKDIR /app
                COPY package*.json ./
                RUN npm ci
                COPY . .
                RUN npm run build
                """
            ),
        },
    ),
    "react-vite": ProjectTemplate(
        id="react-vite",
        name="React/Vite app",
        description="Static React build served by Nginx.",
        project_type="web",
        runtime_type="static",
        install_command="npm ci",
        build_command="npm run build",
        output_directory="dist",
        files={
            "package.json": _text(
                """
                {
                  "scripts": {
                    "build": "vite --host 0.0.0.0"
                  },
                  "dependencies": {
                    "@vitejs/plugin-react": "latest",
                    "vite": "latest",
                    "react": "latest",
                    "react-dom": "latest"
                  },
                  "devDependencies": {}
                }
                """
            ),
            "index.html": (
                '<div id="root"></div><script type="module" src="/src/main.jsx"></script>\n'
            ),
            "src/main.jsx": _text(
                """
                import React from 'react'
                import { createRoot } from 'react-dom/client'

                createRoot(document.getElementById('root')).render(<h1>Nectarine React app</h1>)
                """
            ),
        },
    ),
    "next": ProjectTemplate(
        id="next",
        name="Next app",
        description="Next.js app with standalone Docker runtime.",
        project_type="web",
        runtime_type="docker",
        start_command="npm run start",
        runtime_config={"internal_port": 3000},
        files={
            "package.json": _text(
                """
                {
                  "scripts": {
                    "build": "next build",
                    "start": "next start -H 0.0.0.0"
                  },
                  "dependencies": {
                    "next": "latest",
                    "react": "latest",
                    "react-dom": "latest"
                  },
                  "devDependencies": {}
                }
                """
            ),
            "app/page.tsx": _text(
                """
                export default function Page() {
                  return <main><h1>Nectarine Next app</h1></main>
                }
                """
            ),
            "Dockerfile": _text(
                """
                FROM node:22-slim
                WORKDIR /app
                COPY package*.json ./
                RUN npm ci
                COPY . .
                RUN npm run build
                CMD ["npm", "run", "start"]
                """
            ),
        },
    ),
    "node-api": ProjectTemplate(
        id="node-api",
        name="Node.js API",
        description="Node API container with configurable start command.",
        project_type="backend",
        runtime_type="docker",
        start_command="npm run start",
        runtime_config={"internal_port": 8000},
        suggested_env=("PORT", "DATABASE_URL"),
        files={
            "package.json": _text(
                """
                {
                  "scripts": {
                    "start": "node server.js"
                  },
                  "dependencies": {
                    "fastify": "latest"
                  },
                  "devDependencies": {}
                }
                """
            ),
            "server.js": _text(
                """
                import Fastify from 'fastify'

                const app = Fastify()
                app.get('/health', async () => ({ status: 'ok' }))
                await app.listen({ host: '0.0.0.0', port: Number(process.env.PORT || 8000) })
                """
            ),
            "Dockerfile": _text(
                """
                FROM node:22-slim
                WORKDIR /app
                COPY package*.json ./
                RUN npm ci --omit=dev
                COPY . .
                CMD ["npm", "run", "start"]
                """
            ),
        },
    ),
    "telegram-bot-python": ProjectTemplate(
        id="telegram-bot-python",
        name="Telegram bot Python",
        description="aiogram bot container.",
        project_type="bot",
        runtime_type="docker",
        start_command="python -m bot",
        runtime_config={
            "internal_port": 8000,
            "persistent_mounts": [
                {"source": "data", "target": "/app/data", "read_only": False}
            ],
        },
        suggested_env=("TELEGRAM_BOT_TOKEN",),
        files={
            "requirements.txt": "aiogram>=3,<4\n",
            "bot/__main__.py": _text(
                """
                import asyncio
                import os

                from aiogram import Bot, Dispatcher


                async def main() -> None:
                    token = os.environ["TELEGRAM_BOT_TOKEN"]
                    await Dispatcher().start_polling(Bot(token))


                asyncio.run(main())
                """
            ),
            "Dockerfile": _text(
                """
                FROM python:3.12-slim
                WORKDIR /app
                COPY requirements.txt .
                RUN pip install --no-cache-dir -r requirements.txt
                COPY . .
                CMD ["python", "-m", "bot"]
                """
            ),
        },
    ),
    "discord-bot-python": ProjectTemplate(
        id="discord-bot-python",
        name="Discord bot Python",
        description="Python Discord bot container.",
        project_type="bot",
        runtime_type="docker",
        start_command="python -m bot",
        runtime_config={
            "internal_port": 8000,
            "persistent_mounts": [
                {"source": "data", "target": "/app/data", "read_only": False}
            ],
        },
        suggested_env=("DISCORD_BOT_TOKEN",),
        files={
            "requirements.txt": "discord.py>=2,<3\n",
            "bot/__main__.py": _text(
                """
                import os

                import discord

                client = discord.Client(intents=discord.Intents.default())
                client.run(os.environ["DISCORD_BOT_TOKEN"])
                """
            ),
            "Dockerfile": _text(
                """
                FROM python:3.12-slim
                WORKDIR /app
                COPY requirements.txt .
                RUN pip install --no-cache-dir -r requirements.txt
                COPY . .
                CMD ["python", "-m", "bot"]
                """
            ),
        },
    ),
    "static-site": ProjectTemplate(
        id="static-site",
        name="Static site",
        description="Plain static files served through Nginx.",
        project_type="static",
        runtime_type="static",
        output_directory="public",
        files={
            "public/index.html": _text(
                """
                <!doctype html>
                <html lang="ru">
                  <head><meta charset="utf-8"><title>Nectarine site</title></head>
                  <body><h1>It works</h1></body>
                </html>
                """
            )
        },
    ),
    "docker-compose": ProjectTemplate(
        id="docker-compose",
        name="Docker Compose app",
        description="Existing compose stack managed by the panel.",
        project_type="docker",
        runtime_type="docker_compose",
        files={
            "docker-compose.yml": _text(
                """
                services:
                  app:
                    image: nginx:alpine
                    ports:
                      - "8080:80"
                """
            )
        },
    ),
    "minecraft-forge": ProjectTemplate(
        id="minecraft-forge",
        name="Minecraft Forge server",
        description="Forge server runtime with EULA and memory settings.",
        project_type="minecraft_forge",
        runtime_type="minecraft_forge",
        runtime_config={"java_version": 21, "xms": "1G", "xmx": "2G", "port": 25565},
        suggested_env=("RCON_PASSWORD",),
        files={"eula.txt": "eula=false\n"},
    ),
}


for _engine in ("paper", "purpur", "spigot"):
    TEMPLATES[f"minecraft-{_engine}"] = ProjectTemplate(
        id=f"minecraft-{_engine}",
        name=f"Minecraft {_engine.title()} server",
        description="Plugin server with official installation, backups and RCON.",
        project_type=f"minecraft_{_engine}",
        runtime_type=f"minecraft_{_engine}",
        runtime_config={
            "java_version": 25,
            "xms": "1G",
            "xmx": "2G",
            "game_port": 25565,
            "server_jar": "server.jar",
            "eula_accepted": False,
            "rcon_enabled": True,
        },
        files={"eula.txt": "eula=false\n"},
    )


def list_project_templates() -> list[ProjectTemplate]:
    """Return all built-in templates in UI order."""
    return list(TEMPLATES.values())


def get_project_template(template_id: str) -> ProjectTemplate | None:
    """Return one built-in template by stable identifier."""
    return TEMPLATES.get(template_id)


def materialize_project_template(
    storage_root: Path,
    project: Project,
    template: ProjectTemplate,
) -> None:
    """Create non-overwriting starter files for a newly created project."""
    root = runtime_root(storage_root, project)
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in template.files.items():
        target = (root / relative_path).resolve()
        if root.resolve() not in target.parents:
            raise ValueError("Template path escapes project root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(content, encoding="utf-8")
