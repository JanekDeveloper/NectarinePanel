"""Allowlisted owner command handlers."""

import logging
import tempfile
from pathlib import Path

import httpx
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from nectarine_bot.api import PanelApi
from nectarine_bot.config import settings

router = Router()
logger = logging.getLogger(__name__)


class RestoreUpload(StatesGroup):
    """Telegram document upload state for destructive restore flows."""

    waiting_for_artifact = State()


def is_owner(user_id: int | None) -> bool:
    """Return whether a Telegram user is the configured owner."""
    return user_id is not None and settings.telegram_owner_id == user_id


def _username(message_or_callback: Message | CallbackQuery) -> str | None:
    """Return a stable optional Telegram username for audit context."""
    user = message_or_callback.from_user
    return user.username if user else None


def _callback_message(callback: CallbackQuery) -> Message | None:
    """Return an editable callback message, excluding inaccessible messages."""
    return callback.message if isinstance(callback.message, Message) else None


async def _answer_callback(
    callback: CallbackQuery,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    """Answer a callback without failing processing for an expired Telegram query."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        logger.warning("Telegram callback query expired before it could be answered")


def confirmation_keyboard(project_id: str, action: str) -> InlineKeyboardMarkup:
    """Build an explicit destructive action confirmation keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"confirm:{project_id}:{action}",
                ),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


@router.message(CommandStart())
async def start(message: Message, api: PanelApi) -> None:
    """Describe bot access for the configured owner."""
    user = message.from_user
    if user is None or not is_owner(user.id):
        await message.answer("Доступ запрещён.")
        return
    await api.bind_owner(user.id, _username(message))
    await message.answer("NectarinePanel подключён. Доступны /projects и /status.")


@router.message(Command("projects"))
async def projects(message: Message, api: PanelApi) -> None:
    """List projects and their current states."""
    if not is_owner(message.from_user.id if message.from_user else None):
        return
    items = await api.projects()
    if not items:
        await message.answer("Проектов нет.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{item['name']} ({item['status']})",
                    callback_data=f"project:{item['id']}",
                )
            ]
            for item in items[:30]
        ]
    )
    await message.answer("Выберите проект:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("project:"))
async def select_project(callback: CallbackQuery) -> None:
    """Show confirmed lifecycle actions for one project."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    project_id = (callback.data or "").split(":", maxsplit=1)[1]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"confirm:{project_id}:{action}",
                )
                for label, action in [
                    ("Start", "start"),
                    ("Stop", "stop"),
                    ("Restart", "restart"),
                ]
            ],
            [
                InlineKeyboardButton(
                    text="Создать backup",
                    callback_data=f"confirm:{project_id}:backup",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Загрузить backup и восстановить",
                    callback_data=f"uploadrestore:projects:{project_id}",
                )
            ],
        ]
    )
    await callback.answer()
    message = _callback_message(callback)
    if message:
        await message.edit_text(
            "Выберите действие. Выполнение потребует подтверждённого нажатия.",
            reply_markup=keyboard,
        )


@router.message(Command("databases"))
async def databases(message: Message, api: PanelApi) -> None:
    """List databases with export actions."""
    if not is_owner(message.from_user.id if message.from_user else None):
        return
    items = await api.databases()
    if not items:
        await message.answer("Баз данных нет.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Export {item['name']} ({item['engine']})",
                    callback_data=f"dbexport:{item['id']}",
                ),
                InlineKeyboardButton(
                    text="Restore dump",
                    callback_data=f"uploadrestore:databases:{item['id']}",
                ),
            ]
            for item in items[:30]
        ]
    )
    await message.answer("Экспорт базы данных:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("dbexport:"))
async def export_database(callback: CallbackQuery, api: PanelApi) -> None:
    """Queue a selected database export."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    database_id = (callback.data or "").split(":", maxsplit=1)[1]
    result = await api.request_export("databases", database_id)
    await callback.answer("Экспорт поставлен в очередь.")
    message = _callback_message(callback)
    if message:
        await message.edit_text(
            f"Job {result['job_id']} создан. Готовый dump появится в /backups."
        )


@router.message(Command("backups"))
async def backups(message: Message, api: PanelApi) -> None:
    """List ready backup artifacts."""
    if not is_owner(message.from_user.id if message.from_user else None):
        return
    items = await api.backups()
    if not items:
        await message.answer("Готовых бэкапов нет.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Скачать {item['type']} ({item['size_bytes'] // 1024 // 1024} МБ)",
                    callback_data=f"backup:{item['id']}",
                ),
                InlineKeyboardButton(
                    text="Restore",
                    callback_data=f"restorebackup:{item['id']}",
                ),
            ]
            for item in items[:30]
        ]
    )
    await message.answer("Выберите backup:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("backup:"))
async def send_backup(callback: CallbackQuery, api: PanelApi) -> None:
    """Send a small backup or return an expiring link."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    backup_id = (callback.data or "").split(":", maxsplit=1)[1]
    metadata = await api.backup_metadata(backup_id)
    message = _callback_message(callback)
    if int(metadata["size_bytes"] or 0) <= settings.telegram_max_file_bytes:
        filename, content = await api.backup_artifact(backup_id)
        if message:
            await message.answer_document(BufferedInputFile(content, filename=filename))
    else:
        link = await api.backup_download_link(backup_id)
        if message:
            await message.answer(
                f"Файл превышает лимит Telegram. Одноразовая ссылка: {link['url']}"
            )
    await callback.answer()


@router.message(Command("status"))
async def status(message: Message) -> None:
    """Confirm that the bot process is responsive."""
    if is_owner(message.from_user.id if message.from_user else None):
        await message.answer("Бот работает.")


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_action(callback: CallbackQuery, api: PanelApi) -> None:
    """Execute a previously presented owner confirmation."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    _, project_id, action = (callback.data or "").split(":", maxsplit=2)
    if action not in {"start", "stop", "restart", "backup"}:
        await callback.answer("Недопустимое действие.", show_alert=True)
        return
    result = await api.project_action(project_id, action)
    await callback.answer("Задача поставлена в очередь.")
    message = _callback_message(callback)
    if message:
        await message.edit_text(f"Задача {result['job_id']} создана.")


@router.callback_query(F.data.startswith("uploadrestore:"))
async def request_restore_upload(callback: CallbackQuery, state: FSMContext) -> None:
    """Ask the owner to upload a dump or backup artifact for restore."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    _, resource, resource_id = (callback.data or "").split(":", maxsplit=2)
    if resource not in {"databases", "projects"}:
        await callback.answer("Недопустимый тип restore.", show_alert=True)
        return
    await state.set_state(RestoreUpload.waiting_for_artifact)
    await state.update_data(resource=resource, resource_id=resource_id)
    await callback.answer()
    message = _callback_message(callback)
    if message:
        suffix = ".dump для PostgreSQL, .sql для MySQL/MariaDB или .sqlite3 для SQLite"
        if resource == "projects":
            suffix = ".tar.gz или .tar.gz.enc"
        await message.edit_text(f"Отправьте файл {suffix}. Restore перезапишет текущие данные.")


@router.message(RestoreUpload.waiting_for_artifact, F.document)
async def receive_restore_upload(
    message: Message,
    state: FSMContext,
    bot: Bot,
    api: PanelApi,
) -> None:
    """Receive a restore artifact and forward it to the internal panel API."""
    user = message.from_user
    if user is None or not is_owner(user.id):
        await state.clear()
        return
    document = message.document
    if document is None or not document.file_name:
        await message.answer("Файл не найден.")
        return
    data = await state.get_data()
    resource = str(data.get("resource", ""))
    resource_id = str(data.get("resource_id", ""))
    if resource not in {"databases", "projects"} or not resource_id:
        await state.clear()
        await message.answer("Restore context устарел. Повторите команду.")
        return
    with tempfile.TemporaryDirectory(prefix="nectarine-tg-restore-") as directory:
        target = Path(directory) / Path(document.file_name).name
        await bot.download(document, destination=target)
        result = await api.upload_restore(
            resource,
            resource_id,
            target,
            user.id,
            _username(message),
        )
    await state.clear()
    await message.answer(f"Restore job {result['job_id']} создан.")


@router.callback_query(F.data.startswith("restorebackup:"))
async def restore_existing_backup(callback: CallbackQuery, api: PanelApi) -> None:
    """Queue restore of an existing project backup after inline confirmation."""
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    backup_id = (callback.data or "").split(":", maxsplit=1)[1]
    result = await api.restore_backup(backup_id, callback.from_user.id, _username(callback))
    await callback.answer("Restore поставлен в очередь.")
    message = _callback_message(callback)
    if message:
        await message.edit_text(f"Restore job {result['job_id']} создан.")


@router.callback_query(F.data.startswith("2fa:"))
async def approve_two_factor(callback: CallbackQuery, api: PanelApi) -> None:
    """Approve a pending panel login from the owner Telegram account."""
    if not is_owner(callback.from_user.id):
        await _answer_callback(callback, "Доступ запрещён.", show_alert=True)
        return
    token = (callback.data or "").split(":", maxsplit=1)[1]
    try:
        await api.approve_2fa(token, callback.from_user.id, _username(callback))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 410}:
            text = "Запрос на вход истёк. Повторите вход в панели."
        else:
            text = "Не удалось подтвердить вход. Повторите попытку."
        await _answer_callback(callback, text, show_alert=True)
        return
    except httpx.HTTPError:
        await _answer_callback(
            callback,
            "Панель временно недоступна. Повторите попытку.",
            show_alert=True,
        )
        return
    message = _callback_message(callback)
    if message:
        try:
            await message.edit_text("Вход в NectarinePanel разрешён.")
        except TelegramBadRequest:
            logger.warning("Telegram login approval message could not be updated")
    await _answer_callback(callback, "Вход разрешён.")


@router.callback_query(F.data == "2fa-cancel")
async def cancel_two_factor(callback: CallbackQuery) -> None:
    """Acknowledge a declined Telegram two-factor request."""
    if is_owner(callback.from_user.id):
        message = _callback_message(callback)
        if message:
            try:
                await message.edit_text("Вход в NectarinePanel отклонён.")
            except TelegramBadRequest:
                logger.warning("Telegram login rejection message could not be updated")
        await _answer_callback(callback, "Отклонено.")


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery) -> None:
    """Cancel a pending inline confirmation."""
    if is_owner(callback.from_user.id):
        await callback.answer("Отменено.")
        message = _callback_message(callback)
        if message:
            await message.edit_text("Действие отменено.")
